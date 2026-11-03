from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from api.dependencies import get_db
from bcs.api.lookups import get_cycle_or_404, get_member_or_404
from bcs.schemas.statement import CycleStatementOut, MemberStatementOut
from bcs.services import statement_service

router = APIRouter(tags=["statements"])


@router.get("/api/cycles/{cycle_id}/statement", response_model=CycleStatementOut)
def get_cycle_statement(cycle_id: str, db: Session = Depends(get_db)):
    """Statement sieci za cykl (§15.2)."""
    cycle = get_cycle_or_404(db, cycle_id)
    try:
        return statement_service.cycle_statement(db, cycle)
    except statement_service.StatementNotReady as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.get("/api/members/{member_id}/statement", response_model=MemberStatementOut)
def get_member_statement(member_id: str, cycle_id: str | None = None, db: Session = Depends(get_db)):
    """Statement uczestnika (§15.1) za `cycle_id`, domyślnie za ostatni
    cykl z policzonym nettingiem."""
    member = get_member_or_404(db, member_id)
    if cycle_id:
        cycle = get_cycle_or_404(db, cycle_id)
        if cycle.network_id != member.network_id:
            raise HTTPException(status_code=404, detail="Cykl nie znaleziony")
    else:
        cycle = statement_service.latest_netted_cycle(db, member.network_id)
        if cycle is None:
            raise HTTPException(status_code=409, detail="Sieć nie ma jeszcze cyklu z policzonym nettingiem")
    try:
        return statement_service.member_statement(db, member, cycle)
    except statement_service.StatementNotReady as exc:
        raise HTTPException(status_code=409, detail=str(exc))
