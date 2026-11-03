# wf/routers/contact_api.py
# coding: utf-8

"""
Admin CRUD dla kontrahentów i ich adresów (zob. wf/models/wf.py::Contact/
ContactAddress, wf/services/contact_service.py) - ekran "Kontrahenci" w
galwork-arena.

Odczyt (`GET`) wymaga tylko zalogowania. Zapis (`POST`/`PATCH`/`DELETE`)
wymaga `require_process_admin` - tak jak dictionary_entry_api.py."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from api.dependencies import get_db
from wf.dependencies import get_current_wf_user, require_process_admin
from wf.schemas.wf_schemas import (
    ContactAddressCreate,
    ContactAddressRead,
    ContactAddressUpdate,
    ContactCreate,
    ContactPage,
    ContactRead,
    ContactUpdate,
    CurrentUser,
)
from wf.services import contact_service as service
from wf.services.contact_service import (
    ContactAddressConflictError,
    ContactAddressNotFoundError,
    ContactConflictError,
    ContactNotFoundError,
)

router = APIRouter(tags=["contacts"])


@router.get("/contacts", response_model=ContactPage)
def list_contacts(
    q: str | None = Query(default=None, max_length=200),
    active_only: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _current_user: CurrentUser = Depends(get_current_wf_user),
):
    rows, total = service.list_contacts(db, q=q, active_only=active_only, limit=limit, offset=offset)
    return ContactPage(items=rows, total=total)


@router.post("/contacts", response_model=ContactRead, status_code=201)
def create_contact(
    payload: ContactCreate,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_process_admin),
):
    try:
        return service.create_contact(db, payload, created_by_id=current_user.id)
    except ContactConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/contacts/{contact_id}", response_model=ContactRead)
def get_contact(
    contact_id: UUID,
    db: Session = Depends(get_db),
    _current_user: CurrentUser = Depends(get_current_wf_user),
):
    try:
        return service.get_contact(db, contact_id)
    except ContactNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/contacts/{contact_id}", response_model=ContactRead)
def update_contact(
    contact_id: UUID,
    payload: ContactUpdate,
    db: Session = Depends(get_db),
    _current_user: CurrentUser = Depends(require_process_admin),
):
    try:
        return service.update_contact(db, contact_id, payload)
    except ContactNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ContactConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.delete("/contacts/{contact_id}", status_code=204)
def delete_contact(
    contact_id: UUID,
    db: Session = Depends(get_db),
    _current_user: CurrentUser = Depends(require_process_admin),
):
    try:
        service.delete_contact(db, contact_id)
    except ContactNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/contacts/{contact_id}/addresses", response_model=ContactAddressRead, status_code=201)
def create_contact_address(
    contact_id: UUID,
    payload: ContactAddressCreate,
    db: Session = Depends(get_db),
    _current_user: CurrentUser = Depends(require_process_admin),
):
    try:
        return service.create_address(db, contact_id, payload)
    except ContactNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ContactAddressConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.patch("/contact-addresses/{address_id}", response_model=ContactAddressRead)
def update_contact_address(
    address_id: UUID,
    payload: ContactAddressUpdate,
    db: Session = Depends(get_db),
    _current_user: CurrentUser = Depends(require_process_admin),
):
    try:
        return service.update_address(db, address_id, payload)
    except ContactAddressNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ContactAddressConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.delete("/contact-addresses/{address_id}", status_code=204)
def delete_contact_address(
    address_id: UUID,
    db: Session = Depends(get_db),
    _current_user: CurrentUser = Depends(require_process_admin),
):
    try:
        service.delete_address(db, address_id)
    except ContactAddressNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
