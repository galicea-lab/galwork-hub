# bcs/api/access.py
# 2026-11-24: kontrola dostępu do danych BCS (dotąd odczyt i część zapisu
# nie wymagały tokenu - zob. galwork-bcs-book/dev.md, "Bezpieczeństwo").
#
#   * operator (`bcs_operator`) - wszystko, także zapis konfiguracji sieci,
#   * audytor (`bcs_auditor`)  - odczyt wszystkiego, bez zapisu,
#   * pozostali zalogowani      - tylko dane uczestników sieci, do których
#     są przypięci (`MemberUser.user_sub` = `sub` z tokenu, nadaje operator).
#
# Uwierzytelnienie wymusza `router.py` (app_bcs z zależnością
# get_current_principal); tu jest autoryzacja per zasób.
from __future__ import annotations

from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.security import BCS_OPERATOR_ROLE, CurrentPrincipal, _token_roles, get_current_principal
from bcs.models.member import MemberUser, NetworkMember

BCS_AUDITOR_ROLE = 'bcs_auditor'


def is_operator(principal: CurrentPrincipal) -> bool:
    return BCS_OPERATOR_ROLE in _token_roles(principal.claims)


def is_staff(principal: CurrentPrincipal) -> bool:
    """Operator albo audytor - wgląd we wszystkie dane BCS."""
    return bool({BCS_OPERATOR_ROLE, BCS_AUDITOR_ROLE} & _token_roles(principal.claims))


def own_member_ids(db: Session, principal: CurrentPrincipal) -> set[str]:
    """Uczestnicy (we wszystkich sieciach), w imieniu których działa wołający."""
    sub = principal.token.sub or principal.claims.get('sub')
    if not sub:
        return set()
    return set(db.execute(select(MemberUser.member_id).where(MemberUser.user_sub == sub)).scalars())


def own_network_ids(db: Session, principal: CurrentPrincipal) -> set[str]:
    members = own_member_ids(db, principal)
    if not members:
        return set()
    return set(db.execute(
        select(NetworkMember.network_id).where(NetworkMember.id.in_(members))).scalars())


def forbidden(detail: str = "Brak dostępu do danych tego uczestnika") -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


def require_staff(principal: CurrentPrincipal = Depends(get_current_principal)) -> CurrentPrincipal:
    """Zależność: operator albo audytor (dane całej sieci)."""
    if not is_staff(principal):
        raise forbidden("Wymagana rola operatora lub audytora BCS")
    return principal


def ensure_member_access(db: Session, principal: CurrentPrincipal, member_id: str, *,
                         write: bool = False) -> None:
    """Odczyt: personel albo uczestnik przypięty do `member_id`.
    Zapis (`write=True`): operator albo uczestnik przypięty do `member_id`."""
    if is_operator(principal) or (not write and is_staff(principal)):
        return
    if member_id not in own_member_ids(db, principal):
        raise forbidden()


def ensure_network_access(db: Session, principal: CurrentPrincipal, network_id: str) -> None:
    """Personel albo uczestnik tej sieci."""
    if is_staff(principal):
        return
    if network_id not in own_network_ids(db, principal):
        raise forbidden("Brak dostępu do tej sieci")

