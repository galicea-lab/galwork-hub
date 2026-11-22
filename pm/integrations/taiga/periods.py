# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Okresy rozliczeniowe - tydzień ISO, miesiąc, zamknięcia.

- tydzień pracy = tydzień ISO momentu zmiany w strefie Europe/Warsaw,
- tydzień należy do miesiąca swojego czwartku (reguła ISO 8601), więc tydzień
  na przełomie miesięcy rozlicza się w jednym miesiącu,
- rozliczenia są miesięczne (D11): zamknięty miesiąc zamraża swoje tygodnie,
  a korekta, która by w nie trafiła, przechodzi do najstarszego otwartego
  tygodnia - jako nowy wpis, nigdy jako zmiana zaksięgowanego.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from pm.models.taiga_ledger import PeriodClose, WeekApproval

TZ = ZoneInfo("Europe/Warsaw")


def iso_week(moment: datetime | date) -> str:
    """`2026-W47` dla chwili (w strefie Europe/Warsaw) albo daty."""
    if isinstance(moment, datetime):
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        moment = moment.astimezone(TZ).date()
    year, week, _ = moment.isocalendar()
    return f"{year}-W{week:02d}"


def week_monday(week: str) -> date:
    year, number = week.split("-W")
    return date.fromisocalendar(int(year), int(number), 1)


def next_week(week: str) -> str:
    return iso_week(week_monday(week) + timedelta(days=7))


def week_month(week: str) -> str:
    """Miesiąc rozliczeniowy tygodnia - miesiąc jego czwartku, np. `2026-11`."""
    thursday = week_monday(week) + timedelta(days=3)
    return f"{thursday.year}-{thursday.month:02d}"


def closed_months(db: Session) -> set[str]:
    return set(db.execute(select(PeriodClose.month)).scalars())


def is_frozen(week: str, closed: set[str]) -> bool:
    return week_month(week) in closed


def booking_week(origin_week: str, closed: set[str]) -> str:
    """Tydzień księgowania: `origin_week`, a gdy jest zamrożony - najstarszy otwarty po nim."""
    week = origin_week
    # 2026-11-21: limit bezpieczeństwa - 10 lat zamkniętych miesięcy z rzędu to błąd danych
    for _ in range(520):
        if not is_frozen(week, closed):
            return week
        week = next_week(week)
    raise ValueError(f"Brak otwartego tygodnia po {origin_week}")


def close_month(db: Session, month: str, *, closed_by: str | None = None) -> PeriodClose:
    """Zamyka miesiąc (idempotentne). Miesiąc w formacie `RRRR-MM`."""
    datetime.strptime(month, "%Y-%m")
    existing = db.execute(select(PeriodClose).where(PeriodClose.month == month)).scalars().first()
    if existing:
        return existing
    row = PeriodClose(month=month, closed_at=datetime.now(UTC), closed_by=closed_by)
    db.add(row)
    db.commit()
    return row


def approve_week(db: Session, taiga_project_id: int, week: str, *,
                 approved_by: str | None = None) -> WeekApproval:
    week_monday(week)
    existing = db.execute(select(WeekApproval).where(
        WeekApproval.taiga_project_id == taiga_project_id, WeekApproval.week == week)).scalars().first()
    if existing:
        if existing.revoked_at is not None:
            # 2026-11-21: ponowne zatwierdzenie po korekcie
            existing.revoked_at = None
            existing.approved_at = datetime.now(UTC)
            existing.approved_by = approved_by
            db.commit()
        return existing
    row = WeekApproval(taiga_project_id=taiga_project_id, week=week,
                       approved_at=datetime.now(UTC), approved_by=approved_by)
    db.add(row)
    db.commit()
    return row


def week_state(db: Session, taiga_project_id: int, week: str, closed: set[str] | None = None) -> str:
    """`frozen` | `approved` | `open` (rozdz. 6.2)."""
    if is_frozen(week, closed if closed is not None else closed_months(db)):
        return "frozen"
    approved = db.execute(select(WeekApproval.id).where(
        WeekApproval.taiga_project_id == taiga_project_id, WeekApproval.week == week,
        WeekApproval.revoked_at.is_(None))).first()
    return "approved" if approved else "open"


def revoke_week(db: Session, taiga_project_id: int, week: str) -> bool:
    """Cofa zatwierdzenie tygodnia (po korekcie zatwierdzonego dokumentu); True = było zatwierdzone."""
    row = db.execute(select(WeekApproval).where(
        WeekApproval.taiga_project_id == taiga_project_id, WeekApproval.week == week,
        WeekApproval.revoked_at.is_(None))).scalars().first()
    if row is None:
        return False
    row.revoked_at = datetime.now(UTC)
    db.flush()
    return True
