# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Dziennik prac (`pm_work_ledger`) liczony ze STANU pozycji Taigi.

Dla każdej pozycji (zadanie albo US) silnik wylicza docelowy wkład - klucz
(osoba Taigi, pracownik ERPNext, projekt ERPNext, Activity Type) i liczbę
godzin - i porównuje go z sumą jej wpisów w dzienniku. Zapisuje tylko różnicę:

- przeklasyfikowanie (inny wykonawca, mapowanie osoby, projekt, rodzaj pracy)
  przenosi saldo między kluczami w TYCH SAMYCH tygodniach, w których godziny
  zaksięgowano (zamrożony tydzień -> najstarszy otwarty po nim),
- zmiana godzin (także usunięcie = 0 h) trafia do tygodnia zmiany.

Powtórzenie zdarzenia (np. ta sama zmiana z webhooka i z timeline'u) daje
różnicę 0, więc deduplikacja między źródłami nie jest potrzebna. Zdarzenie
starsze od ostatniej zastosowanej zmiany pozycji jest pomijane - Taiga nie
gwarantuje kolejności, a późniejsze zdarzenie niesie nowszy stan.

Niezmiennik (testowany): dla każdej pozycji suma wpisów pod bieżącym kluczem
= `hours_total - baseline_hours` (0 dla usuniętej), pod innymi kluczami = 0.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from connector.base import ConnectorError
from pm.integrations.taiga import activity as activity_mod
from pm.integrations.taiga.events import TaigaChange, from_rest
from pm.integrations.taiga.periods import booking_week, closed_months, iso_week, week_state
from pm.models.taiga import PersonLink, TaigaEvent, TaigaProjectLink
from pm.models.taiga_ledger import LedgerReason, TaigaItem, WorkLedger

ZERO = Decimal("0")


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _dec(value) -> Decimal:
    if value is None:
        return ZERO
    return value if isinstance(value, Decimal) else Decimal(str(value))


@dataclass(frozen=True)
class Key:
    taiga_user_id: int | None
    erp_employee: str | None
    erp_project: str | None
    activity_type: str | None


@dataclass(frozen=True)
class Entry:
    key: Key
    work_week: str
    origin_week: str
    hours_delta: Decimal
    reason: str


class LedgerContext:
    """Wspólny kontekst przetwarzania: sesja, Taiga (opcjonalnie) i pamięci podręczne."""

    def __init__(self, db: Session, taiga=None, *, now: datetime | None = None):
        self.db = db
        self.taiga = taiga
        self._now = now
        self._links: dict[int, TaigaProjectLink | None] = {}
        self._indexes: dict[int, activity_mod.MapIndex] = {}
        self._roles: dict[int, dict[int, int]] = {}
        self._closed: set[str] | None = None

    def now(self) -> datetime:
        return self._now or datetime.now(UTC)

    def link(self, pid: int) -> TaigaProjectLink | None:
        if pid not in self._links:
            self._links[pid] = self.db.execute(select(TaigaProjectLink).where(
                TaigaProjectLink.taiga_project_id == pid)).scalars().first()
        return self._links[pid]

    def index(self, pid: int) -> activity_mod.MapIndex:
        if pid not in self._indexes:
            self._indexes[pid] = activity_mod.MapIndex.load(self.db, pid)
        return self._indexes[pid]

    def invalidate(self) -> None:
        self._indexes.clear()
        self._links.clear()
        self._closed = None

    @property
    def closed(self) -> set[str]:
        if self._closed is None:
            self._closed = closed_months(self.db)
        return self._closed

    def member_role(self, pid: int, user_id: int | None) -> int | None:
        if user_id is None or self.taiga is None:
            return None
        if pid not in self._roles:
            try:
                self._roles[pid] = {m["user"]: m.get("role") for m in self.taiga.list_memberships(pid)
                                    if m.get("user") is not None}
            except ConnectorError:
                self._roles[pid] = {}
        return self._roles[pid].get(user_id)

    def employee(self, user_id: int | None, on: date | None = None) -> str | None:
        """Pracownik ERPNext osoby Taigi - tylko zatwierdzone powiązania, ważne w dniu `on`."""
        if user_id is None:
            return None
        on = on or self.now().date()
        links = self.db.execute(select(PersonLink).where(
            PersonLink.taiga_user_id == user_id, PersonLink.confirmed.is_(True))).scalars().all()
        valid = [p for p in links if (p.valid_from is None or p.valid_from <= on)
                 and (p.valid_to is None or p.valid_to >= on)]
        valid.sort(key=lambda p: p.valid_from or date.min, reverse=True)
        return valid[0].erp_employee if valid else None

    def item(self, kind: str, taiga_id: int) -> TaigaItem | None:
        return self.db.execute(select(TaigaItem).where(
            TaigaItem.kind == kind, TaigaItem.taiga_id == taiga_id)).scalars().first()

    def fetch(self, kind: str, taiga_id: int, *, occurred_at: datetime | None,
              by_user_id: int | None) -> TaigaChange | None:
        """Bieżący stan z REST; None = obiekt nie istnieje (usunięty)."""
        if self.taiga is None:
            raise LookupError("brak konektora Taigi")
        try:
            obj = self.taiga.get_object(kind, taiga_id)
        except ConnectorError as exc:
            if " -> 404" in str(exc):
                return None
            raise
        link = self.link(_id(obj.get("project")))
        attr_id = None
        if link is not None:
            attr_id = link.hours_attribute_id_task if kind == "task" else link.hours_attribute_id_us
        values = self.taiga.get_custom_attribute_values(kind, taiga_id)
        return from_rest(kind, obj, values, hours_attribute_id=attr_id, occurred_at=occurred_at,
                         by_user_id=by_user_id)


def _id(value):
    if isinstance(value, dict):
        return value.get("id")
    return value


# --- wkład pozycji i plan różnic -------------------------------------------------

def desired(ctx: LedgerContext, item: TaigaItem,
            index: activity_mod.MapIndex | None = None) -> tuple[Key, Decimal, activity_mod.Resolution]:
    link = ctx.link(item.taiga_project_id)
    user = item.assigned_to if item.assigned_to is not None else item.fallback_user_id
    user_story = ctx.item("userstory", item.user_story_id) if item.kind == "task" and item.user_story_id else None
    resolution = activity_mod.resolve(item, link, index or ctx.index(item.taiga_project_id), user_story)
    key = Key(user, ctx.employee(user), resolution.erp_project, resolution.activity_type)
    hours = ZERO if item.deleted else _dec(item.hours_total) - _dec(item.baseline_hours)
    return key, hours, resolution


def balances(db: Session, item_id: int) -> dict[Key, dict[str, Decimal]]:
    result: dict[Key, dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: ZERO))
    if item_id is None:
        return result
    rows = db.execute(select(WorkLedger).where(WorkLedger.item_id == item_id)).scalars()
    for row in rows:
        key = Key(row.taiga_user_id, row.erp_employee, row.erp_project, row.activity_type)
        result[key][row.work_week] += _dec(row.hours_delta)
    return result


def _rekey_reason(old: Key, new: Key) -> str:
    if old.taiga_user_id != new.taiga_user_id:
        return LedgerReason.REASSIGNED
    if old.erp_employee != new.erp_employee:
        return LedgerReason.PERSON_MAPPED
    if old.erp_project != new.erp_project:
        return LedgerReason.PROJECT_CHANGED
    return LedgerReason.ACTIVITY_CHANGED


def plan(ctx: LedgerContext, item: TaigaItem, *, change_week: str, hours_reason: str,
         index: activity_mod.MapIndex | None = None) -> list[Entry]:
    key, hours, _ = desired(ctx, item, index)
    entries: list[Entry] = []
    total = ZERO
    for old_key, weeks in balances(ctx.db, item.id).items():
        for week, amount in sorted(weeks.items()):
            total += amount
            if old_key == key or amount == ZERO:
                continue
            reason = _rekey_reason(old_key, key)
            target = booking_week(week, ctx.closed)
            entries.append(Entry(old_key, target, week, -amount, reason))
            entries.append(Entry(key, target, week, amount, reason))
    delta = hours - total
    if delta != ZERO:
        entries.append(Entry(key, booking_week(change_week, ctx.closed), change_week, delta, hours_reason))
    return entries


def _before_billing(link: TaigaProjectLink | None, week: str) -> bool:
    return bool(link and link.billing_from and week < iso_week(link.billing_from))


def sync_item(ctx: LedgerContext, item: TaigaItem, *, occurred_at: datetime | None,
              hours_reason: str = LedgerReason.HOURS_CHANGED, event_id: int | None = None,
              changed_by: int | None = None) -> list[Entry]:
    """Wyrównuje dziennik pozycji do jej bieżącego stanu; zwraca zapisane wpisy."""
    if item.id is None:
        ctx.db.flush()
    change_week = iso_week(occurred_at or ctx.now())
    link = ctx.link(item.taiga_project_id)
    if _before_billing(link, change_week):
        # 2026-11-21: zmiana godzin sprzed startu rozliczeń - do bazy, bez księgowania
        key, hours, _ = desired(ctx, item)
        booked = sum((a for weeks in balances(ctx.db, item.id).values() for a in weeks.values()), ZERO)
        item.baseline_hours = _dec(item.baseline_hours) + (hours - booked)
    entries = plan(ctx, item, change_week=change_week, hours_reason=hours_reason)
    key, _, resolution = desired(ctx, item)
    item.activity_type_resolved = resolution.activity_type
    item.erp_project_resolved = resolution.erp_project
    item.erp_employee_resolved = key.erp_employee
    for entry in entries:
        ctx.db.add(WorkLedger(
            item_id=item.id, taiga_project_id=item.taiga_project_id,
            taiga_user_id=entry.key.taiga_user_id, erp_employee=entry.key.erp_employee,
            erp_project=entry.key.erp_project, activity_type=entry.key.activity_type,
            work_week=entry.work_week, origin_week=entry.origin_week, hours_delta=entry.hours_delta,
            reason=entry.reason, event_id=event_id, taiga_changed_by=changed_by, occurred_at=occurred_at,
            # 2026-11-21: czas zapisu z aplikacji - server_default bywa zaokrąglony (SQLite)
            # albo równy początkowi transakcji (PostgreSQL), a analiza porównuje go z chwilą zatwierdzenia
            created_at=datetime.now(UTC),
        ))
    ctx.db.flush()
    return entries


# --- zastosowanie zdarzenia ----------------------------------------------------------

@dataclass
class ApplyResult:
    applied: bool
    entries: list[Entry] = field(default_factory=list)
    note: str | None = None


def _ensure_item(ctx: LedgerContext, kind: str, taiga_id: int, pid: int) -> TaigaItem:
    item = ctx.item(kind, taiga_id)
    if item is None:
        item = TaigaItem(kind=kind, taiga_id=taiga_id, taiga_project_id=pid, hours_total=ZERO,
                         baseline_hours=ZERO, deleted=False)
        ctx.db.add(item)
        ctx.db.flush()
    return item


def ensure_user_story(ctx: LedgerContext, us_id: int, pid: int) -> TaigaItem | None:
    """US zadania - z bazy albo pobrana z REST (potrzebna do rozstrzygania po US i epice)."""
    item = ctx.item("userstory", us_id)
    if item is not None or ctx.taiga is None:
        return item
    state = ctx.fetch("userstory", us_id, occurred_at=None, by_user_id=None)
    if state is None:
        return None
    item = _ensure_item(ctx, "userstory", us_id, pid)
    # 2026-11-21: US pobrana jako kontekst zadania - tylko struktura (epiki,
    # wykonawca); jej godziny zaksięguje dopiero jej własne zdarzenie, które może
    # jeszcze czekać w kolejce
    state.hours_known = False
    _apply_fields(ctx, item, state, by_user_id=None)
    ctx.db.flush()
    return item


def _apply_fields(ctx: LedgerContext, item: TaigaItem, state: TaigaChange, *, by_user_id: int | None) -> None:
    if state.ref is not None:
        item.ref = state.ref
    if state.subject:
        item.subject = state.subject[:500]
    if state.hours_known:
        item.hours_total = state.hours if state.hours is not None else ZERO
    if state.assigned_known:
        item.assigned_to = state.assigned_to_id
    if state.assigned_users is not None:
        item.assigned_users = list(state.assigned_users)
    if state.is_closed is not None:
        item.is_closed = state.is_closed
    if state.tags is not None:
        item.tags = list(state.tags)
    if item.kind == "task" and state.user_story_id is not None:
        item.user_story_id = state.user_story_id
    if item.kind == "userstory" and state.epic_ids is not None:
        item.epic_ids = list(state.epic_ids)
    if item.assigned_to is None and _dec(item.hours_total) > ZERO and item.fallback_user_id is None:
        # 2026-11-21: zadanie bez wykonawcy - czas osoby, która wpisała godziny (flaga w anomaliach)
        item.fallback_user_id = by_user_id
    item.assigned_role_id = ctx.member_role(
        item.taiga_project_id, item.assigned_to if item.assigned_to is not None else item.fallback_user_id)


def apply_change(ctx: LedgerContext, change: TaigaChange, *, event_id: int | None = None) -> ApplyResult:
    """Stosuje znormalizowane zdarzenie do stanu pozycji i wyrównuje dziennik."""
    if change.object_type == "relateduserstory":
        return _apply_relation(ctx, change, event_id=event_id)
    if change.object_type not in ("task", "userstory") or change.object_id is None:
        return ApplyResult(False, note=f"typ {change.object_type} bez wpływu na dziennik")
    kind, oid = change.object_type, change.object_id
    occurred = _aware(change.occurred_at) or ctx.now()
    item = ctx.item(kind, oid)
    if item is not None and item.last_change_at is not None and occurred < _aware(item.last_change_at):
        return ApplyResult(False, note="zdarzenie starsze od zastosowanego stanu")
    pid = change.project_id or (item.taiga_project_id if item else None)
    if pid is None:
        return ApplyResult(False, note="brak projektu")

    state: TaigaChange | None = change
    deleted = change.action == "delete"
    needs_state = change.needs_fetch or (item is None and (change.source != "webhook" or kind == "userstory"))
    if not deleted and needs_state and ctx.taiga is not None:
        state = ctx.fetch(kind, oid, occurred_at=occurred, by_user_id=change.by_user_id)
        deleted = state is None
        if state is not None:
            # 2026-11-21: z REST tylko struktura (wykonawca, US, epiki) - godziny
            # z samego zdarzenia; bieżące godziny z REST przypisane chwili starszego
            # zdarzenia trafiłyby do złego tygodnia. Pozycja widziana pierwszy raz po
            # stanie początkowym (baseline) ma całą historię godzin w zdarzeniach.
            state.hours, state.hours_known = change.hours, change.hours_known
            state.hours_error, state.hours_changed = change.hours_error, change.hours_changed
    if item is None and deleted:
        return ApplyResult(False, note="usunięcie nieznanej pozycji")
    item = item or _ensure_item(ctx, kind, oid, pid)

    previous_epics = list(item.epic_ids or [])
    reason = LedgerReason.HOURS_CHANGED
    if deleted:
        item.deleted = True
        reason = LedgerReason.DELETED
    else:
        if item.deleted:
            item.deleted = False
            reason = LedgerReason.RESTORED
        _apply_fields(ctx, item, state, by_user_id=change.by_user_id)
    if kind == "task" and item.user_story_id:
        ensure_user_story(ctx, item.user_story_id, pid)
    entries = sync_item(ctx, item, occurred_at=occurred, hours_reason=reason, event_id=event_id,
                        changed_by=change.by_user_id)
    item.last_change_at = occurred
    item.last_event_id = event_id
    if kind == "userstory" and list(item.epic_ids or []) != previous_epics:
        entries += _sync_tasks_of(ctx, item, occurred=occurred, event_id=event_id, changed_by=change.by_user_id)
    ctx.db.flush()
    return ApplyResult(True, entries)


def _sync_tasks_of(ctx: LedgerContext, user_story: TaigaItem, *, occurred: datetime,
                   event_id: int | None, changed_by: int | None) -> list[Entry]:
    entries: list[Entry] = []
    tasks = ctx.db.execute(select(TaigaItem).where(
        TaigaItem.kind == "task", TaigaItem.user_story_id == user_story.taiga_id)).scalars().all()
    for task in tasks:
        entries += sync_item(ctx, task, occurred_at=occurred, event_id=event_id, changed_by=changed_by)
    return entries


def _apply_relation(ctx: LedgerContext, change: TaigaChange, *, event_id: int | None) -> ApplyResult:
    if change.user_story_id is None or change.epic_id is None:
        return ApplyResult(False, note="relacja bez US albo epiki")
    pid = change.project_id
    item = ctx.item("userstory", change.user_story_id)
    if item is None:
        if pid is None:
            return ApplyResult(False, note="brak projektu")
        item = ensure_user_story(ctx, change.user_story_id, pid) or _ensure_item(
            ctx, "userstory", change.user_story_id, pid)
    epics = set(item.epic_ids or [])
    if change.action == "delete":
        epics.discard(change.epic_id)
    else:
        epics.add(change.epic_id)
    if sorted(epics) == sorted(item.epic_ids or []):
        return ApplyResult(False, note="relacja bez zmian")
    item.epic_ids = sorted(epics)
    occurred = _aware(change.occurred_at) or ctx.now()
    entries = sync_item(ctx, item, occurred_at=occurred, event_id=event_id, changed_by=change.by_user_id)
    entries += _sync_tasks_of(ctx, item, occurred=occurred, event_id=event_id, changed_by=change.by_user_id)
    return ApplyResult(True, entries)


def make_handler(taiga=None):
    """Konsument kolejki `pm_taiga_event` (service.process_pending)."""
    from pm.integrations.taiga.service import HandlerNotReady

    def handler(db: Session, event: TaigaEvent, change: TaigaChange) -> None:
        ctx = LedgerContext(db, taiga)
        link = ctx.link(event.taiga_project_id)
        if link is None or link.baseline_at is None:
            raise HandlerNotReady("projekt bez stanu początkowego (pm_taiga.py baseline)")
        apply_change(ctx, change, event_id=event.id)
    return handler


# --- przeliczenia po zmianach słownika i mapowania osób -------------------------------

@dataclass
class ItemPlan:
    item: TaigaItem
    entries: list[Entry]


def _project_items(ctx: LedgerContext, pid: int) -> list[TaigaItem]:
    return list(ctx.db.execute(select(TaigaItem).where(TaigaItem.taiga_project_id == pid)
                               .order_by(TaigaItem.kind, TaigaItem.taiga_id)).scalars())


def preview_mapping(ctx: LedgerContext, *, taiga_project_id: int, scope: str, taiga_object_id: int,
                    activity_type: str | None, erp_project: str | None = None) -> list[ItemPlan]:
    """Skutki zmiany słownika BEZ zapisu: które pozycje i ile godzin przejdzie (Z4)."""
    index = ctx.index(taiga_project_id).with_entry(
        scope, taiga_object_id, activity_mod.MapEntry(activity_type, erp_project))
    week = iso_week(ctx.now())
    plans = []
    for item in _project_items(ctx, taiga_project_id):
        entries = plan(ctx, item, change_week=week, hours_reason=LedgerReason.HOURS_CHANGED, index=index)
        if entries:
            plans.append(ItemPlan(item, entries))
    return plans


def apply_mapping(ctx: LedgerContext, *, taiga_project_id: int, scope: str, taiga_object_id: int,
                  activity_type: str | None, erp_project: str | None = None, source: str = "manual",
                  created_by: str | None = None, allowed_activity_types=None) -> list[ItemPlan]:
    """Zmiana słownika + przeniesienie godzin w dzienniku; zwraca zapisane wpisy."""
    activity_mod.set_mapping(ctx.db, taiga_project_id=taiga_project_id, scope=scope,
                             taiga_object_id=taiga_object_id, activity_type=activity_type,
                             erp_project=erp_project, source=source, created_by=created_by,
                             allowed_activity_types=allowed_activity_types, now=ctx.now())
    ctx.invalidate()
    return recompute(ctx, taiga_project_id)


def recompute(ctx: LedgerContext, taiga_project_id: int, *, user_id: int | None = None) -> list[ItemPlan]:
    """Wyrównuje dziennik wszystkich pozycji projektu (opcjonalnie: jednej osoby)."""
    plans = []
    for item in _project_items(ctx, taiga_project_id):
        if user_id is not None and user_id not in (item.assigned_to, item.fallback_user_id):
            continue
        entries = sync_item(ctx, item, occurred_at=ctx.now())
        if entries:
            plans.append(ItemPlan(item, entries))
    ctx.db.commit()
    return plans


def confirm_person(ctx: LedgerContext, taiga_user_id: int, erp_employee: str) -> list[ItemPlan]:
    """Zatwierdza mapowanie osoby i przenosi jej godziny na pracownika ERPNext."""
    links = ctx.db.execute(select(PersonLink).where(PersonLink.taiga_user_id == taiga_user_id)).scalars().all()
    target = [p for p in links if p.erp_employee == erp_employee]
    if not target:
        target = [PersonLink(taiga_user_id=taiga_user_id, erp_employee=erp_employee, source="manual")]
        ctx.db.add(target[0])
    target[0].confirmed = True
    ctx.db.flush()
    plans = []
    for pid in {i.taiga_project_id for i in ctx.db.execute(select(TaigaItem)).scalars()}:
        plans += recompute(ctx, pid, user_id=taiga_user_id)
    ctx.db.commit()
    return plans


# --- rekoncyliacja usunięć i stan początkowy -------------------------------------------

def detect_deletions(ctx: LedgerContext, link: TaigaProjectLink) -> list[TaigaItem]:
    """Usunięcia niewidoczne w timeline: pozycje, których nie ma już na liście REST."""
    pid = link.taiga_project_id
    deleted = []
    for kind in ("task", "userstory"):
        existing = {obj["id"] for obj in ctx.taiga.list_objects(kind, pid)}
        items = ctx.db.execute(select(TaigaItem).where(
            TaigaItem.taiga_project_id == pid, TaigaItem.kind == kind,
            TaigaItem.deleted.is_(False))).scalars().all()
        for item in items:
            if item.taiga_id not in existing:
                item.deleted = True
                sync_item(ctx, item, occurred_at=ctx.now(), hours_reason=LedgerReason.DELETED)
                item.last_change_at = ctx.now()
                deleted.append(item)
    ctx.db.commit()
    return deleted


def baseline(ctx: LedgerContext, link: TaigaProjectLink) -> dict[str, int]:
    """Stan początkowy projektu przed startem rozliczeń - godziny już wpisane NIE są księgowane.

    Uruchamiać raz, przed pierwszym przetworzeniem kolejki; istniejące pozycje pomija.
    """
    pid = link.taiga_project_id
    stats = {"created": 0, "skipped": 0}
    for kind, attr_id in (("userstory", link.hours_attribute_id_us), ("task", link.hours_attribute_id_task)):
        for obj in ctx.taiga.list_objects(kind, pid):
            if ctx.item(kind, obj["id"]) is not None:
                stats["skipped"] += 1
                continue
            values = ctx.taiga.get_custom_attribute_values(kind, obj["id"])
            state = from_rest(kind, obj, values, hours_attribute_id=attr_id, occurred_at=None)
            item = _ensure_item(ctx, kind, obj["id"], pid)
            _apply_fields(ctx, item, state, by_user_id=None)
            item.baseline_hours = _dec(item.hours_total)
            key, _, resolution = desired(ctx, item)
            item.activity_type_resolved = resolution.activity_type
            item.erp_project_resolved = resolution.erp_project
            item.erp_employee_resolved = key.erp_employee
            item.last_change_at = ctx.now()
            stats["created"] += 1
    link.baseline_at = ctx.now()
    ctx.db.commit()
    return stats


# --- raporty ---------------------------------------------------------------------

@dataclass(frozen=True)
class SummaryRow:
    week: str
    erp_employee: str | None
    taiga_user_id: int | None
    erp_project: str | None
    activity_type: str | None
    hours: Decimal


def week_summary(db: Session, taiga_project_id: int, week: str | None = None) -> list[SummaryRow]:
    """Agregat dziennika: tydzień × osoba × projekt ERPNext × Activity Type (bez zer)."""
    query = select(WorkLedger).where(WorkLedger.taiga_project_id == taiga_project_id)
    if week:
        query = query.where(WorkLedger.work_week == week)
    sums: dict[tuple, Decimal] = defaultdict(lambda: ZERO)
    for row in db.execute(query).scalars():
        sums[(row.work_week, row.erp_employee, row.taiga_user_id, row.erp_project, row.activity_type)] += \
            _dec(row.hours_delta)
    return [SummaryRow(*k, v) for k, v in sorted(sums.items(), key=lambda kv: tuple(str(x) for x in kv[0]))
            if v != ZERO]


@dataclass(frozen=True)
class Anomaly:
    code: str
    message: str
    item_id: int | None = None
    taiga_ref: int | None = None


def anomalies(ctx: LedgerContext, taiga_project_id: int) -> list[Anomaly]:
    """Sytuacje do przeglądu przez PM (rozdz. 8 i Z9 projektu)."""
    found: list[Anomaly] = []
    items = _project_items(ctx, taiga_project_id)
    tasks_by_us: dict[int, list[TaigaItem]] = defaultdict(list)
    for item in items:
        if item.kind == "task" and item.user_story_id and not item.deleted:
            tasks_by_us[item.user_story_id].append(item)
    for item in items:
        if item.deleted:
            continue
        hours = _dec(item.hours_total) - _dec(item.baseline_hours)
        if hours == ZERO:
            continue
        label = f"{item.kind} #{item.ref or item.taiga_id}"
        if item.assigned_to is None:
            found.append(Anomaly("no_assignee", f"{label}: czas bez wykonawcy, przypisany osobie, "
                                 f"która wpisała godziny (Taiga id {item.fallback_user_id})", item.id, item.ref))
        if item.kind == "userstory":
            if any(_dec(t.hours_total) > ZERO for t in tasks_by_us.get(item.taiga_id, [])):
                found.append(Anomaly("double_counting", f"{label}: czas wpisany na US i na jej zadaniach",
                                     item.id, item.ref))
            if item.assigned_users and len(item.assigned_users) > 1:
                found.append(Anomaly("multiple_assignees", f"{label}: czas na US z kilkoma wykonawcami - "
                                     "zaksięgowany głównemu wykonawcy", item.id, item.ref))
        if item.activity_type_resolved is None:
            found.append(Anomaly("no_activity_type", f"{label}: brak rodzaju pracy - do triażu", item.id, item.ref))
        if item.erp_employee_resolved is None:
            found.append(Anomaly("no_employee", f"{label}: osoba bez zatwierdzonego mapowania na pracownika",
                                 item.id, item.ref))
        if item.erp_project_resolved is None:
            found.append(Anomaly("no_erp_project", f"{label}: brak projektu ERPNext", item.id, item.ref))
    for row in week_summary(ctx.db, taiga_project_id):
        if row.hours < ZERO:
            found.append(Anomaly("negative_balance",
                                 f"{row.week} {row.erp_employee or row.taiga_user_id} {row.activity_type}: "
                                 f"saldo {row.hours} h (korekta do decyzji PM)"))
    return found


def week_states(db: Session, taiga_project_id: int, weeks: list[str]) -> dict[str, str]:
    closed = closed_months(db)
    return {week: week_state(db, taiga_project_id, week, closed) for week in weeks}
