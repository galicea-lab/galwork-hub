from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from api.dependencies import get_db
from api.security import CurrentPrincipal, get_current_principal
from bcs.api.access import ensure_member_access, forbidden, is_operator, is_staff, own_member_ids
from bcs.api.lookups import get_network_or_404, get_obligation_or_404
from bcs.models.enums import ObligationStatus
from bcs.models.member import NetworkMember
from bcs.models.obligation import Obligation
from bcs.models.source_system import SourceSystem
from bcs.schemas.dispute import DisputeCreate, DisputeOut
from bcs.schemas.obligation import ObligationBatchIn, ObligationBatchResult, ObligationOut
from bcs.services import dispute_service
from bcs.services.obligation_service import import_obligations

router = APIRouter(prefix="/api/networks/{network_id}/obligations", tags=["obligations"])


@router.post("/batch", response_model=ObligationBatchResult)
def import_obligations_batch(network_id: str, payload: ObligationBatchIn, db: Session = Depends(get_db),
                             principal: CurrentPrincipal = Depends(get_current_principal)):
    """Import zobowiązań z ERP/księgowości uczestnika - kontrakt §7.3.
    TODO(bcs): brak walidacji `Idempotency-Key`/podpisu HMAC na poziomie
    HTTP (§7.6/§7.7) - dziś idempotencja jest tylko na poziomie
    `UniqueConstraint(source_system_id, external_id)` w bazie."""
    get_network_or_404(db, network_id)
    # 2026-11-24: system źródłowy musi należeć do uczestnika tej sieci; poza
    # operatorem importuje tylko uczestnik-właściciel systemu i tylko dokumenty,
    # w których sam jest stroną.
    source = db.get(SourceSystem, payload.source_system_id)
    owner = db.get(NetworkMember, source.member_id) if source is not None else None
    if owner is None or owner.network_id != network_id:
        raise HTTPException(status_code=404, detail="System źródłowy nie znaleziony")
    if is_operator(principal):
        return import_obligations(db, network_id, payload)
    ensure_member_access(db, principal, owner.id, write=True)
    return import_obligations(db, network_id, payload, party_member_id=owner.id)


@router.get("", response_model=list[ObligationOut])
def list_obligations(
    network_id: str,
    member_id: str | None = None,
    role: str | None = None,
    status: ObligationStatus | None = None,
    db: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(get_current_principal),
):
    """Filtry: `member_id` (+ `role`=debtor|creditor, domyślnie obie
    strony), `status`. Portal uczestnika powinien zawsze podawać
    `member_id` - po dodaniu tokenów wymusi to backend."""
    get_network_or_404(db, network_id)
    query = select(Obligation).where(Obligation.network_id == network_id)
    # 2026-11-24: poza personelem tylko dokumenty, w których stroną jest
    # uczestnik wołającego (filtrowanie po stronie serwera, nie w portalu).
    if not is_staff(principal):
        own = own_member_ids(db, principal)
        if member_id and member_id not in own:
            raise forbidden()
        query = query.where(or_(Obligation.debtor_member_id.in_(own), Obligation.creditor_member_id.in_(own)))
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
def get_obligation(network_id: str, obligation_id: str, db: Session = Depends(get_db),
                   principal: CurrentPrincipal = Depends(get_current_principal)):
    obligation = get_obligation_or_404(db, obligation_id, network_id)
    _ensure_party(db, principal, obligation)
    return obligation


def _ensure_party(db: Session, principal: CurrentPrincipal, obligation: Obligation) -> None:
    """Personel albo uczestnik będący stroną dokumentu."""
    if is_staff(principal):
        return
    if not {obligation.debtor_member_id, obligation.creditor_member_id} & own_member_ids(db, principal):
        raise forbidden()


@router.post("/{obligation_id}/dispute", response_model=DisputeOut, status_code=201)
def dispute_obligation(network_id: str, obligation_id: str, payload: DisputeCreate, db: Session = Depends(get_db),
                      principal: CurrentPrincipal = Depends(get_current_principal)):
    """Spór (§7.6/§8.4) - dokument przechodzi w DISPUTED i wypada z nettingu."""
    obligation = get_obligation_or_404(db, obligation_id, network_id)
    # 2026-11-24: spór bez strony (raised_by_member_id) zakłada tylko operator;
    # uczestnik zgłasza w imieniu swojego uczestnika (stronę sprawdza serwis).
    if not is_operator(principal):
        if payload.raised_by_member_id is None:
            raise forbidden("Spór bez wskazania strony może założyć tylko operator")
        ensure_member_access(db, principal, payload.raised_by_member_id, write=True)
    try:
        return dispute_service.open_dispute(db, obligation, payload)
    except dispute_service.DisputeStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
