# api/lookups.py
# Wspólne "pobierz albo 404" dla routerów. Komunikaty są celowo różne od
# FastAPI-owego "Not Found" - frontend (src/api/client.ts::isMissingEndpoint)
# odróżnia po tym brak rekordu od braku trasy.
from fastapi import HTTPException
from sqlalchemy.orm import Session

from bcs.models.cycle import ClearingCycle
from bcs.models.dispute import Dispute
from bcs.models.member import NetworkMember, Organization
from bcs.models.network import BarterNetwork
from bcs.models.obligation import Obligation


def get_network_or_404(db: Session, network_id: str) -> BarterNetwork:
    network = db.get(BarterNetwork, network_id)
    if network is None:
        raise HTTPException(status_code=404, detail="Sieć nie znaleziona")
    return network


def get_organization_or_404(db: Session, organization_id: str) -> Organization:
    org = db.get(Organization, organization_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organizacja nie znaleziona")
    return org


def get_member_or_404(db: Session, member_id: str, network_id: str | None = None) -> NetworkMember:
    """`network_id` podany = uczestnik musi należeć do tej sieci (ścieżki
    /networks/{network_id}/members/{member_id})."""
    member = db.get(NetworkMember, member_id)
    if member is None or (network_id is not None and member.network_id != network_id):
        raise HTTPException(status_code=404, detail="Uczestnik nie znaleziony")
    return member


def get_obligation_or_404(db: Session, obligation_id: str, network_id: str | None = None) -> Obligation:
    obligation = db.get(Obligation, obligation_id)
    if obligation is None or (network_id is not None and obligation.network_id != network_id):
        raise HTTPException(status_code=404, detail="Zobowiązanie nie znalezione")
    return obligation


def get_cycle_or_404(db: Session, cycle_id: str) -> ClearingCycle:
    cycle = db.get(ClearingCycle, cycle_id)
    if cycle is None:
        raise HTTPException(status_code=404, detail="Cykl nie znaleziony")
    return cycle


def get_dispute_or_404(db: Session, dispute_id: str) -> Dispute:
    dispute = db.get(Dispute, dispute_id)
    if dispute is None:
        raise HTTPException(status_code=404, detail="Spór nie znaleziony")
    return dispute
