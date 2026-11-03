from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.dependencies import get_db
from bcs.api.lookups import get_organization_or_404
from api.security import require_operator
from bcs.models.member import Organization
from bcs.schemas.member import OrganizationCreate, OrganizationOut, OrganizationUpdate

router = APIRouter(prefix="/api/organizations", tags=["organizations"])


@router.get("", response_model=list[OrganizationOut])
def list_organizations(tax_id: str | None = None, db: Session = Depends(get_db)):
    query = select(Organization).order_by(Organization.legal_name)
    if tax_id:
        query = query.where(Organization.tax_id == tax_id)
    return db.execute(query).scalars().all()


@router.post("", response_model=OrganizationOut, status_code=201)
def create_organization(payload: OrganizationCreate, db: Session = Depends(get_db)):
    """Rejestracja podmiotu prawnego - BCS jest tu źródłem prawdy (zob.
    models/member.py - Organization NIE istnieje jeszcze ani w galwork-hub,
    ani w e-Talar)."""
    org = Organization(**payload.model_dump())
    db.add(org)
    db.commit()
    db.refresh(org)
    return org


@router.get("/{organization_id}", response_model=OrganizationOut)
def get_organization(organization_id: str, db: Session = Depends(get_db)):
    return get_organization_or_404(db, organization_id)


@router.patch("/{organization_id}", response_model=OrganizationOut, dependencies=[Depends(require_operator)])
def update_organization(organization_id: str, payload: OrganizationUpdate, db: Session = Depends(get_db)):
    """M.in. wynik KYB (`kyb_status`) - warunek aktywacji członkostwa
    (services/member_service.py::activate_member).
    TODO(bcs): wpis do AuditEvent i four-eyes przy zmianie KYB (§14.2)."""
    org = get_organization_or_404(db, organization_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(org, key, value)
    db.commit()
    db.refresh(org)
    return org
