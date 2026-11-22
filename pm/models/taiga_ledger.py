# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Integracja Taiga - galwork-pm - ERPNext, tabele Etapu 2.

- `pm_activity_map` - słownik: obiekt Taigi -> Activity Type (wersjonowany),
- `pm_taiga_item` - bieżący stan zadań i US Taigi,
- `pm_work_ledger` - dziennik prac (append-only): różnice godzin per osoba,
  projekt ERPNext, Activity Type i tydzień ISO,
- `pm_period_close` - zamknięte miesiące rozliczeniowe (D11),
- `pm_week_approval` - zatwierdzenie tygodnia projektu przez PM.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base

HOURS = Numeric(10, 2, asdecimal=True)


class ActivityMapScope:
    TASK = "task"
    USERSTORY = "userstory"
    EPIC = "epic"
    ROLE = "role"
    ALL = (TASK, USERSTORY, EPIC, ROLE)


class ActivityMap(Base):
    """Wiersze nie są nadpisywane: zmiana = zamknięcie wersji (`valid_to`) i nowa wersja."""

    __tablename__ = "pm_activity_map"
    __table_args__ = (
        Index("ix_pm_activity_map_lookup", "taiga_project_id", "scope", "taiga_object_id", "valid_to"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    taiga_project_id: Mapped[int] = mapped_column(Integer)
    scope: Mapped[str] = mapped_column(String(16))
    taiga_object_id: Mapped[int] = mapped_column(Integer)
    # 2026-11-21: NULL = wpis tylko nadpisuje projekt ERPNext (Z7), bez rodzaju pracy
    activity_type: Mapped[str | None] = mapped_column(String(140), nullable=True)
    erp_project: Mapped[str | None] = mapped_column(String(140), nullable=True)
    source: Mapped[str] = mapped_column(String(16), default="manual")
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class TaigaItem(Base):
    __tablename__ = "pm_taiga_item"
    __table_args__ = (UniqueConstraint("kind", "taiga_id", name="uq_pm_taiga_item"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))           # 2026-11-21: task | userstory
    taiga_id: Mapped[int] = mapped_column(Integer)
    taiga_project_id: Mapped[int] = mapped_column(Integer, index=True)
    ref: Mapped[int | None] = mapped_column(Integer, nullable=True)
    subject: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # 2026-11-21: zadanie -> jego US; US -> lista epik (dla zadania brana z jego US)
    user_story_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    epic_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)
    assigned_to: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 2026-11-21: US może mieć kilku wykonawców - czas na takiej US jest anomalią
    assigned_users: Mapped[list | None] = mapped_column(JSON, nullable=True)
    assigned_role_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 2026-11-21: osoba, której przypisano czas zadania bez wykonawcy (kto wpisał godziny)
    fallback_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_closed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    tags: Mapped[list | None] = mapped_column(JSON, nullable=True)
    hours_total: Mapped[Decimal] = mapped_column(HOURS, default=Decimal("0"))
    # 2026-11-21: godziny sprzed uruchomienia rozliczeń (pm_taiga.py baseline) - nie są księgowane
    baseline_hours: Mapped[Decimal] = mapped_column(HOURS, default=Decimal("0"))
    activity_type_resolved: Mapped[str | None] = mapped_column(String(140), nullable=True)
    erp_project_resolved: Mapped[str | None] = mapped_column(String(140), nullable=True)
    erp_employee_resolved: Mapped[str | None] = mapped_column(String(140), nullable=True)
    last_change_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_event_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class LedgerReason:
    HOURS_CHANGED = "hours_changed"
    REASSIGNED = "reassigned"
    PERSON_MAPPED = "person_mapped"
    ACTIVITY_CHANGED = "activity_changed"
    PROJECT_CHANGED = "project_changed"
    DELETED = "deleted"
    RESTORED = "restored"
    MANUAL = "manual"


class WorkLedger(Base):
    """Append-only. Suma `hours_delta` pozycji po kluczu = jej bieżący wkład."""

    __tablename__ = "pm_work_ledger"
    __table_args__ = (
        Index("ix_pm_work_ledger_aggregate", "work_week", "erp_employee", "erp_project", "activity_type"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("pm_taiga_item.id"), index=True)
    taiga_project_id: Mapped[int] = mapped_column(Integer, index=True)
    taiga_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    erp_employee: Mapped[str | None] = mapped_column(String(140), nullable=True)
    erp_project: Mapped[str | None] = mapped_column(String(140), nullable=True)
    activity_type: Mapped[str | None] = mapped_column(String(140), nullable=True)
    work_week: Mapped[str] = mapped_column(String(8))       # 2026-11-21: 2026-W47
    origin_week: Mapped[str] = mapped_column(String(8))
    hours_delta: Mapped[Decimal] = mapped_column(HOURS)
    reason: Mapped[str] = mapped_column(String(32))
    event_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    taiga_changed_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PeriodClose(Base):
    __tablename__ = "pm_period_close"

    id: Mapped[int] = mapped_column(primary_key=True)
    month: Mapped[str] = mapped_column(String(7), unique=True)  # 2026-11-21: 2026-11
    closed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    closed_by: Mapped[str | None] = mapped_column(String(255), nullable=True)


class WeekApproval(Base):
    __tablename__ = "pm_week_approval"
    __table_args__ = (UniqueConstraint("taiga_project_id", "week", name="uq_pm_week_approval"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    taiga_project_id: Mapped[int] = mapped_column(Integer)
    week: Mapped[str] = mapped_column(String(8))
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # 2026-11-21: korekta zatwierdzonego dokumentu (cancel + amend) cofa zatwierdzenie
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ErpTimesheetState:
    DRAFT = "draft"            # 2026-11-21: Draft w ERPNext, nadpisywany przy synchronizacji
    SUBMITTED = "submitted"    # 2026-11-21: zatwierdzony (po akceptacji tygodnia przez PM)
    BILLED = "billed"          # 2026-11-21: powiązany z fakturą - nienaruszalny
    CANCELLED = "cancelled"    # 2026-11-21: anulowany bez poprawki (brak godzin do wysłania)
    ERROR = "error"            # 2026-11-21: odrzucenie merytoryczne ERPNext (`erp_error`)


class ErpTimesheet(Base):
    """2026-11-21: Timesheet ERPNext na osobę × projekt × tydzień (D10)."""

    __tablename__ = "pm_erp_timesheet"
    __table_args__ = (UniqueConstraint("erp_employee", "erp_project", "week", name="uq_pm_erp_timesheet"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    erp_employee: Mapped[str] = mapped_column(String(140))
    erp_project: Mapped[str] = mapped_column(String(140))
    week: Mapped[str] = mapped_column(String(8))
    galwork_ref: Mapped[str] = mapped_column(String(255), unique=True)
    timesheet_name: Mapped[str | None] = mapped_column(String(140), nullable=True)
    amended_from: Mapped[str | None] = mapped_column(String(140), nullable=True)
    docstatus: Mapped[int | None] = mapped_column(Integer, nullable=True)
    state: Mapped[str] = mapped_column(String(16), default=ErpTimesheetState.DRAFT)
    erp_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class ErpPosting(Base):
    """2026-11-21: wiersze wysłane w danej wersji dokumentu (rodzaj pracy -> godziny)."""

    __tablename__ = "pm_erp_posting"
    __table_args__ = (UniqueConstraint("timesheet_name", "activity_type", name="uq_pm_erp_posting"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    erp_timesheet_id: Mapped[int] = mapped_column(ForeignKey("pm_erp_timesheet.id"), index=True)
    timesheet_name: Mapped[str] = mapped_column(String(140))
    activity_type: Mapped[str] = mapped_column(String(140))
    hours: Mapped[Decimal] = mapped_column(HOURS)
    ledger_max_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
