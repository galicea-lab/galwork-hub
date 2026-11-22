# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Serwis integracji Taigi - kolejka zdarzeń, rekoncyliacja, konfiguracja.

Etap 1 zbiera zdarzenia (webhook + timeline) w `pm_taiga_event` i je
klasyfikuje. Zdarzenia istotne zostają w stanie `pending`, dopóki nie
zarejestruje się ich konsument (dziennik prac, Etap 2) - dzięki temu nic
nie jest oznaczane jako przetworzone, zanim ktoś faktycznie z niego skorzysta.
"""
from __future__ import annotations

import hashlib
import secrets
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from connector.base import ConnectorError
from pm.integrations.taiga.events import (
    TaigaChange,
    TaigaPayloadError,
    from_timeline,
    from_webhook,
)
from pm.models.taiga import PersonLink, TaigaEvent, TaigaEventStatus, TaigaProjectLink

DEFAULT_HOURS_NAME = "Czas [h]"
HOURS_DESCRIPTION = "Łączny czas pracy w godzinach (np. 1.5), rozliczany tygodniowo."

#: 2026-11-21: konsument zdarzenia (Etap 2: dziennik prac); zwraca nic albo podnosi wyjątek
Handler = Callable[[Session, TaigaEvent, TaigaChange], None]
_handlers: list[Handler] = []


class HandlerNotReady(Exception):
    """Konsument nie może jeszcze przetworzyć zdarzenia - zostaje ono w `pending`."""


def register_handler(handler: Handler) -> None:
    if handler not in _handlers:
        _handlers.append(handler)


def clear_handlers() -> None:
    _handlers.clear()


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime | None) -> datetime | None:
    """SQLite oddaje daty bez strefy - traktujemy je jako UTC."""
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def get_link(db: Session, taiga_project_id: int) -> TaigaProjectLink | None:
    return db.execute(select(TaigaProjectLink).where(
        TaigaProjectLink.taiga_project_id == taiga_project_id)).scalars().first()


def _hours_name(link: TaigaProjectLink | None) -> str:
    return (link.hours_attribute_name if link else None) or DEFAULT_HOURS_NAME


# --- kolejka zdarzeń --------------------------------------------------------

def webhook_dedupe_key(raw_body: bytes) -> str:
    return "wh:" + hashlib.sha256(raw_body).hexdigest()


def enqueue(db: Session, *, taiga_project_id: int, source: str, dedupe_key: str,
            payload: dict, change: TaigaChange | None) -> tuple[TaigaEvent | None, bool]:
    """Zapisuje zdarzenie; (zdarzenie, True) albo (None, False) dla duplikatu."""
    event = TaigaEvent(
        taiga_project_id=taiga_project_id,
        source=source,
        dedupe_key=dedupe_key,
        object_type=change.object_type if change else None,
        action=change.action if change else None,
        object_id=change.object_id if change else None,
        occurred_at=change.occurred_at if change else None,
        by_taiga_user_id=change.by_user_id if change else None,
        payload=payload,
        status=TaigaEventStatus.PENDING,
        attempts=0,
    )
    db.add(event)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return None, False
    return event, True


def normalize(event: TaigaEvent, link: TaigaProjectLink | None) -> TaigaChange:
    name = _hours_name(link)
    if event.source == "webhook":
        return from_webhook(event.payload, hours_name=name)
    if event.source == "timeline":
        return from_timeline(event.payload, hours_name=name)
    raise TaigaPayloadError(f"Nieznane źródło zdarzenia: {event.source!r}")


@dataclass
class ProcessResult:
    processed: int = 0
    ignored: int = 0
    errors: int = 0
    waiting: int = 0


def process_pending(db: Session, *, technical_user_id: int | None,
                    handlers: Iterable[Handler] | None = None, limit: int = 500) -> ProcessResult:
    """Klasyfikuje i przekazuje zdarzenia `pending` konsumentom, od najstarszego.

    Ignorowane: zmiany konta technicznego (ochrona przed pętlą przy zapisie do
    Taigi), typy obiektów bez znaczenia dla rozliczeń, projekty bez aktywnego
    powiązania. Bez konsumentów zdarzenia istotne czekają (`waiting`).
    """
    handlers = list(_handlers if handlers is None else handlers)
    result = ProcessResult()
    events = db.execute(
        select(TaigaEvent).where(TaigaEvent.status == TaigaEventStatus.PENDING)
        .order_by(TaigaEvent.occurred_at, TaigaEvent.id).limit(limit)).scalars().all()
    links: dict[int, TaigaProjectLink | None] = {}
    for event in events:
        if event.taiga_project_id not in links:
            links[event.taiga_project_id] = get_link(db, event.taiga_project_id)
        link = links[event.taiga_project_id]
        try:
            change = normalize(event, link)
        except TaigaPayloadError as exc:
            _finish(event, TaigaEventStatus.ERROR, str(exc))
            db.commit()
            result.errors += 1
            continue
        reason = _ignore_reason(change, link, technical_user_id)
        if reason:
            _finish(event, TaigaEventStatus.IGNORED, reason)
            db.commit()
            result.ignored += 1
            continue
        if not handlers:
            result.waiting += 1
            continue
        try:
            for handler in handlers:
                handler(db, event, change)
            event.attempts += 1
            _finish(event, TaigaEventStatus.PROCESSED, None)
            db.commit()
            result.processed += 1
        except HandlerNotReady:
            db.rollback()
            result.waiting += 1
        except Exception as exc:  # noqa: BLE001 - błąd konsumenta trafia do zdarzenia
            # 2026-11-21: cofa tylko zmiany konsumenta dla tego zdarzenia -
            # poprzednie zdarzenia partii są już zatwierdzone
            db.rollback()
            event.attempts += 1
            _finish(event, TaigaEventStatus.ERROR, f"{type(exc).__name__}: {exc}")
            db.commit()
            result.errors += 1
    return result


def _ignore_reason(change: TaigaChange, link: TaigaProjectLink | None,
                   technical_user_id: int | None) -> str | None:
    if link is None or not link.active:
        return "projekt bez aktywnego powiązania"
    if not change.relevant:
        return f"typ {change.object_type} poza zakresem rozliczeń"
    if technical_user_id is not None and change.by_user_id == technical_user_id:
        return "zmiana konta technicznego"
    if change.action == "change" and not change.changed_fields and not change.hours_changed:
        return "zmiana bez znaczenia dla rozliczeń"
    return None


def _finish(event: TaigaEvent, status: str, error: str | None) -> None:
    event.status = status
    event.error = error
    event.processed_at = _now()


def purge_events(db: Session, *, older_than_days: int = 90) -> int:
    """Retencja surowych zdarzeń (RODO): usuwa zakończone starsze niż N dni."""
    cutoff = _now() - timedelta(days=older_than_days)
    events = db.execute(select(TaigaEvent).where(
        TaigaEvent.status.in_([TaigaEventStatus.PROCESSED, TaigaEventStatus.IGNORED]),
        TaigaEvent.received_at < cutoff)).scalars().all()
    for event in events:
        db.delete(event)
    db.commit()
    return len(events)


# --- rekoncyliacja z timeline'u --------------------------------------------

def reconcile_timeline(db: Session, taiga, link: TaigaProjectLink, *, max_pages: int = 50) -> int:
    """Przenosi nowe wpisy timeline'u projektu do kolejki; zwraca liczbę nowych zdarzeń.

    Timeline obejmuje zmiany atrybutów własnych (których nie widać po
    `modified_date`) oraz dostawy webhooków utracone przez Taigę. Pierwsze
    uruchomienie (bez kursora) bierze wpisy od `billing_from`.
    """
    entries = list(taiga.iter_timeline(link.taiga_project_id, after_id=link.timeline_cursor,
                                       max_pages=max_pages))
    since = None
    if link.timeline_cursor is None and link.billing_from:
        since = datetime(link.billing_from.year, link.billing_from.month,
                         link.billing_from.day, tzinfo=UTC)
    created = 0
    cursor = link.timeline_cursor
    for entry in reversed(entries):
        entry_id = entry.get("id")
        if entry_id is None:
            continue
        cursor = max(cursor or 0, entry_id)
        try:
            change = from_timeline(entry, hours_name=_hours_name(link))
        except TaigaPayloadError:
            change = None
        if since and change and change.occurred_at and change.occurred_at < since:
            continue
        _, is_new = enqueue(db, taiga_project_id=link.taiga_project_id, source="timeline",
                            dedupe_key=f"tl:{link.taiga_project_id}:{entry_id}",
                            payload=entry, change=change)
        created += int(is_new)
    link.timeline_cursor = cursor
    link.last_reconcile_at = _now()
    db.commit()
    return created


# --- audyt --------------------------------------------------------------

@dataclass
class Finding:
    level: str          # 2026-11-21: error | warning | info
    code: str
    message: str
    taiga_project_id: int | None = None


def expected_webhook_url(base_url: str, taiga_project_id: int) -> str:
    return f"{base_url.rstrip('/')}/{taiga_project_id}"


def audit(db: Session, taiga, *, webhook_url: str | None, silent_after_days: int = 7,
          reconcile_stale_hours: int = 24) -> list[Finding]:
    """Kontrola konfiguracji: konto techniczne, atrybuty godzin, rekoncyliacja, webhooki.

    Webhook jest opcjonalny: bez `webhook_url` zmiany przychodzą wyłącznie
    z timeline'u (rekoncyliacja), a kontrole webhooka są pomijane.
    """
    findings: list[Finding] = []
    links = db.execute(select(TaigaProjectLink).where(TaigaProjectLink.active.is_(True))).scalars().all()
    linked_ids = {link.taiga_project_id for link in links}
    technical = taiga.technical_user_id
    if not webhook_url and links:
        findings.append(Finding("info", "webhook_disabled",
                                "brak [taiga] webhook_url - zmiany tylko z timeline'u (rekoncyliacja)"))
    for link in links:
        pid = link.taiga_project_id
        try:
            if technical:
                member = [m for m in taiga.list_memberships(pid) if m.get("user") == technical]
                if not member:
                    findings.append(Finding("error", "technical_not_member",
                                            f"konto techniczne (id {technical}) nie jest członkiem projektu", pid))
                elif not member[0].get("is_admin"):
                    findings.append(Finding(
                        "warning", "technical_not_admin",
                        f"konto techniczne (id {technical}) nie jest administratorem projektu - "
                        "brak dostępu do webhooków i ich logu", pid))
            if webhook_url:
                findings.extend(_audit_webhook(taiga, link, webhook_url, silent_after_days))
            findings.extend(_audit_hours_attributes(taiga, link))
        except ConnectorError as exc:
            findings.append(Finding("error", "taiga_unreachable", str(exc), pid))
        last_reconcile = _aware(link.last_reconcile_at)
        if last_reconcile is None or last_reconcile < _now() - timedelta(hours=reconcile_stale_hours):
            findings.append(Finding("warning", "reconcile_stale",
                                    f"ostatnia rekoncyliacja: {last_reconcile or 'nigdy'}", pid))
    if technical:
        try:
            for project in taiga.request("GET", "/projects", params={"member": technical}) or []:
                if project.get("id") not in linked_ids:
                    findings.append(Finding("info", "project_not_linked",
                                            f"projekt {project.get('slug')} bez powiązania",
                                            project.get("id")))
        except ConnectorError as exc:
            findings.append(Finding("warning", "taiga_unreachable", str(exc)))
    return findings


def _audit_webhook(taiga, link: TaigaProjectLink, webhook_url: str,
                   silent_after_days: int) -> list[Finding]:
    pid = link.taiga_project_id
    findings: list[Finding] = []
    if not taiga.webhook_secret(pid):
        findings.append(Finding("error", "webhook_secret_missing",
                                f"brak sekretu webhooka (webhook_secret_{pid})", pid))
    webhooks = taiga.list_webhooks(pid)
    ours = [w for w in webhooks if w.get("url") == expected_webhook_url(webhook_url, pid)]
    if not ours:
        urls = ", ".join(w.get("url", "?") for w in webhooks) or "brak"
        findings.append(Finding("warning", "webhook_missing",
                                f"brak webhooka do huba (webhooki projektu: {urls})", pid))
    for webhook in ours:
        failed = [log for log in taiga.list_webhook_logs(webhook["id"])
                  if not 200 <= int(log.get("status") or 0) < 300]
        if failed:
            findings.append(Finding(
                "warning", "webhook_failed",
                f"{len(failed)} nieudanych dostaw w logu Taigi (ostatnia: "
                f"{failed[0].get('created')}, HTTP {failed[0].get('status')}) - "
                "zmiany uzupełni rekoncyliacja", pid))
    last = _aware(link.last_webhook_at)
    if last is None or last < _now() - timedelta(days=silent_after_days):
        findings.append(Finding("info", "webhook_silent", f"ostatni webhook: {last or 'nigdy'}", pid))
    return findings


def _audit_hours_attributes(taiga, link: TaigaProjectLink) -> list[Finding]:
    pid = link.taiga_project_id
    findings: list[Finding] = []
    for kind, attr_id in (("task", link.hours_attribute_id_task),
                          ("userstory", link.hours_attribute_id_us)):
        attrs = {a.get("id"): a for a in taiga.list_custom_attributes(kind, pid)}
        attr = attrs.get(attr_id)
        if attr is None:
            findings.append(Finding("error", "hours_attribute_missing",
                                    f"brak atrybutu godzin {kind} id={attr_id}", pid))
        elif attr.get("name") != _hours_name(link):
            findings.append(Finding(
                "error", "hours_attribute_renamed",
                f"atrybut {kind} id={attr_id} nazywa się {attr.get('name')!r}, "
                f"oczekiwano {_hours_name(link)!r} - webhook i timeline kluczują nazwą", pid))
    return findings


# --- rejestracja projektu ---------------------------------------------------

@dataclass
class RegisterResult:
    taiga_project_id: int
    attributes: dict[str, int | None] = field(default_factory=dict)
    created_attributes: list[str] = field(default_factory=list)
    webhook_id: int | None = None
    webhook_secret: str | None = None   # 2026--11-21: tylko gdy webhook został utworzony teraz
    actions: list[str] = field(default_factory=list)


def register_project(db: Session, taiga, *, slug: str, webhook_url: str | None,
                     apply: bool, hours_name: str = DEFAULT_HOURS_NAME) -> RegisterResult:
    """Atrybuty „Czas [h]” (zadanie i US), webhook do huba i powiązanie projektu.

    Idempotentne: istniejące atrybuty i webhook są tylko odczytywane. Bez
    `apply` niczego nie zmienia (ani w Taidze, ani w bazie).
    """
    project = taiga.get_project_by_slug(slug)
    pid = int(project["id"])
    result = RegisterResult(taiga_project_id=pid)
    for kind in ("task", "userstory"):
        existing = [a for a in taiga.list_custom_attributes(kind, pid) if a.get("name") == hours_name]
        if existing:
            result.attributes[kind] = existing[0]["id"]
            if existing[0].get("type") != "number":
                result.actions.append(f"UWAGA: atrybut {kind} ma typ {existing[0].get('type')}")
            continue
        result.actions.append(f"utworzenie atrybutu {kind} {hours_name!r} (number)")
        if apply:
            created = taiga.create_custom_attribute(kind, pid, name=hours_name, attr_type="number",
                                                    description=HOURS_DESCRIPTION)
            result.attributes[kind] = created["id"]
            result.created_attributes.append(kind)
        else:
            result.attributes[kind] = None
    if webhook_url:
        url = expected_webhook_url(webhook_url, pid)
        found = [w for w in taiga.list_webhooks(pid) if w.get("url") == url]
        if found:
            result.webhook_id = found[0]["id"]
        else:
            result.actions.append(f"utworzenie webhooka -> {url}")
            if apply:
                secret = secrets.token_hex(32)
                created = taiga.create_webhook(pid, name="galwork-hub", url=url, key=secret)
                result.webhook_id = created["id"]
                result.webhook_secret = secret
    link = get_link(db, pid)
    if link is None:
        result.actions.append("utworzenie powiązania pm_taiga_project_link")
    if apply:
        if link is None:
            link = TaigaProjectLink(taiga_project_id=pid, hours_attribute_name=hours_name,
                                    billable=False, active=True)
            db.add(link)
        link.taiga_slug = project.get("slug") or slug
        link.hours_attribute_name = hours_name
        link.hours_attribute_id_task = result.attributes.get("task")
        link.hours_attribute_id_us = result.attributes.get("userstory")
        if result.webhook_id:
            link.webhook_id = result.webhook_id
        db.commit()
    return result


# --- mapowanie osób --------------------------------------------------------

@dataclass
class PersonProposal:
    taiga_user_id: int
    full_name: str | None
    email: str | None
    erp_employee: str | None
    status: str          # 2026-11-21: new | exists | no_email | no_match


def propose_person_links(db: Session, taiga, erp, *, taiga_project_id: int,
                         apply: bool = False) -> list[PersonProposal]:
    """Propozycje `taiga_user_id -> Employee` po zgodności e-maila.

    E-mail członka (`memberships.user_email`, widoczny dla administratora
    projektu) porównywany z `Employee.user_id` i `Employee.company_email`.
    Z `apply` propozycje trafiają do `pm_person_link` jako niezatwierdzone
    (`confirmed=False`, `source='email'`) - zatwierdza je PM.
    """
    employees = erp.get_list("Employee", filters=[["status", "=", "Active"]],
                             fields=["name", "user_id", "company_email"], limit=1000)
    by_email: dict[str, str] = {}
    for employee in employees:
        for key in ("user_id", "company_email"):
            if employee.get(key):
                by_email.setdefault(employee[key].strip().lower(), employee["name"])
    proposals = []
    technical = taiga.technical_user_id
    for member in taiga.list_memberships(taiga_project_id):
        user_id = member.get("user")
        # 2026-11-21: konto techniczne nie rozlicza czasu - bez propozycji mapowania
        if user_id is None or user_id == technical:
            continue
        email = (member.get("user_email") or member.get("email") or "").strip().lower() or None
        existing = db.execute(select(PersonLink).where(PersonLink.taiga_user_id == user_id)).scalars().first()
        employee = by_email.get(email) if email else None
        if existing:
            status = "exists"
            employee = existing.erp_employee
        elif not email:
            status = "no_email"
        elif not employee:
            status = "no_match"
        else:
            status = "new"
            if apply:
                db.add(PersonLink(taiga_user_id=user_id, erp_employee=employee, source="email",
                                  confirmed=False, note="propozycja po zgodności e-maila"))
        proposals.append(PersonProposal(user_id, member.get("full_name"), email, employee, status))
    if apply:
        db.commit()
    return proposals


# --- dane startowe (seed) ----------------------------------------------------

_LINK_FIELDS = ("taiga_slug", "erp_project", "hours_attribute_id_task", "hours_attribute_id_us",
                "hours_attribute_name", "default_activity_type", "billable", "triage_group",
                "billing_from", "active", "pm_project_id")
_PERSON_FIELDS = ("keycloak_user_id", "source", "valid_from", "valid_to", "note")


def _seed_value(key: str, value: Any) -> Any:
    if key in ("billing_from", "valid_from", "valid_to") and isinstance(value, str):
        return date.fromisoformat(value)
    return value


def load_seed(db: Session, data: dict) -> dict[str, int]:
    """Ładuje dane startowe (projekty, osoby); ponowne wczytanie aktualizuje, nie dubluje.

    Klucze zaczynające się od `_` to komentarze i są pomijane. Osoby z seeda
    są zatwierdzone (`confirmed=True`) - seed jest decyzją administratora.
    """
    stats = {"projects_created": 0, "projects_updated": 0, "persons_created": 0, "persons_updated": 0}
    for row in data.get("pm_taiga_project_link") or []:
        pid = int(row["taiga_project_id"])
        link = get_link(db, pid)
        if link is None:
            link = TaigaProjectLink(taiga_project_id=pid, hours_attribute_name=DEFAULT_HOURS_NAME,
                                    billable=False, active=True)
            db.add(link)
            stats["projects_created"] += 1
        else:
            stats["projects_updated"] += 1
        for key in _LINK_FIELDS:
            if key in row:
                setattr(link, key, _seed_value(key, row[key]))
    for row in data.get("pm_person_link") or []:
        user_id, employee = int(row["taiga_user_id"]), row["erp_employee"]
        person = db.execute(select(PersonLink).where(PersonLink.taiga_user_id == user_id,
                                                     PersonLink.erp_employee == employee)).scalars().first()
        if person is None:
            person = PersonLink(taiga_user_id=user_id, erp_employee=employee)
            db.add(person)
            stats["persons_created"] += 1
        else:
            stats["persons_updated"] += 1
        person.source = "seed"
        person.confirmed = True
        for key in _PERSON_FIELDS:
            if key in row and key != "source":
                setattr(person, key, _seed_value(key, row[key]))
        if row.get("source"):
            person.note = "; ".join(filter(None, [f"źródło: {row['source']}", row.get("_uwagi")]))
    db.commit()
    return stats
