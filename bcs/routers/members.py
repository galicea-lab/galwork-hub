from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from api.dependencies import get_db
from bcs.api.lookups import get_member_or_404, get_network_or_404, get_organization_or_404
from api.security import require_operator
from bcs.models.member import MemberUser, NetworkMember
from bcs.schemas.member import (
    MemberUserCreate, MemberUserOut, NetworkMemberCreate, NetworkMemberOut, NetworkMemberUpdate,
)
from bcs.services import member_service

router = APIRouter(prefix="/api/networks/{network_id}/members", tags=["members"])


@router.get("", response_model=list[NetworkMemberOut])
def list_members(network_id: str, db: Session = Depends(get_db)):
    get_network_or_404(db, network_id)
    return db.execute(
        select(NetworkMember).where(NetworkMember.network_id == network_id).order_by(NetworkMember.member_code)
    ).scalars().all()


@router.post("", response_model=NetworkMemberOut, status_code=201, dependencies=[Depends(require_operator)])
def add_member(network_id: str, payload: NetworkMemberCreate, db: Session = Depends(get_db)):
    """Dodanie uczestnika do sieci (§7.6: POST /networks/{id}/members) -
    wchodzi jako INVITED, ACTIVE dopiero po KYB (POST .../activate)."""
    get_network_or_404(db, network_id)
    get_organization_or_404(db, payload.organization_id)
    duplicate = db.execute(
        select(NetworkMember).where(
            NetworkMember.network_id == network_id, NetworkMember.member_code == payload.member_code
        )
    ).scalars().first()
    if duplicate is not None:
        # member_code identyfikuje stronę w imporcie z ERP - musi być
        # unikalny w sieci (services/obligation_service.py::_member_by_code)
        raise HTTPException(status_code=409, detail=f"Kod uczestnika {payload.member_code} jest już zajęty w tej sieci")
    member = NetworkMember(network_id=network_id, **payload.model_dump())
    db.add(member)
    db.commit()
    db.refresh(member)
    return member


@router.get("/{member_id}", response_model=NetworkMemberOut)
def get_member(network_id: str, member_id: str, db: Session = Depends(get_db)):
    return get_member_or_404(db, member_id, network_id)


@router.patch("/{member_id}", response_model=NetworkMemberOut, dependencies=[Depends(require_operator)])
def update_member(network_id: str, member_id: str, payload: NetworkMemberUpdate, db: Session = Depends(get_db)):
    """Rola, limit kredytowy, konto rozliczeniowe w e-Talar.
    TODO(bcs): four-eyes przy zmianie limitu (§14.2)."""
    member = get_member_or_404(db, member_id, network_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(member, key, value)
    db.commit()
    db.refresh(member)
    return member


@router.post("/{member_id}/activate", response_model=NetworkMemberOut, dependencies=[Depends(require_operator)])
def activate_member(network_id: str, member_id: str, db: Session = Depends(get_db)):
    """Aktywacja po KYB (§7.6) - 409, jeśli KYB organizacji nie jest APPROVED."""
    member = get_member_or_404(db, member_id, network_id)
    try:
        return member_service.activate_member(db, member)
    except member_service.MemberStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post("/{member_id}/suspend", response_model=NetworkMemberOut, dependencies=[Depends(require_operator)])
def suspend_member(network_id: str, member_id: str, db: Session = Depends(get_db)):
    member = get_member_or_404(db, member_id, network_id)
    try:
        return member_service.suspend_member(db, member)
    except member_service.MemberStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.get("/{member_id}/users", response_model=list[MemberUserOut])
def list_member_users(network_id: str, member_id: str, db: Session = Depends(get_db)):
    get_member_or_404(db, member_id, network_id)
    return db.execute(select(MemberUser).where(MemberUser.member_id == member_id)).scalars().all()


@router.post(
    "/{member_id}/users", response_model=MemberUserOut, status_code=201, dependencies=[Depends(require_operator)]
)
def add_member_user(network_id: str, member_id: str, payload: MemberUserCreate, db: Session = Depends(get_db)):
    """Nadanie kontu Keycloak (`sub`) prawa działania w imieniu uczestnika -
    na tej podstawie działa GET /api/me/memberships."""
    get_member_or_404(db, member_id, network_id)
    link = MemberUser(member_id=member_id, **payload.model_dump())
    db.add(link)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Użytkownik jest już powiązany z tym uczestnikiem")
    db.refresh(link)
    return link


@router.delete("/{member_id}/users/{user_sub}", status_code=204, dependencies=[Depends(require_operator)])
def remove_member_user(network_id: str, member_id: str, user_sub: str, db: Session = Depends(get_db)):
    get_member_or_404(db, member_id, network_id)
    link = db.execute(
        select(MemberUser).where(MemberUser.member_id == member_id, MemberUser.user_sub == user_sub)
    ).scalars().first()
    if link is None:
        raise HTTPException(status_code=404, detail="Powiązanie użytkownika nie znalezione")
    db.delete(link)
    db.commit()
