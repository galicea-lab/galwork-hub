from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from api.dependencies import get_db
from bcs.api.lookups import get_dispute_or_404, get_network_or_404
from api.security import CurrentPrincipal, get_current_principal, require_operator
from bcs.api.access import forbidden, is_staff, own_member_ids
from bcs.models.dispute import Dispute
from bcs.models.enums import DisputeStatus
from bcs.models.obligation import Obligation
from bcs.schemas.dispute import DisputeOut, DisputeResolve
from bcs.services import dispute_service

router = APIRouter(tags=["disputes"])


@router.get("/api/networks/{network_id}/disputes", response_model=list[DisputeOut])
def list_disputes(network_id: str, status: DisputeStatus | None = None, db: Session = Depends(get_db),
                  principal: CurrentPrincipal = Depends(get_current_principal)):
    get_network_or_404(db, network_id)
    query = (
        select(Dispute)
        .join(Obligation, Obligation.id == Dispute.obligation_id)
        .where(Obligation.network_id == network_id)
    )
    if not is_staff(principal):
        own = own_member_ids(db, principal)
        query = query.where(or_(Obligation.debtor_member_id.in_(own), Obligation.creditor_member_id.in_(own)))
    if status:
        query = query.where(Dispute.status == status)
    return db.execute(query.order_by(Dispute.created_at.desc())).scalars().all()


@router.get("/api/disputes/{dispute_id}", response_model=DisputeOut)
def get_dispute(dispute_id: str, db: Session = Depends(get_db),
                principal: CurrentPrincipal = Depends(get_current_principal)):
    dispute = get_dispute_or_404(db, dispute_id)
    if not is_staff(principal):
        obligation = db.get(Obligation, dispute.obligation_id)
        parties = {obligation.debtor_member_id, obligation.creditor_member_id} if obligation else set()
        if not parties & own_member_ids(db, principal):
            raise forbidden()
    return dispute


@router.post("/api/disputes/{dispute_id}/review", response_model=DisputeOut, dependencies=[Depends(require_operator)])
def review_dispute(dispute_id: str, db: Session = Depends(get_db)):
    dispute = get_dispute_or_404(db, dispute_id)
    try:
        return dispute_service.start_review(db, dispute)
    except dispute_service.DisputeStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post("/api/disputes/{dispute_id}/resolve", response_model=DisputeOut, dependencies=[Depends(require_operator)])
def resolve_dispute(dispute_id: str, payload: DisputeResolve, db: Session = Depends(get_db)):
    """Rozstrzygnięcie przez operatora (§4.1: operator, nie sponsor, jest
    sędzią sporów)."""
    dispute = get_dispute_or_404(db, dispute_id)
    try:
        return dispute_service.resolve_dispute(db, dispute, payload)
    except dispute_service.DisputeStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
