# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""API ekranów galwork-pm dla integracji Taiga - galwork-pm - ERPNext (Etap 4).

`/v1/pm/api/taiga/...`, wymaga tokenu Keycloak. Zakres wglądu (RODO_PM.md,
macierz dostępu):
- administrator (grupa z `[keycloak] admin_groups`, opcjonalnie też
  `[taiga] admin_groups`) - wszystkie projekty, konfiguracja, osoby, alerty,
- PM projektu (grupa `triage_group` powiązania) - jego projekty: tydzień,
  słownik, nieprzypisane, zatwierdzanie tygodni, różnice z ERPNext,
- pracownik - tylko „Mój tydzień” (osoba rozpoznana po e-mailu w ERPNext:
  `Employee.user_id` albo `company_email`).
E-maile członków Taigi widzi tylko administrator.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.dependencies import get_db
from connector.base import ConnectorError
from pm.integrations.taiga import activity, erp_sync, ledger, periods, service
from pm.models.taiga import PersonLink, TaigaProjectLink
from pm.models.taiga_ledger import ActivityMap, ActivityMapScope, TaigaItem, WorkLedger
from wf.dependencies import get_current_wf_user, is_process_admin
from wf.schemas.wf_schemas import CurrentUser

router = APIRouter(prefix="/api/taiga", tags=["integracja Taiga - rozliczenia"])
ZERO = Decimal("0")


# --- zależności i uprawnienia ---------------------------------------------------------

def get_taiga():
    from connector.taiga import get_connector
    return get_connector()


def get_erp():
    from connector.erpnext.erpnext import get_connector
    return get_connector()


def _leaf(value: str) -> str:
    return value.strip().lstrip("/").split("/")[-1].lower()


def _principals(user: CurrentUser) -> set[str]:
    return {_leaf(v) for v in (user.groups | user.roles) if v}


def is_admin(user: CurrentUser, taiga=None) -> bool:
    if is_process_admin(user):
        return True
    extra = (taiga.config.get("admin_groups") if taiga is not None else None) or ""
    return bool({_leaf(g) for g in extra.split(",") if g.strip()} & _principals(user))


def managed_projects(db: Session, user: CurrentUser, taiga=None) -> set[int]:
    """Projekty Taigi, które użytkownik może przeglądać jako PM (administrator - wszystkie)."""
    links = db.execute(select(TaigaProjectLink)).scalars().all()
    if is_admin(user, taiga):
        return {link.taiga_project_id for link in links}
    mine = _principals(user)
    return {link.taiga_project_id for link in links if link.triage_group and _leaf(link.triage_group) in mine}


def _require_admin(user: CurrentUser, taiga) -> None:
    if not is_admin(user, taiga):
        raise HTTPException(status_code=403, detail="Wymagane uprawnienia administratora integracji")


def _require_project(db: Session, user: CurrentUser, taiga, pid: int) -> TaigaProjectLink:
    link = service.get_link(db, pid)
    if link is None:
        raise HTTPException(status_code=404, detail="Projekt Taigi nie jest objęty integracją")
    if pid not in managed_projects(db, user, taiga):
        raise HTTPException(status_code=403, detail="Brak dostępu do projektu (wymagana grupa PM projektu)")
    return link


def _employee_of(erp, user: CurrentUser) -> str | None:
    if not user.email:
        return None
    email = user.email.strip().lower()
    for field in ("user_id", "company_email"):
        rows = erp.get_list("Employee", filters=[[field, "=", email]], fields=["name"], limit=2)
        if rows:
            return rows[0]["name"]
    return None


def _members(taiga, pid: int) -> dict[int, dict]:
    try:
        return {m["user"]: m for m in taiga.list_memberships(pid) if m.get("user") is not None}
    except ConnectorError:
        return {}


def _hours(value) -> float:
    return float(value or 0)


# --- kim jestem ------------------------------------------------------------------------

@router.get("/me")
def me(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_wf_user),
       taiga=Depends(get_taiga), erp=Depends(get_erp)):
    try:
        employee = _employee_of(erp, user)
    except ConnectorError:
        employee = None
    return {"username": user.username, "display_name": user.display_name, "is_admin": is_admin(user, taiga),
            "projects": sorted(managed_projects(db, user, taiga)), "erp_employee": employee}


# --- projekty i konfiguracja ----------------------------------------------------------------

def _link_out(link: TaigaProjectLink) -> dict:
    return {"taiga_project_id": link.taiga_project_id, "taiga_slug": link.taiga_slug,
            "erp_project": link.erp_project, "default_activity_type": link.default_activity_type,
            "billable": link.billable, "triage_group": link.triage_group,
            "billing_from": link.billing_from, "active": link.active,
            "hours_attribute_name": link.hours_attribute_name,
            "baseline_at": link.baseline_at, "last_reconcile_at": link.last_reconcile_at,
            "timeline_cursor": link.timeline_cursor, "pm_project_id": link.pm_project_id}


@router.get("/projects")
def list_projects(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_wf_user),
                  taiga=Depends(get_taiga)):
    allowed = managed_projects(db, user, taiga)
    links = db.execute(select(TaigaProjectLink).order_by(TaigaProjectLink.taiga_project_id)).scalars()
    return [_link_out(link) for link in links if link.taiga_project_id in allowed]


class ProjectUpdate(BaseModel):
    erp_project: str | None = None
    default_activity_type: str | None = None
    billable: bool | None = None
    triage_group: str | None = None
    billing_from: date | None = None
    active: bool | None = None
    # 2026--11-21: powiązanie z projektem galwork-pm (budżet WBS w analizie)
    pm_project_id: int | None = None


@router.patch("/projects/{pid}")
def update_project(pid: int, payload: ProjectUpdate, db: Session = Depends(get_db),
                   user: CurrentUser = Depends(get_current_wf_user), taiga=Depends(get_taiga)):
    _require_admin(user, taiga)
    link = service.get_link(db, pid)
    if link is None:
        raise HTTPException(status_code=404, detail="Projekt Taigi nie jest objęty integracją")
    changes = payload.model_dump(exclude_unset=True)
    if changes.get("pm_project_id") is not None:
        from pm.models.project import Project
        if db.get(Project, changes["pm_project_id"]) is None:
            raise HTTPException(status_code=422, detail="Nie ma projektu galwork-pm o tym id")
    for key, value in changes.items():
        setattr(link, key, value or None if key in ("erp_project", "default_activity_type", "triage_group")
                else value)
    db.commit()
    if {"erp_project", "default_activity_type"} & set(changes) and link.baseline_at is not None:
        # 2026--11-21: zmiana domyślnego projektu / rodzaju pracy przenosi godziny w dzienniku
        ledger.recompute(ledger.LedgerContext(db, taiga), pid)
    return _link_out(link)


# --- osoby ------------------------------------------------------------------------------

@router.get("/projects/{pid}/persons")
def project_persons(pid: int, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_wf_user),
                    taiga=Depends(get_taiga), erp=Depends(get_erp)):
    _require_project(db, user, taiga, pid)
    admin = is_admin(user, taiga)
    links = defaultdict(list)
    for person in db.execute(select(PersonLink)).scalars():
        links[person.taiga_user_id].append(person)
    try:
        names = {e["name"]: e.get("employee_name") for e in erp.get_list(
            "Employee", fields=["name", "employee_name"], limit=1000)}
    except ConnectorError:
        names = {}
    technical = getattr(taiga, "technical_user_id", None)
    result = []
    for uid, member in sorted(_members(taiga, pid).items()):
        if uid == technical:
            continue
        result.append({
            "taiga_user_id": uid, "full_name": member.get("full_name"), "role": member.get("role_name"),
            "email": member.get("user_email") if admin else None,
            "links": [{"erp_employee": p.erp_employee, "employee_name": names.get(p.erp_employee),
                       "confirmed": p.confirmed, "source": p.source} for p in links.get(uid, [])],
        })
    return result


class PersonConfirm(BaseModel):
    taiga_user_id: int
    erp_employee: str


@router.post("/persons/confirm")
def confirm_person(payload: PersonConfirm, db: Session = Depends(get_db),
                   user: CurrentUser = Depends(get_current_wf_user), taiga=Depends(get_taiga)):
    _require_admin(user, taiga)
    plans = ledger.confirm_person(ledger.LedgerContext(db, taiga), payload.taiga_user_id, payload.erp_employee)
    return {"moved_items": len(plans)}


@router.post("/projects/{pid}/persons/propose")
def propose_persons(pid: int, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_wf_user),
                    taiga=Depends(get_taiga), erp=Depends(get_erp)):
    _require_admin(user, taiga)
    proposals = service.propose_person_links(db, taiga, erp, taiga_project_id=pid, apply=True)
    return [{"taiga_user_id": p.taiga_user_id, "full_name": p.full_name, "erp_employee": p.erp_employee,
             "status": p.status} for p in proposals]


# --- słownik rodzajów prac ------------------------------------------------------------------

@router.get("/activity-types")
def activity_types(user: CurrentUser = Depends(get_current_wf_user), taiga=Depends(get_taiga),
                   erp=Depends(get_erp)):
    return activity.allowed_activity_types(erp, taiga.config.get("activity_types"))


def _map_out(row: ActivityMap) -> dict:
    return {"id": row.id, "scope": row.scope, "taiga_object_id": row.taiga_object_id,
            "activity_type": row.activity_type, "erp_project": row.erp_project, "source": row.source,
            "valid_from": row.valid_from, "created_by": row.created_by}


@router.get("/projects/{pid}/tree")
def project_tree(pid: int, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_wf_user),
                 taiga=Depends(get_taiga)):
    """Drzewo epika -> US -> zadanie z godzinami, rozstrzygniętym rodzajem pracy i przypisaniami słownika."""
    link = _require_project(db, user, taiga, pid)
    ctx = ledger.LedgerContext(db, taiga)
    index = ctx.index(pid)
    items = db.execute(select(TaigaItem).where(TaigaItem.taiga_project_id == pid,
                                               TaigaItem.deleted.is_(False))).scalars().all()
    us_items = {i.taiga_id: i for i in items if i.kind == "userstory"}

    def node(item: TaigaItem) -> dict:
        res = activity.resolve(item, link, index, us_items.get(item.user_story_id) if item.kind == "task" else None)
        entry = index.get(item.kind, item.taiga_id)
        return {"kind": item.kind, "taiga_id": item.taiga_id, "ref": item.ref, "subject": item.subject,
                "hours": _hours(item.hours_total - item.baseline_hours), "assigned_to": item.assigned_to,
                "activity_type": res.activity_type, "activity_source": res.activity_source,
                "erp_project": res.erp_project, "project_source": res.project_source,
                "mapping": {"activity_type": entry.activity_type, "erp_project": entry.erp_project} if entry else None}

    tasks_by_us = defaultdict(list)
    loose = []
    for item in items:
        if item.kind == "task":
            (tasks_by_us[item.user_story_id] if item.user_story_id in us_items else loose).append(node(item))
    epic_names = {}
    try:
        epic_names = {e["id"]: {"ref": e.get("ref"), "subject": e.get("subject")} for e in taiga.list_epics(pid)}
    except (ConnectorError, AttributeError):
        pass
    epics: dict[int | None, list] = defaultdict(list)
    for us in sorted(us_items.values(), key=lambda i: i.ref or i.taiga_id):
        entry = {**node(us), "tasks": sorted(tasks_by_us.get(us.taiga_id, []), key=lambda t: t["ref"] or 0)}
        epics[min(us.epic_ids) if us.epic_ids else None].append(entry)
    epic_list = []
    for epic_id in sorted(epics, key=lambda e: (e is None, e or 0)):
        mapping = index.get(ActivityMapScope.EPIC, epic_id) if epic_id else None
        epic_list.append({"taiga_id": epic_id, **(epic_names.get(epic_id) or {"ref": None, "subject": None}),
                          "mapping": {"activity_type": mapping.activity_type, "erp_project": mapping.erp_project}
                          if mapping else None, "userstories": epics[epic_id]})
    roles = []
    try:
        for role in taiga.get_project(pid).get("roles") or []:
            mapping = index.get(ActivityMapScope.ROLE, role["id"])
            roles.append({"id": role["id"], "name": role.get("name"),
                          "activity_type": mapping.activity_type if mapping else None})
    except (ConnectorError, AttributeError):
        pass
    return {"project": _link_out(link), "epics": epic_list, "loose_tasks": loose, "roles": roles}


class MappingChange(BaseModel):
    scope: str
    taiga_object_id: int
    activity_type: str | None = None
    erp_project: str | None = None


def _plans_out(plans) -> dict:
    moved = []
    weeks: dict[str, float] = defaultdict(float)
    for plan in plans:
        moved.append({"kind": plan.item.kind, "taiga_id": plan.item.taiga_id, "ref": plan.item.ref,
                      "subject": plan.item.subject,
                      "entries": [{"week": e.work_week, "origin_week": e.origin_week, "hours": _hours(e.hours_delta),
                                   "activity_type": e.key.activity_type, "erp_project": e.key.erp_project,
                                   "erp_employee": e.key.erp_employee, "reason": e.reason} for e in plan.entries]})
        for e in plan.entries:
            if e.hours_delta > ZERO:
                weeks[e.work_week] += _hours(e.hours_delta)
    return {"items": moved, "hours_by_week": dict(sorted(weeks.items()))}


@router.post("/projects/{pid}/map/preview")
def preview_mapping(pid: int, payload: MappingChange, db: Session = Depends(get_db),
                    user: CurrentUser = Depends(get_current_wf_user), taiga=Depends(get_taiga)):
    _require_project(db, user, taiga, pid)
    if payload.scope not in ActivityMapScope.ALL:
        raise HTTPException(status_code=422, detail=f"Nieznany zakres {payload.scope!r}")
    plans = ledger.preview_mapping(ledger.LedgerContext(db, taiga), taiga_project_id=pid, scope=payload.scope,
                                   taiga_object_id=payload.taiga_object_id, activity_type=payload.activity_type,
                                   erp_project=payload.erp_project)
    db.rollback()
    return _plans_out(plans)


@router.post("/projects/{pid}/map")
def apply_mapping(pid: int, payload: MappingChange, db: Session = Depends(get_db),
                  user: CurrentUser = Depends(get_current_wf_user), taiga=Depends(get_taiga), erp=Depends(get_erp)):
    _require_project(db, user, taiga, pid)
    try:
        plans = ledger.apply_mapping(
            ledger.LedgerContext(db, taiga), taiga_project_id=pid, scope=payload.scope,
            taiga_object_id=payload.taiga_object_id, activity_type=payload.activity_type,
            erp_project=payload.erp_project, created_by=user.username,
            allowed_activity_types=lambda: activity.allowed_activity_types(erp, taiga.config.get("activity_types")))
    except activity.ActivityMapError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return _plans_out(plans)


@router.get("/projects/{pid}/map")
def list_mapping(pid: int, history: bool = False, db: Session = Depends(get_db),
                 user: CurrentUser = Depends(get_current_wf_user), taiga=Depends(get_taiga)):
    _require_project(db, user, taiga, pid)
    query = select(ActivityMap).where(ActivityMap.taiga_project_id == pid)
    if not history:
        query = query.where(ActivityMap.valid_to.is_(None))
    return [_map_out(r) | {"valid_to": r.valid_to}
            for r in db.execute(query.order_by(ActivityMap.scope, ActivityMap.taiga_object_id,
                                               ActivityMap.valid_from)).scalars()]


# --- nieprzypisane, tydzień, mój tydzień -----------------------------------------------------

@router.get("/projects/{pid}/unassigned")
def unassigned(pid: int, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_wf_user),
               taiga=Depends(get_taiga)):
    """Pozycje z godzinami bez rodzaju pracy - do triażu, pogrupowane po US."""
    _require_project(db, user, taiga, pid)
    items = db.execute(select(TaigaItem).where(TaigaItem.taiga_project_id == pid, TaigaItem.deleted.is_(False),
                                               TaigaItem.activity_type_resolved.is_(None))).scalars().all()
    us_items = {i.taiga_id: i for i in db.execute(select(TaigaItem).where(
        TaigaItem.taiga_project_id == pid, TaigaItem.kind == "userstory")).scalars()}
    groups: dict[int | None, dict] = {}
    for item in items:
        hours = item.hours_total - item.baseline_hours
        if hours == ZERO:
            continue
        us_id = item.taiga_id if item.kind == "userstory" else item.user_story_id
        us = us_items.get(us_id)
        group = groups.setdefault(us_id, {"user_story_id": us_id, "ref": us.ref if us else None,
                                          "subject": us.subject if us else None,
                                          "epic_ids": us.epic_ids if us else None, "hours": 0.0, "items": []})
        group["hours"] += _hours(hours)
        group["items"].append({"kind": item.kind, "taiga_id": item.taiga_id, "ref": item.ref,
                               "subject": item.subject, "hours": _hours(hours), "assigned_to": item.assigned_to})
    return sorted(groups.values(), key=lambda g: -g["hours"])


def _summary_out(db: Session, pid: int, week_from: str | None, week_to: str | None, members: dict) -> dict:
    rows = [r for r in ledger.week_summary(db, pid)
            if (not week_from or r.week >= week_from) and (not week_to or r.week <= week_to)]
    weeks = sorted({r.week for r in rows})
    states = ledger.week_states(db, pid, weeks)
    return {"weeks": [{"week": w, "state": states[w], "month": periods.week_month(w)} for w in weeks],
            "rows": [{"week": r.week, "erp_employee": r.erp_employee, "taiga_user_id": r.taiga_user_id,
                      "person": (members.get(r.taiga_user_id) or {}).get("full_name"),
                      "erp_project": r.erp_project, "activity_type": r.activity_type, "hours": _hours(r.hours)}
                     for r in rows]}


@router.get("/projects/{pid}/weeks")
def project_weeks(pid: int, week_from: str | None = Query(None, alias="from"),
                  week_to: str | None = Query(None, alias="to"), db: Session = Depends(get_db),
                  user: CurrentUser = Depends(get_current_wf_user), taiga=Depends(get_taiga)):
    _require_project(db, user, taiga, pid)
    return _summary_out(db, pid, week_from, week_to, _members(taiga, pid))


@router.post("/projects/{pid}/weeks/{week}/approve")
def approve_week(pid: int, week: str, db: Session = Depends(get_db),
                 user: CurrentUser = Depends(get_current_wf_user), taiga=Depends(get_taiga)):
    _require_project(db, user, taiga, pid)
    try:
        periods.week_monday(week)
    except ValueError:
        raise HTTPException(status_code=422, detail="Tydzień w formacie RRRR-Www") from None
    if periods.week_state(db, pid, week) == "frozen":
        raise HTTPException(status_code=409, detail="Tydzień należy do zamkniętego miesiąca")
    row = periods.approve_week(db, pid, week, approved_by=user.username)
    return {"week": row.week, "approved_at": row.approved_at, "approved_by": row.approved_by}


@router.get("/me/weeks")
def my_weeks(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_wf_user),
             erp=Depends(get_erp)):
    """„Mój tydzień” - tylko własne godziny (osoba rozpoznana po e-mailu w ERPNext)."""
    employee = _employee_of(erp, user)
    if employee is None:
        return {"erp_employee": None, "rows": [],
                "message": "Nie znaleziono pracownika ERPNext z Twoim adresem e-mail (Employee.user_id / company_email)"}
    sums: dict[tuple, Decimal] = defaultdict(lambda: ZERO)
    for row in db.execute(select(WorkLedger).where(WorkLedger.erp_employee == employee)).scalars():
        sums[(row.work_week, row.taiga_project_id, row.erp_project, row.activity_type)] += row.hours_delta
    return {"erp_employee": employee,
            "rows": [{"week": w, "taiga_project_id": p, "erp_project": e, "activity_type": a, "hours": _hours(h)}
                     for (w, p, e, a), h in sorted(sums.items(), key=lambda kv: tuple(str(x) for x in kv[0]),
                                                   reverse=True) if h != ZERO]}


# --- ERPNext: różnice i korekty niezrealizowane ---------------------------------------------

@router.get("/erp/plan")
def erp_plan(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_wf_user),
             taiga=Depends(get_taiga)):
    """Różnice dziennik ↔ ERPNext (plan bez połączenia z ERPNext) i korekty niezrealizowane."""
    allowed = managed_projects(db, user, taiga)
    if not allowed:
        raise HTTPException(status_code=403, detail="Brak dostępu do projektów integracji")
    erp_projects = {link.erp_project for link in db.execute(select(TaigaProjectLink)).scalars()
                    if link.taiga_project_id in allowed and link.erp_project}
    admin = is_admin(user, taiga)
    plan = erp_sync.build_plan(db)

    def visible(project: str) -> bool:
        return admin or project in erp_projects

    return {
        "docs": [{"week": d.key.week, "erp_employee": d.key.erp_employee, "erp_project": d.key.erp_project,
                  "action": d.action, "approved": d.approved,
                  "timesheet": d.record.timesheet_name if d.record else None,
                  "state": d.record.state if d.record else None,
                  "erp_error": d.record.erp_error if d.record else None,
                  "lines": {k: _hours(v) for k, v in d.lines.items()},
                  "sent": {k: _hours(v) for k, v in d.current.items()}}
                 for d in plan.docs if d.action != "none" and visible(d.key.erp_project)],
        "unrealized": [{"erp_employee": u.erp_employee, "erp_project": u.erp_project,
                        "activity_type": u.activity_type, "hours": _hours(u.hours), "last_week": u.last_week}
                       for u in plan.unrealized if visible(u.erp_project)],
        "unpostable": {k: _hours(v) for k, v in plan.unpostable.items()} if admin else {},
    }


# --- alerty -----------------------------------------------------------------------------

@router.get("/alerts")
def alerts(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_wf_user),
           taiga=Depends(get_taiga), erp=Depends(get_erp)) -> list[dict[str, Any]]:
    """Alerty integracji: konfiguracja Taigi (administrator), dane ERPNext (administrator), anomalie (PM)."""
    allowed = managed_projects(db, user, taiga)
    if not allowed:
        raise HTTPException(status_code=403, detail="Brak dostępu do projektów integracji")
    found: list[dict[str, Any]] = []
    if is_admin(user, taiga):
        try:
            for f in service.audit(db, taiga, webhook_url=taiga.config.get("webhook_url")):
                found.append({"source": "taiga", "level": f.level, "code": f.code, "message": f.message,
                              "taiga_project_id": f.taiga_project_id})
        except ConnectorError as exc:
            found.append({"source": "taiga", "level": "error", "code": "taiga_unreachable", "message": str(exc)})
        try:
            for f in erp_sync.check(db, erp):
                found.append({"source": "erpnext", "level": f.level, "code": f.code, "message": f.message})
        except ConnectorError as exc:
            found.append({"source": "erpnext", "level": "error", "code": "erpnext_unreachable", "message": str(exc)})
    ctx = ledger.LedgerContext(db)
    for pid in sorted(allowed):
        for a in ledger.anomalies(ctx, pid):
            found.append({"source": "dziennik", "level": "warning", "code": a.code, "message": a.message,
                          "taiga_project_id": pid, "taiga_ref": a.taiga_ref})
    return found


# --- analizy (Etap 6) --------------------------------------------------------------------

@router.get("/projects/{pid}/analysis")
def project_analysis(pid: int, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_wf_user),
                     taiga=Depends(get_taiga), erp=Depends(get_erp)):
    """2026--11-21: wykonanie wobec budżetu (WBS galwork-pm), koszt szacowany ze stawek ERPNext, wskaźniki tygodni."""
    from pm.integrations.taiga import analysis
    _require_project(db, user, taiga, pid)
    return analysis.project_analysis(db, pid, erp=erp)
