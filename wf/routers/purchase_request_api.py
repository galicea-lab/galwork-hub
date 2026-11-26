# wf/routers/purchase_request_api.py
# coding: utf-8
"""
2026-11-18: Wniosek zakupowy (zob. wf/models/wf.py::PurchaseRequest,
wf/services/purchase_request_service.py) - galwork-models-book,
obszary/dzialy/zakupy.md.

Tworzenie wniosku (`POST /purchase-requests`) - każdy pracownik
(2026-11-25: `require_staff`, wcześniej każdy zalogowany). Listę
i szczegóły widzi dział zamówień i administrator procesów w całości,
a pozostali - wnioski, których sprawę widzą (instance_access: własne,
w których uczestniczą). Dalszy przebieg (akceptacje) idzie
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
from wf.dependencies import get_current_wf_user, in_groups, is_process_admin, require_staff
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


# 2026-11-25: dział zamówień i administratorzy widzą wszystkie wnioski
def _sees_all(current_user: CurrentUser) -> bool:
    return is_process_admin(current_user) or in_groups(current_user, frozenset({_DZIAL_ZAKUPOW_GROUP}))


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
    current_user: CurrentUser = Depends(require_staff),
):
    from wf.services.instance_access import visible_clause
    visible = None if _sees_all(current_user) else visible_clause(current_user)
    rows, total = service.list_purchase_requests(db, contact_id=contact_id, limit=limit, offset=offset,
                                                 visible_instances=visible)
    return PurchaseRequestPage(items=rows, total=total)


@router.post("/purchase-requests", response_model=PurchaseRequestRead, status_code=201)
def create_purchase_request(
    payload: PurchaseRequestCreate,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_staff),
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
    current_user: CurrentUser = Depends(require_staff),
):
    try:
        row = service.get_purchase_request(db, purchase_request_id)
    except PurchaseRequestNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not _sees_all(current_user):
        from wf.services.instance_access import can_view
        if row.process_instance is None or not can_view(row.process_instance, current_user):
            raise HTTPException(status_code=403, detail="Brak uprawnień do tego wniosku")
    return row


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
