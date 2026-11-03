from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.dependencies import get_db
from bcs.api.lookups import get_cycle_or_404
from api.security import require_operator
from connector.base import ConnectorError
from bcs.models.cycle import NetPosition
from bcs.models.settlement import SettlementBatch
from bcs.schemas.settlement import SettlementBatchOut
from bcs.services import settlement_service

router = APIRouter(tags=["settlement"])


@router.post(
    "/api/cycles/{cycle_id}/settle",
    response_model=SettlementBatchOut,
    dependencies=[Depends(require_operator)],
)
def settle_cycle(cycle_id: str, db: Session = Depends(get_db)):
    """Budowa batcha z pozycji netto i (próba) złożenia go w e-Talar
    (§10.4/§12.2). Dziś connector/etalar nie ma gdzie wysłać batcha (e-Talar
    bez /v3/settlement/*) - batch zostaje w statusie CREATED i endpoint
    zwraca 200 z batchem, informacja o nieudanej wysyłce idzie do logów
    (TODO(bcs): pole `last_error`/status 207 albo osobny endpoint retry,
    żeby to było widoczne dla operatora, nie tylko w logu)."""
    cycle = get_cycle_or_404(db, cycle_id)

    positions = db.execute(select(NetPosition).where(NetPosition.cycle_id == cycle_id)).scalars().all()
    try:
        batch = settlement_service.build_settlement_batch(db, cycle, positions)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    try:
        settlement_service.submit_batch_to_etalar(batch, batch.transfers)
    except ConnectorError as exc:
        # Oczekiwany stan tego szkicu (e-Talar bez /v3/settlement/*) - zob.
        # connector/etalar/etalar.py. Batch i transfery ZOSTAJĄ zapisane
        # (Phase 1: rozliczenie ręczne poza tym serwisem).
        import logging
        logging.getLogger(__name__).warning("Batch %s nie wysłany do e-Talar: %s", batch.id, exc)

    return batch


@router.get("/api/batches/{batch_id}", response_model=SettlementBatchOut)
def get_batch(batch_id: str, db: Session = Depends(get_db)):
    batch = db.get(SettlementBatch, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Batch nie znaleziony")
    return batch
