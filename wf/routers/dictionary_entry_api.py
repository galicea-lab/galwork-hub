# wf/routers/dictionary_entry_api.py
# coding: utf-8

"""
Admin CRUD dla wierszy słowników płaskich (`dictionary_entry` - zob.
wf/models/wf.py::DictionaryEntry, wf/services/dictionary_entry_service.py).

Odczyt (`GET`) wymaga tylko zalogowania - to samo poziom co reszta modułu
wf. Zapis (`POST`/`PATCH`/`DELETE`) wymaga `require_process_admin` (ta sama
grupa Keycloak, co zarządzanie definicjami procesów - słowniki to też
konfiguracja systemu, nie dane biznesowe użytkownika).
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from api.dependencies import get_db
from wf.dependencies import get_current_wf_user, require_process_admin
from wf.schemas.wf_schemas import (
    CurrentUser,
    DictionaryEntryCreate,
    DictionaryEntryPage,
    DictionaryEntryRead,
    DictionaryEntryUpdate,
    OptionRead,
)
from wf.services import dictionary_entry_service as service
from wf.services.dictionary_entry_service import (
    DictionaryEntryConflictError,
    DictionaryEntryNotFoundError,
)
from wf.services.dictionary_service import TABLE_DICTIONARIES

router = APIRouter(prefix="/dictionary-entries", tags=["dictionary-entries"])


@router.get("/catalog", response_model=list[OptionRead])
def list_dictionary_catalog(_current_user: CurrentUser = Depends(get_current_wf_user)):
    """Znane słowniki płaskie (tabela `dictionary_entry`) - `value` to
    `dictionary_key` do użycia w pozostałych endpointach tego routera,
    `label` to nazwa do pokazania w selektorze admina
    (galwork-arena: ekran "Słowniki")."""
    return [
        OptionRead(value=key, label=dictionary.source_name)
        for key, dictionary in TABLE_DICTIONARIES.items()
    ]


@router.get("", response_model=DictionaryEntryPage)
def list_dictionary_entries(
    dictionary_key: str = Query(..., min_length=1, max_length=100),
    q: str | None = Query(default=None, max_length=200),
    active_only: bool = Query(default=False),
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _current_user: CurrentUser = Depends(get_current_wf_user),
):
    rows, total = service.list_entries(
        db, dictionary_key, q=q, active_only=active_only, limit=limit, offset=offset
    )
    return DictionaryEntryPage(items=rows, total=total)


@router.post("", response_model=DictionaryEntryRead, status_code=201)
def create_dictionary_entry(
    payload: DictionaryEntryCreate,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_process_admin),
):
    try:
        return service.create_entry(db, payload, created_by_id=current_user.id)
    except DictionaryEntryConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.patch("/{entry_id}", response_model=DictionaryEntryRead)
def update_dictionary_entry(
    entry_id: UUID,
    payload: DictionaryEntryUpdate,
    db: Session = Depends(get_db),
    _current_user: CurrentUser = Depends(require_process_admin),
):
    try:
        return service.update_entry(db, entry_id, payload)
    except DictionaryEntryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DictionaryEntryConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.delete("/{entry_id}", status_code=204)
def delete_dictionary_entry(
    entry_id: UUID,
    db: Session = Depends(get_db),
    _current_user: CurrentUser = Depends(require_process_admin),
):
    try:
        service.delete_entry(db, entry_id)
    except DictionaryEntryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
