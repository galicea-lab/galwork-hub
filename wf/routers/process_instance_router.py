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

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.orm import Session, selectinload

from api.dependencies import get_db
from wf.dependencies import ACT_REPOSITORY_MODEL_KEY, can_create_act, get_current_wf_user
from wf.models import ProcessInstance, ProcessModel, ProcessModelVersion
from wf.schemas.wf_schemas import (
    CurrentUser,
    Page,
    ProcessInstanceAction,
    ProcessInstanceCreate,
    ProcessInstanceRead,
    ProcessInstanceRecordRead,
    ProcessInstanceStatus,
)
from wf.services import instance_access, instance_record, rollouts
from wf.services.process_service import (
    InvalidTaskDataError,
    NotAuthorizedError,
    ProcessServiceError,
    get_service,
)

router = APIRouter(prefix="/process-instances", tags=["process-instances"])

_WITH_VERSION = selectinload(ProcessInstance.version).selectinload(ProcessModelVersion.process_model)


def _get_instance_or_404(db: Session, instance_id: UUID, user: CurrentUser, *,
                         write: bool = False) -> ProcessInstance:
    # 2026-11-22: 403, gdy użytkownik nie widzi sprawy (instance_access.can_view);
    # `write=True` - działanie na sprawie (rejestr jawny nie wystarcza)
    return instance_access.get_visible_instance(db, instance_id, user, _WITH_VERSION, write=write)


def _check_lock_version(instance: ProcessInstance, expected: int) -> None:
    """Kontrola kolizji (optymistyczna blokada w ProcessInstance.lock_version)."""
    if instance.lock_version != expected:
        raise HTTPException(
            status_code=409,
            detail=f"Konflikt wersji (lock_version={instance.lock_version}, "
                   f"oczekiwano {expected}) - odśwież dane i spróbuj ponownie",
        )


# 2026-11-22 (H2): sortowanie listy - pole albo `m.<klucz>` metadanych, `-` = malejąco
_SORT_COLUMNS = {
    'started_at': ProcessInstance.started_at,
    'updated_at': ProcessInstance.updated_at,
    'completed_at': ProcessInstance.completed_at,
    'title': ProcessInstance.title,
    'business_key': ProcessInstance.business_key,
    'status': ProcessInstance.status,
}
_META_PREFIX = 'm.'


def _sort_clause(sort: str):
    descending = sort.startswith('-')
    name = sort[1:] if descending else sort
    if name.startswith(_META_PREFIX) and len(name) > len(_META_PREFIX):
        column = ProcessInstance.extracted_metadata[name[len(_META_PREFIX):]].as_string()
    elif name in _SORT_COLUMNS:
        column = _SORT_COLUMNS[name]
    else:
        raise HTTPException(status_code=422, detail=(
            f"Nieznane sortowanie '{sort}' - dozwolone: {', '.join(sorted(_SORT_COLUMNS))} "
            f"albo m.<klucz metadanych>, z '-' dla kolejności malejącej"))
    column = column.desc() if descending else column.asc()
    return column.nulls_last()


@router.get("", response_model=Page[ProcessInstanceRead])
def list_process_instances(
    request: Request,
    limit: int = 50,
    offset: int = 0,
    status: ProcessInstanceStatus | None = None,
    process_model_key: str | None = None,
    parent_instance_id: UUID | None = None,  # 2026-11-13: instancje potomne
    q: str | None = None,
    sort: str = '-started_at',
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_wf_user),
):
    """Lista spraw widocznych dla użytkownika.

    2026-11-22 (H2, custom_dashboard2.md §4.1): `q` - fragment tytułu,
    `business_key` albo metadanych (bez rozróżniania wielkości liter);
    `m.<klucz>=wartość` - równość wartości metadanej (porównanie tekstowe,
    parametr można powtórzyć dla kilku kluczy); `sort` - zob. `_SORT_COLUMNS`
    i `m.<klucz>`. `metadata` w odpowiedzi to kolumny z `metadata_extraction_paths`.
    """
    stmt = select(ProcessInstance).options(_WITH_VERSION)
    # 2026-11-22: tylko instancje widoczne dla użytkownika (wf/services/instance_access.py)
    visible = instance_access.visible_clause(current_user)
    if visible is not None:
        stmt = stmt.where(visible)
    if status is not None:
        stmt = stmt.where(ProcessInstance.status == status)
    if parent_instance_id is not None:
        stmt = stmt.where(ProcessInstance.parent_instance_id == parent_instance_id)
    if process_model_key is not None:
        stmt = (stmt.join(ProcessModelVersion)
                .join(ProcessModel)
                .where(ProcessModel.key == process_model_key))
    if q and q.strip():
        pattern = f"%{q.strip().lower()}%"
        stmt = stmt.where(or_(
            func.lower(ProcessInstance.title).like(pattern),
            func.lower(func.coalesce(ProcessInstance.business_key, '')).like(pattern),
            func.lower(cast(ProcessInstance.extracted_metadata, String)).like(pattern)))
    for name, value in request.query_params.multi_items():
        if name.startswith(_META_PREFIX) and len(name) > len(_META_PREFIX):
            stmt = stmt.where(
                ProcessInstance.extracted_metadata[name[len(_META_PREFIX):]].as_string() == value)
    order = _sort_clause(sort)
    total = db.execute(select(func.count()).select_from(stmt.order_by(None).subquery())).scalar_one()
    stmt = stmt.order_by(order, ProcessInstance.started_at.desc()).limit(limit).offset(offset)
    items = db.execute(stmt).scalars().all()
    return Page(items=items, total=total, limit=limit, offset=offset)


@router.post("", response_model=ProcessInstanceRead, status_code=201)
def start_process_instance(payload: ProcessInstanceCreate,
                           db: Session = Depends(get_db),
                           current_user: CurrentUser = Depends(get_current_wf_user)):
    """Startuje instancję procesu w silniku BPMN i zapisuje jej stan."""
    # dodawanie aktu do repozytorium wymaga grupy z
    # [wf] act_editor_groups albo grupy Keycloak `act_editor`.
    if payload.process_model_key == ACT_REPOSITORY_MODEL_KEY and not can_create_act(current_user):
        raise HTTPException(status_code=403, detail='Brak uprawnień do dodawania aktów')
    service = get_service()
    try:
        version = service.published_version(db, payload.process_model_key)
        # 2026-11-13: dane z formularza startowego (startEvent) - walidacja
        # jak przy wykonaniu zadania; wewnętrzne starty (edr, instancje
        # potomne) idą bez niej przez ProcessService.start_instance.
        service.validate_start_data(version, payload.data)
        # 2026-11-17: wdrożenie obiegu w jednostce użytkownika ([wf] rollout_enforcement)
        decision = rollouts.decide_start(db, version, current_user)
        if not decision.allowed:
            raise HTTPException(status_code=403, detail=decision.warning)
        instance = service.start_instance(
            db, version, rollout_decision=decision,
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
    return _get_instance_or_404(db, instance.id, current_user)


@router.get("/{instance_id}", response_model=ProcessInstanceRead)
def get_process_instance(instance_id: UUID,
                         db: Session = Depends(get_db),
                         current_user: CurrentUser = Depends(get_current_wf_user)):
    return _get_instance_or_404(db, instance_id, current_user)


# 2026-11-22 (H4): karta obiektu - dane w formularzu kroku `galwork.record_step`
# tylko do odczytu (wf/services/instance_record.py); odczyt jak szczegóły sprawy.
@router.get("/{instance_id}/record", response_model=ProcessInstanceRecordRead)
def get_process_instance_record(instance_id: UUID,
                                db: Session = Depends(get_db),
                                current_user: CurrentUser = Depends(get_current_wf_user)):
    from wf.services.process_service import FormRenderError
    from wf.services.print_service import PrintError
    instance = _get_instance_or_404(db, instance_id, current_user)
    try:
        return instance_record.record(get_service(), instance, current_user)
    except FormRenderError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (ProcessServiceError, PrintError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


# BEGIN 2026-11-13
@router.post("/{instance_id}/wake", response_model=ProcessInstanceRead)
def wake_process_instance(instance_id: UUID,
                          db: Session = Depends(get_db),
                          current_user: CurrentUser = Depends(get_current_wf_user)):
    """Ponownie wykonuje czekające kroki systemowe (serviceTask z `handler`),
    np. sprawdza status podpisu w Signius - zamiast czekać na cron
    (scripts/wf_wake.py)."""
    instance = _get_instance_or_404(db, instance_id, current_user, write=True)
    try:
        get_service().wake(db, instance, current_user)
    except ProcessServiceError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return _get_instance_or_404(db, instance_id, current_user)
# -END


@router.post("/{instance_id}/cancel", response_model=ProcessInstanceRead)
def cancel_process_instance(instance_id: UUID, payload: ProcessInstanceAction,
                            db: Session = Depends(get_db),
                            current_user: CurrentUser = Depends(get_current_wf_user)):
    instance = _get_instance_or_404(db, instance_id, current_user, write=True)
    _check_lock_version(instance, payload.expected_lock_version)
    if instance.status == ProcessInstanceStatus.CANCELLED:
        return instance
    try:
        get_service().cancel(db, instance, current_user, payload.reason)
    except ProcessServiceError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return _get_instance_or_404(db, instance_id, current_user)


@router.post("/{instance_id}/suspend", response_model=ProcessInstanceRead)
def suspend_process_instance(instance_id: UUID, payload: ProcessInstanceAction,
                             db: Session = Depends(get_db),
                             current_user: CurrentUser = Depends(get_current_wf_user)):
    instance = _get_instance_or_404(db, instance_id, current_user, write=True)
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
    return _get_instance_or_404(db, instance_id, current_user)


@router.post("/{instance_id}/resume", response_model=ProcessInstanceRead)
def resume_process_instance(instance_id: UUID, payload: ProcessInstanceAction,
                            db: Session = Depends(get_db),
                            current_user: CurrentUser = Depends(get_current_wf_user)):
    instance = _get_instance_or_404(db, instance_id, current_user, write=True)
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
    return _get_instance_or_404(db, instance_id, current_user)
