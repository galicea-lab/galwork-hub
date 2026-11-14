# coding: utf-8

"""
Router API dla instancji procesów (ProcessInstance).

2026-10-05:
  * start instancji uruchamia prawdziwy silnik BPMN (wf/engine.py +
    wf/services/process_service.py): powstaje ProcessInstance,
    ProcessInstanceState (stan silnika) i zadania w `human_task`;
  * zamiast `acting_user_id: UUID` w parametrze zapytania działa zależność
    `get_current_wf_user` - token Keycloak, więc klient nie może podać
    cudzego identyfikatora;
  * anulowanie/wstrzymanie/wznowienie idą przez ProcessService i zapisują
    zdarzenia w dzienniku `process_event`.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from api.dependencies import get_db
from wf.dependencies import get_current_wf_user
from wf.models import ProcessInstance, ProcessModel, ProcessModelVersion
from wf.schemas.wf_schemas import (
    CurrentUser,
    Page,
    ProcessInstanceAction,
    ProcessInstanceCreate,
    ProcessInstanceRead,
    ProcessInstanceStatus,
)
from wf.services.process_service import (
    InvalidTaskDataError,
    NotAuthorizedError,
    ProcessServiceError,
    get_service,
)

router = APIRouter(prefix="/process-instances", tags=["process-instances"])

_WITH_VERSION = selectinload(ProcessInstance.version).selectinload(ProcessModelVersion.process_model)


def _get_instance_or_404(db: Session, instance_id: UUID) -> ProcessInstance:
    stmt = select(ProcessInstance).options(_WITH_VERSION).where(ProcessInstance.id == instance_id)
    instance = db.execute(stmt).scalars().first()
    if instance is None:
        raise HTTPException(status_code=404, detail="Instancja procesu nie znaleziona")
    return instance


def _check_lock_version(instance: ProcessInstance, expected: int) -> None:
    """Kontrola kolizji (optymistyczna blokada w ProcessInstance.lock_version)."""
    if instance.lock_version != expected:
        raise HTTPException(
            status_code=409,
            detail=f"Konflikt wersji (lock_version={instance.lock_version}, "
                   f"oczekiwano {expected}) - odśwież dane i spróbuj ponownie",
        )


@router.get("", response_model=Page[ProcessInstanceRead])
def list_process_instances(
    limit: int = 50,
    offset: int = 0,
    status: ProcessInstanceStatus | None = None,
    process_model_key: str | None = None,
    parent_instance_id: UUID | None = None,  # 2026-11-13: instancje potomne
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_wf_user),
):
    stmt = select(ProcessInstance).options(_WITH_VERSION)
    if status is not None:
        stmt = stmt.where(ProcessInstance.status == status)
    if parent_instance_id is not None:
        stmt = stmt.where(ProcessInstance.parent_instance_id == parent_instance_id)
    if process_model_key is not None:
        stmt = (stmt.join(ProcessModelVersion)
                .join(ProcessModel)
                .where(ProcessModel.key == process_model_key))
    total = len(db.execute(stmt).scalars().all())
    stmt = stmt.order_by(ProcessInstance.started_at.desc()).limit(limit).offset(offset)
    items = db.execute(stmt).scalars().all()
    return Page(items=items, total=total, limit=limit, offset=offset)


@router.post("", response_model=ProcessInstanceRead, status_code=201)
def start_process_instance(payload: ProcessInstanceCreate,
                           db: Session = Depends(get_db),
                           current_user: CurrentUser = Depends(get_current_wf_user)):
    """Startuje instancję procesu w silniku BPMN i zapisuje jej stan."""
    service = get_service()
    try:
        version = service.published_version(db, payload.process_model_key)
        # 2026-11-13: dane z formularza startowego (startEvent) - walidacja
        # jak przy wykonaniu zadania; wewnętrzne starty (edr, instancje
        # potomne) idą bez niej przez ProcessService.start_instance.
        service.validate_start_data(version, payload.data)
        instance = service.start_instance(
            db, version,
            title=payload.title,
            business_key=payload.business_key,
            data=payload.data,
            actor=current_user,
        )
    except InvalidTaskDataError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except ProcessServiceError as exc:
        # Brak opublikowanej wersji procesu albo błędne pliki BPMN.
        raise HTTPException(status_code=409, detail=str(exc))
    return _get_instance_or_404(db, instance.id)


@router.get("/{instance_id}", response_model=ProcessInstanceRead)
def get_process_instance(instance_id: UUID,
                         db: Session = Depends(get_db),
                         current_user: CurrentUser = Depends(get_current_wf_user)):
    return _get_instance_or_404(db, instance_id)


# BEGIN 2026-11-13
@router.post("/{instance_id}/wake", response_model=ProcessInstanceRead)
def wake_process_instance(instance_id: UUID,
                          db: Session = Depends(get_db),
                          current_user: CurrentUser = Depends(get_current_wf_user)):
    """Ponownie wykonuje czekające kroki systemowe (serviceTask z `handler`),
    np. sprawdza status podpisu w Signius - zamiast czekać na cron
    (scripts/wf_wake.py)."""
    instance = _get_instance_or_404(db, instance_id)
    try:
        get_service().wake(db, instance, current_user)
    except ProcessServiceError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return _get_instance_or_404(db, instance_id)
# -END


@router.post("/{instance_id}/cancel", response_model=ProcessInstanceRead)
def cancel_process_instance(instance_id: UUID, payload: ProcessInstanceAction,
                            db: Session = Depends(get_db),
                            current_user: CurrentUser = Depends(get_current_wf_user)):
    instance = _get_instance_or_404(db, instance_id)
    _check_lock_version(instance, payload.expected_lock_version)
    if instance.status == ProcessInstanceStatus.CANCELLED:
        return instance
    try:
        get_service().cancel(db, instance, current_user, payload.reason)
    except ProcessServiceError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return _get_instance_or_404(db, instance_id)


@router.post("/{instance_id}/suspend", response_model=ProcessInstanceRead)
def suspend_process_instance(instance_id: UUID, payload: ProcessInstanceAction,
                             db: Session = Depends(get_db),
                             current_user: CurrentUser = Depends(get_current_wf_user)):
    instance = _get_instance_or_404(db, instance_id)
    _check_lock_version(instance, payload.expected_lock_version)
    if instance.status not in (ProcessInstanceStatus.RUNNING, ProcessInstanceStatus.WAITING):
        raise HTTPException(
            status_code=409,
            detail=f"Nie można wstrzymać instancji o statusie {instance.status}",
        )
    try:
        get_service().set_suspended(db, instance, current_user, payload.reason)
    except ProcessServiceError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return _get_instance_or_404(db, instance_id)


@router.post("/{instance_id}/resume", response_model=ProcessInstanceRead)
def resume_process_instance(instance_id: UUID, payload: ProcessInstanceAction,
                            db: Session = Depends(get_db),
                            current_user: CurrentUser = Depends(get_current_wf_user)):
    instance = _get_instance_or_404(db, instance_id)
    _check_lock_version(instance, payload.expected_lock_version)
    if instance.status != ProcessInstanceStatus.SUSPENDED:
        raise HTTPException(
            status_code=409,
            detail=f"Instancja nie jest wstrzymana (status={instance.status})",
        )
    try:
        get_service().resume(db, instance, current_user)
    except ProcessServiceError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except NotAuthorizedError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    return _get_instance_or_404(db, instance_id)
