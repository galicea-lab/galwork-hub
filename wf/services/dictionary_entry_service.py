# wf/services/dictionary_entry_service.py
# coding: utf-8
"""
CRUD dla wierszy tabeli `dictionary_entry` (wf/models/wf.py::DictionaryEntry)
- słowniki płaskie (kod/etykieta), zarządzane jako zwykły zasób, bez
SpiffWorkflow (decyzja 2026-10-18, zob. docstring modelu).

Używane z dwóch miejsc:
  * wf/routers/dictionary_entry_api.py - admin CRUD (lista z filtrem/
    wyszukiwaniem, tworzenie, edycja, usuwanie).
  * wf/services/dictionary_service.py - odczyt opcji dla
    `AsyncSelectWidget`/`TreeSelectWidget` (`TableDictionary`), ten sam
    `list_entries` z `active_only=True`.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from wf.models import DictionaryEntry
from wf.schemas.wf_schemas import DictionaryEntryCreate, DictionaryEntryUpdate


class DictionaryEntryNotFoundError(Exception):
    """Brak wiersza o podanym `id`."""


class DictionaryEntryConflictError(Exception):
    """Para (dictionary_key, code) już istnieje (UNIQUE constraint)."""


def list_entries(
    db: Session,
    dictionary_key: str,
    *,
    q: str | None = None,
    active_only: bool = False,
    limit: int = 200,
    offset: int = 0,
) -> tuple[list[DictionaryEntry], int]:
    stmt = select(DictionaryEntry).where(DictionaryEntry.dictionary_key == dictionary_key)
    if active_only:
        stmt = stmt.where(DictionaryEntry.is_active.is_(True))
    if q:
        pattern = f"%{q}%"
        stmt = stmt.where(
            DictionaryEntry.label.ilike(pattern) | DictionaryEntry.code.ilike(pattern)
        )
    total = db.execute(
        select(func.count()).select_from(stmt.order_by(None).subquery())
    ).scalar_one()
    rows = (
        db.execute(
            stmt.order_by(DictionaryEntry.sort_order, DictionaryEntry.label)
            .limit(limit)
            .offset(offset)
        )
        .scalars()
        .all()
    )
    return rows, total


def get_entry(db: Session, entry_id: UUID) -> DictionaryEntry:
    entry = db.get(DictionaryEntry, entry_id)
    if entry is None:
        raise DictionaryEntryNotFoundError(f"Wiersz słownika {entry_id} nie istnieje")
    return entry


def create_entry(
    db: Session, payload: DictionaryEntryCreate, *, created_by_id: int | None
) -> DictionaryEntry:
    entry = DictionaryEntry(
        dictionary_key=payload.dictionary_key,
        code=payload.code,
        label=payload.label,
        sort_order=payload.sort_order,
        is_active=payload.is_active,
        extra=payload.extra,
        created_by_id=created_by_id,
    )
    db.add(entry)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise DictionaryEntryConflictError(
            f"Wiersz o kodzie '{payload.code}' już istnieje w słowniku '{payload.dictionary_key}'"
        ) from exc
    db.refresh(entry)
    return entry


def update_entry(db: Session, entry_id: UUID, payload: DictionaryEntryUpdate) -> DictionaryEntry:
    entry = get_entry(db, entry_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(entry, field, value)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise DictionaryEntryConflictError(
            f"Wiersz o kodzie '{entry.code}' już istnieje w słowniku '{entry.dictionary_key}'"
        ) from exc
    db.refresh(entry)
    return entry


def delete_entry(db: Session, entry_id: UUID) -> None:
    entry = get_entry(db, entry_id)
    db.delete(entry)
    db.commit()
