# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Przegląd tygodnia przez PM (Etap 5, rozdz. 7.2) - logika kroków procesu BPMN.

Proces `pm-przeglad-tygodnia` (galwork-models/projekty/przeglad-tygodnia):
podsumowanie -> przegląd PM (przypisanie rodzajów pracy, zatwierdź / odłóż) ->
zapis triażu i zatwierdzenie tygodni -> wysyłka Timesheetów do ERPNext ->
ewentualna korekta po odrzuceniu przez ERPNext. Ten moduł nie zna silnika
procesów - obsługi kroków (`process_tasks.py`) tylko przekazują mu dane.

Przegląd obejmuje wszystkie niezatwierdzone tygodnie projektu do wskazanego
(zaległe także), bo odłożony przegląd nie może zostawić tygodnia bez
zatwierdzenia na zawsze. Nieprzypisane pozycje są liczone dla całego projektu.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from connector.base import ConnectorError
from pm.integrations.taiga import activity, erp_sync, ledger, periods, service
from pm.models.taiga import PersonLink, TaigaProjectLink
from pm.models.taiga_ledger import TaigaItem, WorkLedger

PROCESS_KEY = "pm-przeglad-tygodnia"
ZERO = Decimal("0")


def business_key(taiga_project_id: int, week: str) -> str:
    return f"pm-przeglad:{taiga_project_id}:{week}"


def _h(value) -> float:
    return round(float(value or 0), 2)


def _link(db: Session, pid: int) -> TaigaProjectLink:
    link = service.get_link(db, pid)
    if link is None:
        raise ValueError(f"Projekt Taigi {pid} nie jest objęty integracją")
    return link


# --- 1. podsumowanie -------------------------------------------------------------------

def pending_weeks(db: Session, pid: int, upto_week: str) -> list[str]:
    """Tygodnie z godzinami do zatwierdzenia: niezatwierdzone, niezamrożone, nie późniejsze niż `upto_week`."""
    closed = periods.closed_months(db)
    weeks = sorted({r.week for r in ledger.week_summary(db, pid) if r.week <= upto_week})
    return [w for w in weeks if periods.week_state(db, pid, w, closed) == "open"]


def unassigned_entries(db: Session, pid: int) -> list[dict[str, Any]]:
    """Pozycje z godzinami bez rodzaju pracy: US (z zadaniami) albo zadanie bez US."""
    items = db.execute(select(TaigaItem).where(
        TaigaItem.taiga_project_id == pid, TaigaItem.deleted.is_(False),
        TaigaItem.activity_type_resolved.is_(None))).scalars().all()
    us_items = {i.taiga_id: i for i in db.execute(select(TaigaItem).where(
        TaigaItem.taiga_project_id == pid, TaigaItem.kind == "userstory")).scalars()}
    groups: dict[tuple[str, int], dict[str, Any]] = {}
    for item in items:
        hours = item.hours_total - item.baseline_hours
        if hours == ZERO:
            continue
        us_id = item.taiga_id if item.kind == "userstory" else item.user_story_id
        if us_id is not None:
            us = us_items.get(us_id)
            key = ("userstory", us_id)
            label = f"historyjka #{(us.ref if us else None) or us_id} {(us.subject if us else '') or ''}".strip()
        else:
            key = ("task", item.taiga_id)
            label = f"zadanie #{item.ref or item.taiga_id} {item.subject or ''}".strip()
        entry = groups.setdefault(key, {"zakres": key[0], "obiekt": key[1], "pozycja": label,
                                        "godziny": 0.0, "rodzaj_pracy": ""})
        entry["godziny"] = _h(entry["godziny"] + float(hours))
    return sorted(groups.values(), key=lambda e: -e["godziny"])


def week_snapshot(db: Session, pid: int, upto_week: str, *, taiga=None) -> dict[str, Any]:
    """Dane formularza „Przegląd tygodnia”."""
    link = _link(db, pid)
    weeks = pending_weeks(db, pid, upto_week)
    members: dict[int, str] = {}
    if taiga is not None:
        try:
            members = {m["user"]: m.get("full_name") for m in taiga.list_memberships(pid) if m.get("user")}
        except ConnectorError:
            members = {}
    rows = [r for r in ledger.week_summary(db, pid) if r.week in weeks]
    summary = [{"tydzien": r.week, "osoba": members.get(r.taiga_user_id) or f"Taiga {r.taiga_user_id}",
                "pracownik": r.erp_employee or "(brak mapowania)",
                "rodzaj_pracy": r.activity_type or "(nieprzypisane)", "godziny": _h(r.hours)} for r in rows]
    plan = erp_sync.build_plan(db)
    corrections = [f"{u.erp_employee} / {u.activity_type}: {_h(u.hours)} h (po {u.last_week})"
                   for u in plan.unrealized if u.erp_project == link.erp_project]
    notes = [a.message for a in ledger.anomalies(ledger.LedgerContext(db), pid)
             if a.code not in ("no_activity_type", "negative_balance")]
    return {
        "tygodnie_do_zatwierdzenia": weeks,
        "podsumowanie": summary,
        "suma_godzin": _h(sum(r.hours for r in rows)),
        "nieprzypisane": unassigned_entries(db, pid),
        "korekty_niezrealizowane": corrections,
        "uwagi": notes,
    }


# --- 2. triaż i zatwierdzenie --------------------------------------------------------------

def apply_triage(db: Session, pid: int, entries: list[dict[str, Any]], *, weeks: list[str], approve: bool,
                 created_by: str | None, allowed=None, taiga=None) -> dict[str, Any]:
    """Zapis przypisań z formularza (`source=triage`) i - przy zatwierdzeniu - zatwierdzenie tygodni."""
    errors: list[str] = []
    changed = False
    for entry in entries or []:
        activity_type = (entry.get("rodzaj_pracy") or "").strip()
        if not activity_type:
            continue
        try:
            activity.set_mapping(db, taiga_project_id=pid, scope=entry["zakres"], taiga_object_id=int(entry["obiekt"]),
                                 activity_type=activity_type, source="triage", created_by=created_by,
                                 allowed_activity_types=allowed)
            changed = True
        except (activity.ActivityMapError, KeyError, ValueError) as exc:
            errors.append(f"{entry.get('pozycja', entry.get('obiekt'))}: {exc}")
    if changed:
        ledger.recompute(ledger.LedgerContext(db, taiga), pid)
    remaining = unassigned_entries(db, pid)
    approved: list[str] = []
    if approve and not remaining and not errors:
        closed = periods.closed_months(db)
        for week in weeks or []:
            if periods.week_state(db, pid, week, closed) == "open":
                periods.approve_week(db, pid, week, approved_by=created_by)
                approved.append(week)
    # 2026-11-21: przyczyna odrzucenia przypisania musi być widoczna w formularzu
    # (pole `komunikat`), nie tylko w danych procesu
    parts = []
    if errors:
        parts.append("Nie wszystkie przypisania zostały zapisane: " + "; ".join(errors) + ".")
    if approve and remaining:
        parts.append(f"Pozostało {len(remaining)} pozycji bez rodzaju pracy - przypisz je albo odłóż przegląd. "
                     "Tygodnie nie zostały zatwierdzone.")
    message = " ".join(parts)
    return {"triage_errors": errors, "triage_remaining": len(remaining), "zatwierdzone_tygodnie": approved,
            "week_approved": bool(approve and not remaining and not errors), "komunikat": message}


# --- 3. wysyłka do ERPNext --------------------------------------------------------------------

def erp_sync_enabled(taiga) -> bool:
    return (taiga.config.get("erp_sync") or "").strip().lower() in ("1", "true", "yes", "tak")


def submit_timesheets(db: Session, erp, taiga, pid: int, weeks: list[str]) -> dict[str, Any]:
    """Wysyłka Timesheetów projektu dla zatwierdzonych tygodni. Błąd techniczny - wyjątek (ponowienie)."""
    link = _link(db, pid)
    if not erp_sync_enabled(taiga):
        return {"erp_status": "Zapis do ERPNext jest wyłączony ([taiga] erp_sync) - Timesheety wyśle "
                              "synchronizacja po włączeniu.", "erp_errors": []}
    wanted = set(weeks or [])
    result = erp_sync.ErpSync(db, erp).sync(
        only=lambda key: key.erp_project == link.erp_project and key.week in wanted)
    errors = [f"{k.week} {k.erp_employee}: {message}" for k, message in result.errors]
    done = ", ".join(f"{a}: {n}" for a, n in sorted(result.actions.items())) or "brak zmian"
    return {"erp_status": f"ERPNext: {done}", "erp_errors": errors}


# --- 4. uruchamianie przeglądów (cron) -----------------------------------------------------------

@dataclass
class StartResult:
    taiga_project_id: int
    week: str
    status: str            # 2026-11-21: started | running | done | nothing | skipped
    detail: str = ""


def system_actor(db: Session):
    """Użytkownik kroków systemowych: `[wf] system_user` (jak scripts/wf_wake.py)."""
    from connector.base import BaseConnector
    from models.user import User
    from wf.schemas.wf_schemas import CurrentUser
    login = ""
    for section in ("wf", "edr"):
        login = (BaseConnector.load_ini_section(section).get("system_user") or "").strip()
        if login:
            break
    user = db.execute(select(User).where((User.name == login) | (User.email == login))).scalars().first() \
        if login else None
    if user is None:
        raise LookupError("ustaw [wf] system_user - użytkownik z tabeli users")
    return CurrentUser(id=user.id, sub=user.keycloak_sub or "sub-pm", username=user.name, email=user.email,
                       display_name="Przegląd tygodnia (cron)", groups=frozenset())


def start_reviews(db: Session, actor, *, week: str | None = None, project: int | None = None,
                  now: datetime | None = None) -> list[StartResult]:
    """Instancje przeglądu dla projektów z godzinami do zatwierdzenia albo nieprzypisanymi pozycjami.

    Domyślnie za poprzedni tydzień. Pomija projekty z przeglądem w toku i tygodnie,
    dla których przegląd już był (klucz biznesowy `pm-przeglad:<projekt>:<tydzień>`).
    """
    from wf.models import ProcessInstance
    from wf.schemas.wf_schemas import ProcessInstanceStatus
    from wf.services.process_service import get_service
    week = week or periods.iso_week((now or datetime.now(UTC)) - timedelta(days=7))
    svc = get_service()
    version = svc.published_version(db, PROCESS_KEY)
    results: list[StartResult] = []
    query = select(TaigaProjectLink).where(TaigaProjectLink.active.is_(True))
    if project is not None:
        query = query.where(TaigaProjectLink.taiga_project_id == project)
    for link in db.execute(query).scalars().all():
        pid = link.taiga_project_id
        if link.baseline_at is None or not link.triage_group:
            results.append(StartResult(pid, week, "skipped", "brak stanu początkowego albo grupy PM (triage_group)"))
            continue
        instances = db.execute(select(ProcessInstance).where(
            ProcessInstance.business_key.like(f"pm-przeglad:{pid}:%"))).scalars().all()
        if any(i.status in (ProcessInstanceStatus.RUNNING, ProcessInstanceStatus.WAITING) for i in instances):
            results.append(StartResult(pid, week, "running", "przegląd w toku"))
            continue
        if any(i.business_key == business_key(pid, week) for i in instances):
            results.append(StartResult(pid, week, "done", "przegląd tego tygodnia już był"))
            continue
        weeks = pending_weeks(db, pid, week)
        if not weeks and not unassigned_entries(db, pid):
            results.append(StartResult(pid, week, "nothing", "brak godzin do zatwierdzenia"))
            continue
        group = link.triage_group.strip().lstrip("/").split("/")[-1]
        instance = svc.start_instance(
            db, version, title=f"Przegląd tygodnia {week} - {link.taiga_slug or pid}",
            business_key=business_key(pid, week), actor=actor,
            data={"taiga_project_id": pid, "tydzien": week, "triage_group": group,
                  "projekt": link.taiga_slug or str(pid), "projekt_erp": link.erp_project or ""})
        results.append(StartResult(pid, week, "started", str(instance.id)))
    return results


# --- 5. przypomnienia -------------------------------------------------------------------

def remind(db: Session, erp, *, week: str | None = None, link_base: str | None = None,
           now: datetime | None = None) -> list[str]:
    """Powiadomienie w hubie dla osób bez godzin w bieżącym tygodniu (powiadomienie, nie blokada).

    Osoba: zatwierdzone mapowanie Taiga -> pracownik ERPNext, konto huba po e-mailu
    pracownika (`Employee.user_id` / `company_email`). Jedno przypomnienie na tydzień.
    """
    from models.user import User
    from wf.models import Notification
    week = week or periods.iso_week(now or datetime.now(UTC))
    title = f"Uzupełnij „Czas [h]” zadań w Taidze - tydzień {week}"
    employees = {p.erp_employee for p in db.execute(select(PersonLink).where(PersonLink.confirmed.is_(True))).scalars()}
    booked = defaultdict(lambda: ZERO)
    for row in db.execute(select(WorkLedger).where(WorkLedger.work_week == week)).scalars():
        if row.erp_employee:
            booked[row.erp_employee] += row.hours_delta
    emails = {}
    for employee in erp.get_list("Employee", fields=["name", "user_id", "company_email", "status"], limit=1000):
        if employee["name"] in employees and employee.get("status") == "Active":
            email = employee.get("user_id") or employee.get("company_email")
            if email:
                emails[employee["name"]] = email.strip().lower()
    notified = []
    for employee, email in sorted(emails.items()):
        if booked[employee] > ZERO:
            continue
        user = db.execute(select(User).where(User.email.ilike(email))).scalars().first()
        if user is None:
            continue
        exists = db.execute(select(Notification.id).where(
            Notification.user_id == user.id, Notification.kind == "pm-przypomnienie",
            Notification.title == title)).first()
        if exists:
            continue
        db.add(Notification(user_id=user.id, kind="pm-przypomnienie", title=title,
                            link=(link_base.rstrip("/") + "/rozliczenia/moj-tydzien") if link_base else None,
                            payload={"week": week, "erp_employee": employee}))
        notified.append(employee)
    db.commit()
    return notified
