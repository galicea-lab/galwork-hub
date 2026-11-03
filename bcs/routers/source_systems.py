from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.dependencies import get_db
from bcs.api.lookups import get_member_or_404, get_network_or_404
from bcs.models.member import NetworkMember
from bcs.models.source_system import SourceSystem
from bcs.schemas.source_system import NetworkSourceSystemCreate, SourceSystemCreate, SourceSystemOut

router = APIRouter(tags=["source-systems"])


def _register(db: Session, member_id: str, payload: SourceSystemCreate) -> SourceSystem:
    source = SourceSystem(member_id=member_id, **payload.model_dump(exclude={"member_id"}))
    db.add(source)
    db.commit()
    db.refresh(source)
    return source


@router.get("/api/members/{member_id}/source-systems", response_model=list[SourceSystemOut])
def list_source_systems(member_id: str, db: Session = Depends(get_db)):
    get_member_or_404(db, member_id)
    return db.execute(select(SourceSystem).where(SourceSystem.member_id == member_id)).scalars().all()


@router.post("/api/members/{member_id}/source-systems", response_model=SourceSystemOut, status_code=201)
def register_source_system(member_id: str, payload: SourceSystemCreate, db: Session = Depends(get_db)):
    """Rejestracja integracji ERP uczestnika (§7.1). `source_system_id`
    z odpowiedzi jest wymagany przy imporcie zobowiązań."""
    get_member_or_404(db, member_id)
    return _register(db, member_id, payload)


@router.get("/api/networks/{network_id}/source-systems", response_model=list[SourceSystemOut])
def list_network_source_systems(network_id: str, db: Session = Depends(get_db)):
    get_network_or_404(db, network_id)
    return db.execute(
        select(SourceSystem)
        .join(NetworkMember, NetworkMember.id == SourceSystem.member_id)
        .where(NetworkMember.network_id == network_id)
    ).scalars().all()


@router.post("/api/networks/{network_id}/source-systems", response_model=SourceSystemOut, status_code=201)
def register_network_source_system(network_id: str, payload: NetworkSourceSystemCreate, db: Session = Depends(get_db)):
    """Adres z barter.md §7.6 - uczestnik wskazany w body."""
    get_member_or_404(db, payload.member_id, network_id)
    return _register(db, payload.member_id, payload)
