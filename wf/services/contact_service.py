# wf/services/contact_service.py
# coding: utf-8
"""
CRUD dla kontrahentów (`Contact`) i ich adresów (`ContactAddress`) - zob.
wf/models/wf.py (docstring `Contact` o podziale tożsamość/adres) i rozmowę
2026-10-19. Zastępuje dla słownika "Adres" (adres-e-doreczen) dotychczasowy
hack "ProcessInstance = kontrahent" (wf/services/dictionary_service.py).

Używane z dwóch miejsc:
  * wf/routers/contact_api.py - admin CRUD (ekran "Kontrahenci" w
    galwork-arena).
  * wf/services/dictionary_service.py - odczyt opcji dla
    `AsyncSelectWidget` (`ContactAddressDictionary`).
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from wf.models import Contact, ContactAddress
from wf.schemas.wf_schemas import (
    ContactAddressCreate,
    ContactAddressUpdate,
    ContactCreate,
    ContactUpdate,
)


class ContactNotFoundError(Exception):
    pass


class ContactConflictError(Exception):
    """NIP albo PESEL już istnieje (UNIQUE constraint, zob. wf/models/wf.py)."""


class ContactAddressNotFoundError(Exception):
    pass


class ContactAddressConflictError(Exception):
    """ADE już istnieje (UNIQUE constraint)."""


def _with_addresses(stmt):
    return stmt.options(selectinload(Contact.addresses))


def list_contacts(
    db: Session, *, q: str | None = None, active_only: bool = False, limit: int = 50, offset: int = 0
) -> tuple[list[Contact], int]:
    stmt = select(Contact)
    if active_only:
        stmt = stmt.where(Contact.is_active.is_(True))
    if q:
        pattern = f"%{q}%"
        stmt = stmt.where(
            or_(
                Contact.company_name.ilike(pattern),
                Contact.last_name.ilike(pattern),
                Contact.first_name.ilike(pattern),
                Contact.nip.ilike(pattern),
                Contact.pesel.ilike(pattern),
            )
        )
    total = db.execute(select(func.count()).select_from(stmt.order_by(None).subquery())).scalar_one()
    rows = (
        db.execute(
            _with_addresses(stmt)
            .order_by(Contact.company_name, Contact.last_name, Contact.first_name)
            .limit(limit)
            .offset(offset)
        )
        .scalars()
        .all()
    )
    return rows, total


def get_contact(db: Session, contact_id: UUID) -> Contact:
    contact = db.execute(_with_addresses(select(Contact).where(Contact.id == contact_id))).scalars().first()
    if contact is None:
        raise ContactNotFoundError(f"Kontrahent {contact_id} nie istnieje")
    return contact


def create_contact(db: Session, payload: ContactCreate, *, created_by_id: int | None) -> Contact:
    contact = Contact(**payload.model_dump(), created_by_id=created_by_id)
    db.add(contact)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ContactConflictError(
            f"Kontrahent z tym NIP/PESEL już istnieje ({payload.nip or payload.pesel})"
        ) from exc
    db.refresh(contact)
    return contact


def update_contact(db: Session, contact_id: UUID, payload: ContactUpdate) -> Contact:
    contact = get_contact(db, contact_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(contact, field, value)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ContactConflictError(
            f"Kontrahent z tym NIP/PESEL już istnieje ({contact.nip or contact.pesel})"
        ) from exc
    db.refresh(contact)
    return contact


def delete_contact(db: Session, contact_id: UUID) -> None:
    contact = get_contact(db, contact_id)
    db.delete(contact)  # kaskada na contact_address - ondelete="CASCADE" w DB
    db.commit()


def get_address(db: Session, address_id: UUID) -> ContactAddress:
    address = db.get(ContactAddress, address_id)
    if address is None:
        raise ContactAddressNotFoundError(f"Adres {address_id} nie istnieje")
    return address


def create_address(db: Session, contact_id: UUID, payload: ContactAddressCreate) -> ContactAddress:
    # 404 wcześniej niż IntegrityError FK, jeśli kontrahent nie istnieje.
    get_contact(db, contact_id)
    address = ContactAddress(contact_id=contact_id, **payload.model_dump())
    db.add(address)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ContactAddressConflictError(f"Adres z ADE '{payload.ade}' już istnieje") from exc
    db.refresh(address)
    return address


def update_address(db: Session, address_id: UUID, payload: ContactAddressUpdate) -> ContactAddress:
    address = get_address(db, address_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(address, field, value)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ContactAddressConflictError(f"Adres z ADE '{address.ade}' już istnieje") from exc
    db.refresh(address)
    return address


def delete_address(db: Session, address_id: UUID) -> None:
    address = get_address(db, address_id)
    db.delete(address)
    db.commit()
