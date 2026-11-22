# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Integracja Taiga - galwork-pm - ERPNext, tabele Etapu 1.

- `pm_taiga_project_link` - projekt Taigi objęty rozliczaniem,
- `pm_person_link` - osoba w Taidze -> pracownik (Employee) w ERPNext,
- `pm_taiga_event` - kolejka zdarzeń z Taigi (webhook i timeline), przetwarzana
  asynchronicznie; surowe zdarzenia mają ograniczoną retencję, trwały jest
  dopiero dziennik prac (Etap 2).
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


class TaigaProjectLink(Base):
    __tablename__ = "pm_taiga_project_link"

    id: Mapped[int] = mapped_column(primary_key=True)
    taiga_project_id: Mapped[int] = mapped_column(Integer, unique=True)
    taiga_slug: Mapped[str | None] = mapped_column(String(255), nullable=True)
    pm_project_id: Mapped[int | None] = mapped_column(
        ForeignKey("pm_projects.id", ondelete="SET NULL"), nullable=True)
    erp_project: Mapped[str | None] = mapped_column(String(140), nullable=True)
    default_activity_type: Mapped[str | None] = mapped_column(String(140), nullable=True)
    # 2026--11-21: id atrybutów są osobne dla zadań i US; nazwa jest potrzebna,
    # bo webhook i timeline kluczują wartości nazwą atrybutu, a REST jego id
    hours_attribute_id_task: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hours_attribute_id_us: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hours_attribute_name: Mapped[str] = mapped_column(String(255), default="Czas [h]")
    billable: Mapped[bool] = mapped_column(Boolean, default=False)
    triage_group: Mapped[str | None] = mapped_column(String(255), nullable=True)
    billing_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    webhook_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_webhook_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 2026--11-21: id ostatniego wpisu timeline'u przeniesionego do pm_taiga_event
    timeline_cursor: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_reconcile_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 2026--11-21: stan początkowy (pm_taiga.py baseline) - bez niego dziennik nie księguje
    baseline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now())


class PersonLink(Base):
    __tablename__ = "pm_person_link"
    __table_args__ = (UniqueConstraint("taiga_user_id", "erp_employee", name="uq_pm_person_link"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    taiga_user_id: Mapped[int] = mapped_column(Integer, index=True)
    erp_employee: Mapped[str] = mapped_column(String(140))
    keycloak_user_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # 2026--11-21: email (propozycja po zgodności e-maila), manual, seed
    source: Mapped[str] = mapped_column(String(32), default="manual")
    # 2026--11-21: propozycje po e-mailu czekają na zatwierdzenie przez PM
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    valid_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now())


class TaigaEventStatus:
    PENDING = "pending"
    PROCESSED = "processed"
    IGNORED = "ignored"
    ERROR = "error"


class TaigaEvent(Base):
    __tablename__ = "pm_taiga_event"

    id: Mapped[int] = mapped_column(primary_key=True)
    taiga_project_id: Mapped[int] = mapped_column(Integer, index=True)
    # 2026--11-21: webhook | timeline
    source: Mapped[str] = mapped_column(String(16))
    # 2026--11-21: webhook: sha256 treści; timeline: id wpisu - ponowna dostawa
    # tego samego zdarzenia nie tworzy drugiego wiersza
    dedupe_key: Mapped[str] = mapped_column(String(128), unique=True)
    object_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    action: Mapped[str | None] = mapped_column(String(16), nullable=True)
    object_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    by_taiga_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payload: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default=TaigaEventStatus.PENDING, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
