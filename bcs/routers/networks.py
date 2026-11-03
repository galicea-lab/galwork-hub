from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.dependencies import get_db
from bcs.api.lookups import get_network_or_404
from api.security import require_operator
from bcs.models.network import BarterNetwork
from bcs.schemas.network import NetworkCreate, NetworkOut, NetworkUpdate

router = APIRouter(prefix="/api/networks", tags=["networks"])


@router.get("", response_model=list[NetworkOut])
def list_networks(db: Session = Depends(get_db)):
    return db.execute(select(BarterNetwork).order_by(BarterNetwork.created_at.desc())).scalars().all()


@router.post("", response_model=NetworkOut, status_code=201, dependencies=[Depends(require_operator)])
def create_network(payload: NetworkCreate, db: Session = Depends(get_db)):
    if db.execute(select(BarterNetwork).where(BarterNetwork.code == payload.code)).scalars().first():
        raise HTTPException(status_code=409, detail=f"Sieć o kodzie {payload.code} już istnieje")
    network = BarterNetwork(**payload.model_dump())
    db.add(network)
    db.commit()
    db.refresh(network)
    return network


@router.get("/{network_id}", response_model=NetworkOut)
def get_network(network_id: str, db: Session = Depends(get_db)):
    return get_network_or_404(db, network_id)


@router.patch("/{network_id}", response_model=NetworkOut, dependencies=[Depends(require_operator)])
def update_network(network_id: str, payload: NetworkUpdate, db: Session = Depends(get_db)):
    """Konfiguracja i status sieci (DRAFT -> PILOT -> ACTIVE ...).
    TODO(bcs): walidacja dozwolonych przejść statusu i audit."""
    network = get_network_or_404(db, network_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(network, key, value)
    db.commit()
    db.refresh(network)
    return network
