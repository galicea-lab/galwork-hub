from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.dependencies import get_db
from bcs.api.lookups import get_cycle_or_404 as _get_cycle_or_404, get_network_or_404
from api.security import CurrentPrincipal, get_current_principal, require_operator
from bcs.api.access import ensure_network_access, is_staff, own_member_ids, require_staff
from bcs.models.cycle import ClearingCycle, NetPosition
from bcs.models.settlement import SettlementBatch
from bcs.schemas.cycle import CycleCreate, CycleOut, NetPositionOut
from bcs.schemas.settlement import SettlementBatchOut
from bcs.services import cycle_service

router = APIRouter(tags=["cycles"])


@router.post(
    "/api/networks/{network_id}/cycles",
    response_model=CycleOut,
    status_code=201,
    dependencies=[Depends(require_operator)],
)
def open_cycle(network_id: str, payload: CycleCreate, db: Session = Depends(get_db)):
    get_network_or_404(db, network_id)
    if payload.period_end <= payload.period_start:
        raise HTTPException(status_code=422, detail="Koniec okresu musi być po jego początku")
    return cycle_service.open_cycle(db, network_id, payload)


@router.get("/api/networks/{network_id}/cycles", response_model=list[CycleOut])
def list_cycles(network_id: str, db: Session = Depends(get_db),
                principal: CurrentPrincipal = Depends(get_current_principal)):
    get_network_or_404(db, network_id)
    ensure_network_access(db, principal, network_id)
    return db.execute(
        select(ClearingCycle)
        .where(ClearingCycle.network_id == network_id)
        .order_by(ClearingCycle.sequence_no.desc())
    ).scalars().all()


@router.get("/api/cycles/{cycle_id}", response_model=CycleOut)
def get_cycle(cycle_id: str, db: Session = Depends(get_db),
              principal: CurrentPrincipal = Depends(get_current_principal)):
    cycle = _get_cycle_or_404(db, cycle_id)
    ensure_network_access(db, principal, cycle.network_id)
    return cycle


@router.post("/api/cycles/{cycle_id}/close", response_model=CycleOut, dependencies=[Depends(require_operator)])
def close_cycle(cycle_id: str, db: Session = Depends(get_db)):
    """Cutoff i blokada (§8.3) - TODO(bcs): okno sporów (`dispute_window_end`)
    nie jest tu jeszcze wymuszane (np. blokada /net przed upływem okna)."""
    cycle = _get_cycle_or_404(db, cycle_id)
    try:
        return cycle_service.close_cycle(db, cycle)
    except cycle_service.CycleStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post("/api/cycles/{cycle_id}/net", response_model=list[NetPositionOut], dependencies=[Depends(require_operator)])
def net_cycle(cycle_id: str, db: Session = Depends(get_db)):
    """Obliczenie pozycji netto (§9) - zob. services/cycle_service.py."""
    cycle = _get_cycle_or_404(db, cycle_id)
    try:
        return cycle_service.compute_netting(db, cycle)
    except cycle_service.CycleStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.get("/api/cycles/{cycle_id}/positions", response_model=list[NetPositionOut])
def get_positions(cycle_id: str, db: Session = Depends(get_db),
                  principal: CurrentPrincipal = Depends(get_current_principal)):
    cycle = _get_cycle_or_404(db, cycle_id)
    ensure_network_access(db, principal, cycle.network_id)
    query = select(NetPosition).where(NetPosition.cycle_id == cycle_id)
    # 2026-11-24: uczestnik widzi tylko pozycje własnych uczestników
    if not is_staff(principal):
        query = query.where(NetPosition.member_id.in_(own_member_ids(db, principal)))
    return db.execute(query).scalars().all()


@router.get("/api/cycles/{cycle_id}/batches", response_model=list[SettlementBatchOut],
            dependencies=[Depends(require_staff)])
def get_cycle_batches(cycle_id: str, db: Session = Depends(get_db)):
    """Batche settlementowe cyklu, najnowszy pierwszy."""
    _get_cycle_or_404(db, cycle_id)
    return db.execute(
        select(SettlementBatch)
        .where(SettlementBatch.cycle_id == cycle_id)
        .order_by(SettlementBatch.created_at.desc())
    ).scalars().all()
