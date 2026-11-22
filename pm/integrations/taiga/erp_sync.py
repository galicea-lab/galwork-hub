# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Synchronizacja dziennika prac z Timesheet ERPNext (Etap 3, rozdz. 4.5 i 6.3-6.4).

Dokument = osoba × projekt ERPNext × tydzień (D10); wiersz = rodzaj pracy
(Activity Type) z sumą godzin tygodnia. Godziny są umowne (`from_time` =
poniedziałek 00:00), Timesheet z integracji nie jest ewidencją czasu pracy.

Ograniczenia ERPNext (sprawdzone na instancji testowej):
- godziny <= 0 przechodzą w Draft, ale Submit je odrzuca - hub wysyła tylko > 0,
- dokumentu zatwierdzonego nie da się edytować - korekta = cancel + amend,
- dokumentu zafakturowanego nie da się anulować (`LinkExistsError`).

Plan dla serii (osoba, projekt, rodzaj pracy), tydzień po tygodniu:
- tydzień nienaruszalny (zamrożony miesiąc albo dokument zafakturowany):
  różnica dziennik - wysłane przechodzi dalej (`carry`),
- tydzień zmienialny: wysyłamy dziennik + carry, jeśli > 0; ujemna wartość
  pomniejsza kolejne tygodnie, a gdy nie ma ich czym pomniejszyć, zostaje
  korektą niezrealizowaną (do decyzji PM, np. faktura korygująca).

Stany dokumentu: Draft jest nadpisywany; Submit po zatwierdzeniu tygodnia przez
PM dla wszystkich projektów Taigi składających się na dokument (D5); korekta
zatwierdzonego dokumentu = cancel + amend i cofnięcie zatwierdzenia tygodnia.
Idempotencja (K12): przed utworzeniem hub szuka dokumentu po `galwork_ref`.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from connector.base import ConnectorError
from connector.erpnext.erpnext import ErpNextValidationError
from pm.integrations.taiga.periods import closed_months, is_frozen, iso_week, revoke_week, week_monday, week_state
from pm.models.taiga import TaigaProjectLink
from pm.models.taiga_ledger import ErpPosting, ErpTimesheet, ErpTimesheetState, TaigaItem, WorkLedger

ZERO = Decimal("0")
TOLERANCE = Decimal("0.005")
DOCTYPE = "Timesheet"
NOTE = ("galwork-pm: integracja Taiga, tydzień {week}. Godziny umowne (agregat tygodniowy) - "
        "nie jest to ewidencja czasu pracy.")


def galwork_ref(employee: str, project: str, week: str) -> str:
    return f"pm:{employee}:{project}:{week}"


def _dec(value) -> Decimal:
    if value is None:
        return ZERO
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _same(a: dict[str, Decimal], b: dict[str, Decimal]) -> bool:
    keys = {k for k, v in a.items() if abs(v) > TOLERANCE} | {k for k, v in b.items() if abs(v) > TOLERANCE}
    return all(abs(a.get(k, ZERO) - b.get(k, ZERO)) <= TOLERANCE for k in keys)


@dataclass(frozen=True)
class DocKey:
    erp_employee: str
    erp_project: str
    week: str


@dataclass
class DocPlan:
    key: DocKey
    lines: dict[str, Decimal]
    current: dict[str, Decimal]
    record: ErpTimesheet | None
    action: str                      # 2026-11-21: none | create | update | submit | amend | cancel | empty_draft
    approved: bool
    taiga_projects: set[int] = field(default_factory=set)
    refs: list[int] = field(default_factory=list)
    ledger_max_id: int | None = None


@dataclass(frozen=True)
class Unrealized:
    erp_employee: str
    erp_project: str
    activity_type: str
    hours: Decimal
    last_week: str


@dataclass
class SyncPlan:
    docs: list[DocPlan]
    unrealized: list[Unrealized]
    unpostable: dict[str, Decimal]   # 2026-11-21: przyczyna -> godziny (brak pracownika / projektu / rodzaju pracy)


# --- plan -----------------------------------------------------------------------------

def _postings(db: Session, record: ErpTimesheet | None) -> dict[str, Decimal]:
    if record is None or not record.timesheet_name or record.state == ErpTimesheetState.CANCELLED:
        return {}
    rows = db.execute(select(ErpPosting).where(ErpPosting.timesheet_name == record.timesheet_name)).scalars()
    return {r.activity_type: _dec(r.hours) for r in rows}


def build_plan(db: Session, *, now: datetime | None = None) -> SyncPlan:
    """Plan synchronizacji z dziennika i zapisanego stanu dokumentów (bez połączenia z ERPNext)."""
    now = now or datetime.now(UTC)
    closed = closed_months(db)
    current_week = iso_week(now)
    series: dict[tuple[str, str, str], dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: ZERO))
    contributors: dict[tuple[str, str], set[int]] = defaultdict(set)
    refs: dict[DocKey, set[int]] = defaultdict(set)
    max_id: dict[DocKey, int] = {}
    unpostable: dict[str, Decimal] = defaultdict(lambda: ZERO)
    item_refs = {i.id: i.ref or i.taiga_id for i in db.execute(select(TaigaItem)).scalars()}
    for row in db.execute(select(WorkLedger)).scalars():
        if not row.erp_employee or not row.erp_project or not row.activity_type:
            reason = ("brak pracownika" if not row.erp_employee else
                      "brak projektu ERPNext" if not row.erp_project else "brak rodzaju pracy")
            unpostable[reason] += _dec(row.hours_delta)
            continue
        series[(row.erp_employee, row.erp_project, row.activity_type)][row.work_week] += _dec(row.hours_delta)
        contributors[(row.erp_employee, row.erp_project)].add(row.taiga_project_id)
        key = DocKey(row.erp_employee, row.erp_project, row.work_week)
        refs[key].add(item_refs.get(row.item_id))
        max_id[key] = max(max_id.get(key, 0), row.id)
    records = {DocKey(r.erp_employee, r.erp_project, r.week): r for r in db.execute(select(ErpTimesheet)).scalars()}

    def fixed(key: DocKey) -> bool:
        record = records.get(key)
        return is_frozen(key.week, closed) or (record is not None and record.state == ErpTimesheetState.BILLED)

    lines: dict[DocKey, dict[str, Decimal]] = defaultdict(dict)
    unrealized: list[Unrealized] = []
    activity_types_of: dict[tuple[str, str], set[str]] = defaultdict(set)
    for (employee, project, activity), weeks in series.items():
        activity_types_of[(employee, project)].add(activity)
    for key, record in records.items():
        for activity in _postings(db, record):
            activity_types_of[(key.erp_employee, key.erp_project)].add(activity)
    for (employee, project), activities in activity_types_of.items():
        doc_weeks = {k.week for k in records if (k.erp_employee, k.erp_project) == (employee, project)}
        for activity in sorted(activities):
            ledger_weeks = series.get((employee, project, activity), {})
            carry = ZERO
            last = current_week
            for week in sorted(set(ledger_weeks) | doc_weeks | {current_week}):
                key = DocKey(employee, project, week)
                amount = ledger_weeks.get(week, ZERO)
                if fixed(key):
                    carry += amount - _postings(db, records.get(key)).get(activity, ZERO)
                    continue
                last = week
                value = amount + carry
                if value > ZERO:
                    lines[key][activity] = value
                    carry = ZERO
                else:
                    carry = value
            if abs(carry) > TOLERANCE:
                unrealized.append(Unrealized(employee, project, activity, carry, last))

    docs: list[DocPlan] = []
    for key in sorted(set(lines) | {k for k in records if not fixed(k)},
                      key=lambda k: (k.erp_employee, k.erp_project, k.week)):
        record = records.get(key)
        if record is not None and fixed(key):
            continue
        want = lines.get(key, {})
        current = _postings(db, record)
        projects = contributors.get((key.erp_employee, key.erp_project)) or _linked_projects(db, key.erp_project)
        approved = bool(projects) and all(week_state(db, p, key.week, closed) == "approved" for p in projects)
        has_doc = record is not None and record.timesheet_name and record.state != ErpTimesheetState.CANCELLED
        submitted = has_doc and record.docstatus == 1
        if not has_doc:
            action = "create" if want else "none"
        elif _same(want, current):
            action = "submit" if (approved and not submitted and want) else "none"
            if record.state == ErpTimesheetState.ERROR and want:
                action = "update" if not submitted else "amend"
        elif submitted:
            action = "amend" if want else "cancel"
        else:
            action = "update" if want else "empty_draft"
        docs.append(DocPlan(key, dict(want), current, record, action, approved, set(projects),
                            sorted(r for r in refs.get(key, set()) if r is not None), max_id.get(key)))
    return SyncPlan(docs, unrealized, dict(unpostable))


def _linked_projects(db: Session, erp_project: str) -> set[int]:
    return set(db.execute(select(TaigaProjectLink.taiga_project_id).where(
        TaigaProjectLink.erp_project == erp_project)).scalars())


# --- wykonanie --------------------------------------------------------------------------

@dataclass
class SyncResult:
    actions: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    errors: list[tuple[DocKey, str]] = field(default_factory=list)
    plan: SyncPlan | None = None


class ErpSync:
    def __init__(self, db: Session, erp, *, now: datetime | None = None):
        self.db = db
        self.erp = erp
        self.now = now or datetime.now(UTC)
        self._companies: dict[str, str | None] = {}

    # --- odświeżenie stanu z ERPNext

    def refresh(self) -> None:
        """Stan zapisanych dokumentów z ERPNext (faktura, ręczne anulowanie / usunięcie)."""
        for record in self.db.execute(select(ErpTimesheet)).scalars().all():
            if record.state in (ErpTimesheetState.BILLED,):
                continue
            self._refresh_record(record)
        self.db.commit()

    def _find_active(self, ref: str) -> dict | None:
        rows = self.erp.get_list(DOCTYPE, filters=[["galwork_ref", "=", ref], ["docstatus", "<", 2]],
                                 fields=["name", "docstatus", "status", "per_billed", "amended_from"], limit=5)
        return rows[0] if rows else None

    def _refresh_record(self, record: ErpTimesheet) -> dict | None:
        found = self._find_active(record.galwork_ref)
        if found is None:
            if record.timesheet_name and record.state != ErpTimesheetState.CANCELLED:
                # 2026-11-21: dokument anulowany albo usunięty w ERPNext poza hubem
                record.state = ErpTimesheetState.CANCELLED
                record.docstatus = 2
            return None
        record.timesheet_name = found["name"]
        record.docstatus = found.get("docstatus")
        record.amended_from = found.get("amended_from")
        if (found.get("status") == "Billed") or _dec(found.get("per_billed")) > ZERO:
            record.state = ErpTimesheetState.BILLED
        elif record.state != ErpTimesheetState.ERROR:
            record.state = ErpTimesheetState.SUBMITTED if found.get("docstatus") == 1 else ErpTimesheetState.DRAFT
        return found

    # --- dokument

    def _company(self, employee: str) -> str | None:
        if employee not in self._companies:
            self._companies[employee] = self.erp.get_doc("Employee", employee).get("company")
        return self._companies[employee]

    def _billable(self, erp_project: str) -> bool:
        return bool(self.db.execute(select(TaigaProjectLink.id).where(
            TaigaProjectLink.erp_project == erp_project, TaigaProjectLink.billable.is_(True))).first())

    def _payload(self, plan: DocPlan) -> dict:
        key = plan.key
        billable = self._billable(key.erp_project)
        refs = ", ".join(f"#{r}" for r in plan.refs)
        description = "Agregat tygodniowy (godziny umowne, galwork-pm)" + (f" - Taiga: {refs}" if refs else "")
        start = f"{week_monday(key.week).isoformat()} 00:00:00"
        logs = []
        for activity, hours in sorted(plan.lines.items()):
            logs.append({"activity_type": activity, "project": key.erp_project, "from_time": start,
                         "hours": float(hours), "description": description[:1000],
                         "is_billable": 1 if billable else 0,
                         "billing_hours": float(hours) if billable else 0})
        return {"employee": key.erp_employee, "company": self._company(key.erp_employee),
                "parent_project": key.erp_project, "note": NOTE.format(week=key.week),
                "galwork_ref": galwork_ref(key.erp_employee, key.erp_project, key.week), "time_logs": logs}

    def _record(self, key: DocKey) -> ErpTimesheet:
        record = self.db.execute(select(ErpTimesheet).where(
            ErpTimesheet.erp_employee == key.erp_employee, ErpTimesheet.erp_project == key.erp_project,
            ErpTimesheet.week == key.week)).scalars().first()
        if record is None:
            record = ErpTimesheet(erp_employee=key.erp_employee, erp_project=key.erp_project, week=key.week,
                                  galwork_ref=galwork_ref(key.erp_employee, key.erp_project, key.week),
                                  state=ErpTimesheetState.DRAFT)
            self.db.add(record)
            self.db.flush()
        return record

    def _store_postings(self, record: ErpTimesheet, plan: DocPlan) -> None:
        for old in self.db.execute(select(ErpPosting).where(
                ErpPosting.timesheet_name == record.timesheet_name)).scalars():
            self.db.delete(old)
        self.db.flush()
        for activity, hours in plan.lines.items():
            self.db.add(ErpPosting(erp_timesheet_id=record.id, timesheet_name=record.timesheet_name,
                                   activity_type=activity, hours=hours, ledger_max_id=plan.ledger_max_id))

    def execute(self, plan: DocPlan) -> str:
        """Wykonuje działanie planu dla jednego dokumentu; zwraca wykonane działanie."""
        record = plan.record or self._record(plan.key)
        found = self._refresh_record(record) if record.timesheet_name or plan.action == "create" else None
        action = plan.action
        if record.state == ErpTimesheetState.BILLED:
            # 2026-11-21: zafakturowany od ostatniego odświeżenia - różnicę przeniesie następny przebieg
            return "billed"
        if action == "create" and found is not None:
            # 2026-11-21:  dokument już istnieje (np. po utracie odpowiedzi) - zostanie wyrównany
            action = "update" if found.get("docstatus") == 0 else "amend"
        if action == "create":
            created = self.erp.save_doc(DOCTYPE, self._payload(plan))
            record.timesheet_name, record.docstatus = created["name"], 0
        elif action == "update":
            self.erp.save_doc(DOCTYPE, {"time_logs": self._payload(plan)["time_logs"]}, record.timesheet_name)
        elif action in ("amend", "cancel"):
            cancelled = record.timesheet_name
            try:
                self.erp.cancel_doc(DOCTYPE, cancelled)
            except ErpNextValidationError as exc:
                if exc.exc_type == "LinkExistsError":
                    record.state = ErpTimesheetState.BILLED
                    return "billed"
                raise
            # 2026-11-21: anulowanie i cofnięcie zatwierdzenia tygodnia zapisane od razu -
            # gdyby amend się nie powiódł, kolejny przebieg nie zatwierdzi nowego
            # dokumentu bez ponownej akceptacji PM
            record.state, record.docstatus = ErpTimesheetState.CANCELLED, 2
            for project in plan.taiga_projects:
                revoke_week(self.db, project, plan.key.week)
            plan.approved = False
            self.db.commit()
            if action == "amend":
                created = self.erp.amend_doc(DOCTYPE, cancelled, self._payload(plan))
                record.timesheet_name, record.amended_from, record.docstatus = created["name"], cancelled, 0
        elif action == "empty_draft":
            # 2026-11-21: konto techniczne nie ma prawa usuwania - pusty Draft do usunięcia ręcznie
            record.erp_error = "Draft bez godzin do wysłania - usuń ręcznie w ERPNext"
            return action
        if action in ("create", "update", "amend"):
            record.state = ErpTimesheetState.DRAFT
            record.erp_error = None
            self._store_postings(record, plan)
            plan.approved = plan.approved and action != "amend"
        if action == "submit" or (action in ("create", "update") and plan.approved):
            self.erp.submit_doc(DOCTYPE, record.timesheet_name)
            record.docstatus, record.state = 1, ErpTimesheetState.SUBMITTED
            if action != "submit":
                action += "+submit"
        record.last_sync_at = self.now
        return action

    def sync(self, *, dry_run: bool = False, only=None) -> SyncResult:
        """Synchronizacja; `only(DocKey) -> bool` zawęża wykonanie (np. projekt i tygodnie przeglądu)."""
        self.refresh()
        result = SyncResult()
        result.plan = build_plan(self.db, now=self.now)
        for plan in result.plan.docs:
            if plan.action == "none" or (only is not None and not only(plan.key)):
                continue
            if dry_run:
                result.actions[plan.action] += 1
                continue
            try:
                done = self.execute(plan)
                self.db.commit()
                result.actions[done] += 1
            except ErpNextValidationError as exc:
                self.db.rollback()
                record = self._record(plan.key)
                record.state = ErpTimesheetState.ERROR
                record.erp_error = "; ".join(exc.messages) or str(exc)
                record.last_sync_at = self.now
                self.db.commit()
                result.errors.append((plan.key, record.erp_error))
            except ConnectorError:
                # 2026-11-21: błąd techniczny - przerwanie przebiegu, cron ponowi
                self.db.rollback()
                raise
        return result


# --- kontrola danych ERPNext (alerty) ---------------------------------------------------------

@dataclass(frozen=True)
class ErpFinding:
    level: str
    code: str
    message: str


def check(db: Session, erp) -> list[ErpFinding]:
    """Brak / nieaktywny pracownik, zamknięty projekt, nieaktywny rodzaj pracy, brak Activity Cost."""
    from pm.models.taiga import PersonLink
    findings: list[ErpFinding] = []
    employees = {e["name"]: e for e in erp.get_list("Employee", fields=["name", "status", "company"], limit=1000)}
    for person in db.execute(select(PersonLink).where(PersonLink.confirmed.is_(True))).scalars():
        employee = employees.get(person.erp_employee)
        if employee is None:
            findings.append(ErpFinding("error", "employee_missing",
                                       f"Taiga {person.taiga_user_id}: brak pracownika {person.erp_employee}"))
        elif employee.get("status") != "Active":
            findings.append(ErpFinding("warning", "employee_inactive",
                                       f"{person.erp_employee}: status {employee.get('status')}"))
    projects = {p["name"]: p for p in erp.get_list("Project", fields=["name", "status"], limit=1000)}
    used_projects = {p for p in db.execute(select(TaigaProjectLink.erp_project)).scalars() if p}
    used_projects |= {p for p in db.execute(select(WorkLedger.erp_project).distinct()).scalars() if p}
    for name in sorted(used_projects):
        project = projects.get(name)
        if project is None:
            findings.append(ErpFinding("error", "project_missing", f"brak projektu {name}"))
        elif project.get("status") != "Open":
            findings.append(ErpFinding("error", "project_closed",
                                       f"projekt {name}: status {project.get('status')} - zapis odrzucony"))
    activities = {a["name"]: a for a in erp.get_list("Activity Type", fields=["name", "disabled", "costing_rate"],
                                                      limit=500)}
    pairs = set(db.execute(select(WorkLedger.erp_employee, WorkLedger.activity_type).distinct()).all())
    for name in sorted({a for _, a in pairs if a}):
        activity = activities.get(name)
        if activity is None or activity.get("disabled"):
            findings.append(ErpFinding("error", "activity_type_inactive", f"rodzaj pracy {name}: brak albo wyłączony"))
    costs = {(c.get("employee"), c.get("activity_type")) for c in erp.get_list(
        "Activity Cost", fields=["employee", "activity_type"], limit=5000)}
    for employee, activity in sorted((e, a) for e, a in pairs if e and a):
        # 2026-11-21: bez Activity Cost ERPNext bierze stawkę kosztu z Activity Type
        # (sprawdzone na instancji testowej) - ostrzeżenie tylko, gdy i ona wynosi 0
        default_rate = _dec((activities.get(activity) or {}).get("costing_rate"))
        if (employee, activity) not in costs and (None, activity) not in costs and default_rate == ZERO:
            findings.append(ErpFinding("warning", "activity_cost_missing",
                                       f"{employee} / {activity}: brak Activity Cost i stawka kosztu Activity Type "
                                       "= 0 - Timesheet będzie miał koszt 0"))
    return findings
