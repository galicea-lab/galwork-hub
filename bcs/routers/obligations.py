from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from api.dependencies import get_db
from bcs.api.lookups import get_network_or_404, get_obligation_or_404
from bcs.models.enums import ObligationStatus
from bcs.models.obligation import Obligation
from bcs.schemas.dispute import DisputeCreate, DisputeOut
from bcs.schemas.obligation import ObligationBatchIn, ObligationBatchResult, ObligationOut
from bcs.services import dispute_service
from bcs.services.obligation_service import import_obligations

router = APIRouter(prefix="/api/networks/{network_id}/obligations", tags=["obligations"])


@router.post("/batch", response_model=ObligationBatchResult)
def import_obligations_batch(network_id: str, payload: ObligationBatchIn, db: Session = Depends(get_db)):
    """Import zobowiązań z ERP/księgowości uczestnika - kontrakt §7.3.
    TODO(bcs): brak walidacji `Idempotency-Key`/podpisu HMAC na poziomie
    HTTP (§7.6/§7.7) - dziś idempotencja jest tylko na poziomie
    `UniqueConstraint(source_system_id, external_id)` w bazie."""
    get_network_or_404(db, network_id)
    return import_obligations(db, network_id, payload)


@router.get("", response_model=list[ObligationOut])
def list_obligations(
    network_id: str,
    member_id: str | None = None,
    role: str | None = None,
    status: ObligationStatus | None = None,
    db: Session = Depends(get_db),
):
    """Filtry: `member_id` (+ `role`=debtor|creditor, domyślnie obie
    strony), `status`. Portal uczestnika powinien zawsze podawać
    `member_id` - po dodaniu tokenów wymusi to backend."""
    get_network_or_404(db, network_id)
    query = select(Obligation).where(Obligation.network_id == network_id)
    if member_id:
        if role == "debtor":
            query = query.where(Obligation.debtor_member_id == member_id)
        elif role == "creditor":
            query = query.where(Obligation.creditor_member_id == member_id)
        else:
            query = query.where(
                or_(Obligation.debtor_member_id == member_id, Obligation.creditor_member_id == member_id)
            )
    if status:
        query = query.where(Obligation.status == status)
    return db.execute(query.order_by(Obligation.created_at.desc())).scalars().all()


@router.get("/{obligation_id}", response_model=ObligationOut)
def get_obligation(network_id: str, obligation_id: str, db: Session = Depends(get_db)):
    return get_obligation_or_404(db, obligation_id, network_id)


@router.post("/{obligation_id}/dispute", response_model=DisputeOut, status_code=201)
def dispute_obligation(network_id: str, obligation_id: str, payload: DisputeCreate, db: Session = Depends(get_db)):
    """Spór (§7.6/§8.4) - dokument przechodzi w DISPUTED i wypada z nettingu."""
    obligation = get_obligation_or_404(db, obligation_id, network_id)
    try:
        return dispute_service.open_dispute(db, obligation, payload)
    except dispute_service.DisputeStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
