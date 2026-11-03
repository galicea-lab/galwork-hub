# wf/routers/purchase_request_api.py
# coding: utf-8
"""
Wniosek zakupowy (zob. wf/models/wf.py::PurchaseRequest,
wf/services/purchase_request_service.py) - galwork-book,
czesc-3-obszary/dzialy/zakupy.md.

Tworzenie wniosku (`POST /purchase-requests`) wymaga tylko zalogowania -
każdy pracownik może złożyć wniosek. Dalszy przebieg (akceptacje) idzie
przez generyczny `wf/routers/human_task_router.py` (`GET/POST
/tasks/...`) - nie ma tu osobnych endpointów na "zatwierdź"/"odrzuć",
bo `wniosek-zakupowy.bpmn` wstrzykuje te decyzje automatycznie (zob.
wf/decisions.py) do formularza zadania zatwierdzającego.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from api.dependencies import get_db
from wf.dependencies import get_current_wf_user
from wf.schemas.wf_schemas import CurrentUser, PurchaseRequestCreate, PurchaseRequestPage, PurchaseRequestRead
from wf.services import purchase_request_service as service
from wf.services.process_service import ProcessServiceError
from wf.services.purchase_request_service import PurchaseRequestNotFoundError

router = APIRouter(tags=["purchase-requests"])

# 2026-10-20: ten sam literał co keycloak_group `OrgUnit` "Dział zamówień"
# (scripts/seed_zakupy.py) i `candidateGroups` w wniosek-zakupowy.bpmn -
# startowanie procedury PZP jest decyzją działu zamówień, nie każdego
# zalogowanego (wzorowane na require_invoice_approver w erpnext_invoice_api.py).
_DZIAL_ZAKUPOW_GROUP = "dzial_zakupow"


def require_dzial_zakupow(current_user: CurrentUser = Depends(get_current_wf_user)) -> CurrentUser:
    principals = {v.lstrip("/").split("/")[-1].lower() for v in current_user.groups | current_user.roles}
    if _DZIAL_ZAKUPOW_GROUP not in principals:
        raise HTTPException(
            status_code=403,
            detail=f"Wymagana grupa: {_DZIAL_ZAKUPOW_GROUP}",
        )
    return current_user


@router.get("/purchase-requests", response_model=PurchaseRequestPage)
def list_purchase_requests(
    contact_id: UUID | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _current_user: CurrentUser = Depends(get_current_wf_user),
):
    rows, total = service.list_purchase_requests(db, contact_id=contact_id, limit=limit, offset=offset)
    return PurchaseRequestPage(items=rows, total=total)


@router.post("/purchase-requests", response_model=PurchaseRequestRead, status_code=201)
def create_purchase_request(
    payload: PurchaseRequestCreate,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_wf_user),
):
    try:
        return service.create_and_start(db, payload, actor=current_user)
    except PurchaseRequestNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except service.PurchaseRequestConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ProcessServiceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/purchase-requests/{purchase_request_id}", response_model=PurchaseRequestRead)
def get_purchase_request(
    purchase_request_id: UUID,
    db: Session = Depends(get_db),
    _current_user: CurrentUser = Depends(get_current_wf_user),
):
    try:
        return service.get_purchase_request(db, purchase_request_id)
    except PurchaseRequestNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/purchase-requests/{purchase_request_id}/start-tender", response_model=PurchaseRequestRead)
def start_tender(
    purchase_request_id: UUID,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_dzial_zakupow),
):
    """Startuje obieg `zamowienie-publiczne` dla wniosku, dla którego reguła
    akceptacji wyliczyła `requires_tender=True` (zob. docstring
    purchase_request_service.start_tender)."""
    try:
        return service.start_tender(db, purchase_request_id, actor=current_user)
    except PurchaseRequestNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ProcessServiceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
