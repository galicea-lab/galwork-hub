# services/member_service.py
# Cykl życia członkostwa w sieci - barter.md §7.6 (activate po KYB), §13.4.
from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from bcs.models.enums import KybStatus, MemberStatus
from bcs.models.member import NetworkMember, Organization


class MemberStateError(Exception):
    """Operacja niedozwolona w obecnym stanie uczestnika/organizacji."""


def activate_member(db: Session, member: NetworkMember) -> NetworkMember:
    """INVITED/SUSPENDED -> ACTIVE. Warunek: organizacja ma KYB APPROVED -
    uczestnik bez weryfikacji nie może brać udziału w nettingu (§13.4).
    TODO(bcs): podpisana umowa sieci (§13.2) i mandat clearingowy (§10.3)
    jako dodatkowe warunki, kiedy będą modelowane."""
    if member.status not in (MemberStatus.INVITED, MemberStatus.SUSPENDED):
        raise MemberStateError(f"Uczestnik w stanie {member.status.value}, nie INVITED/SUSPENDED")
    org = db.get(Organization, member.organization_id)
    if org is None or org.kyb_status != KybStatus.APPROVED:
        status = org.kyb_status.value if org else "brak organizacji"
        raise MemberStateError(f"Aktywacja wymaga zatwierdzonego KYB organizacji (obecnie: {status})")
    member.status = MemberStatus.ACTIVE
    if member.onboarded_at is None:
        member.onboarded_at = date.today()
    db.commit()
    db.refresh(member)
    return member


def suspend_member(db: Session, member: NetworkMember) -> NetworkMember:
    if member.status != MemberStatus.ACTIVE:
        raise MemberStateError(f"Uczestnik w stanie {member.status.value}, nie ACTIVE")
    member.status = MemberStatus.SUSPENDED
    db.commit()
    db.refresh(member)
    return member
