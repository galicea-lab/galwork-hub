# coding: utf-8

"""
Router API dla zadań użytkowników (HumanTask) - skrzynka "Moje zadania",
podjęcie (claim) i wykonanie (complete) zadania.

2026-10-05:
  * tożsamość pochodzi z tokenu Keycloak (`get_current_wf_user`), a nie
    z parametru `acting_user_id` w zapytaniu;
  * `complete` wykonuje zadanie w SpiffWorkflow (wf/engine.py), zapisuje stan
    instancji, synchronizuje kolejne zadania i wpisuje zdarzenie w dzienniku
    (`wf/services/process_service.py`);
  * widoczność zadań uwzględnia kandydatów z BPMN (HumanTaskCandidate),
    role/grupy z Keycloak oraz aktywne zastępstwa (UserSubstitution) przez
    `CurrentUser.acting_user_ids`.

2026-10-06: `GET /tasks/{task_id}/form` zwraca formularz zadania
(JSON Schema + uiSchema + dane wstępne + `process_lock_version`) - ten sam
schemat, który później waliduje `complete`.

2026-10-15:  `GET .../form` i walidacja w `complete` korzystają
teraz z `ProcessService.task_form()` (wf/services/process_service.py),
który filtruje pola wg `x-permissions[nazwa_kroku]` (usuwa "none", ustawia
`readOnly` dla "readonly") i buforuje wynik filtrowania w Redis
(core/redis_cache.py) pod kluczem (content_hash wersji, plik schematu, id
zadania BPMN) - patrz wf/services/form_permissions.py. Formularze bez
`x-permissions` (Spiff-Arena/Jinja) działają jak dotąd, bez filtrowania i
bez cache'a.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session, selectinload

from api.dependencies import get_db
from wf.dependencies import get_current_wf_user
from wf.models import Document, DocumentVersion, HumanTask, HumanTaskCandidate, ProcessInstance, ProcessModelVersion
from wf.schemas.wf_schemas import (
    CurrentUser,
    DocumentRead,
    HumanTaskRead,
    HumanTaskStatus,
    MyTaskItem,
    Page,
    TaskClaimRequest,
    TaskCompleteRequest,
    TaskFormRead,
)
from wf.services.process_service import (
    FormRenderError,
    InvalidTaskDataError,
    NotAuthorizedError,
    ProcessServiceError,
    get_service,
)

router = APIRouter(prefix="/tasks", tags=["tasks"])

_WITH_CONTEXT = (
    selectinload(HumanTask.candidates),
    selectinload(HumanTask.claimed_by),
    selectinload(HumanTask.completed_by),
    selectinload(HumanTask.completed_on_behalf_of),
    selectinload(HumanTask.process_instance)
        .selectinload(ProcessInstance.version)
        .selectinload(ProcessModelVersion.process_model),
)


def _get_task_or_404(db: Session, task_id: UUID) -> HumanTask:
    stmt = select(HumanTask).options(*_WITH_CONTEXT).where(HumanTask.id == task_id)
    task = db.execute(stmt).scalars().first()
    if task is None:
        raise HTTPException(status_code=404, detail="Zadanie nie znalezione")
    return task


def _task_documents(db: Session, task: HumanTask) -> list[DocumentRead]:
    """Dokumenty podpięte pod zadanie (DocumentVersion.human_task_id)."""
    stmt = (
        select(Document)
        .join(DocumentVersion, DocumentVersion.document_id == Document.id)
        .where(DocumentVersion.human_task_id == task.id)
        .options(selectinload(Document.created_by),
                 selectinload(Document.current_version)
                 .selectinload(DocumentVersion.uploaded_by))
        .distinct()
    )
    return [DocumentRead.model_validate(document)
            for document in db.execute(stmt).scalars().all()]


@router.get("/my", response_model=Page[MyTaskItem])
def list_my_tasks(
    limit: int = 50,
    offset: int = 0,
    only_claimed: bool = False,
    overdue: bool = False,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_wf_user),
):
    """Skrzynka zadań: podjęte przez użytkownika (lub osobę, którą zastępuje)
    oraz otwarte, do których ma prawo (kandydaci/grupy/role z BPMN)."""
    acting_ids = current_user.acting_user_ids
    principals = (tuple({current_user.username}
                        | set(current_user.groups)
                        | set(current_user.roles)) or ('',))

    stmt = select(HumanTask).options(*_WITH_CONTEXT)
    # 2026-10-05: widoczność liczona w SQL (a nie po pobraniu listy), żeby
    # `total` zgadzało się z `items` mimo limit/offset. Zadanie bez kandydatów
    # jest widoczne dla każdego - tak samo decyduje TaskAssignment.allows()
    # w wf/engine.py.
    candidate_match = HumanTask.candidates.any(
        HumanTaskCandidate.principal_value.in_(principals))
    stmt = stmt.where(
        or_(
            HumanTask.claimed_by_id.in_(acting_ids or (0,)),
            and_(HumanTask.status == HumanTaskStatus.OPEN,
                 or_(candidate_match, ~HumanTask.candidates.any())),
        )
    )
    if only_claimed:
        stmt = stmt.where(HumanTask.claimed_by_id.in_(acting_ids or (0,)))
    if overdue:
        stmt = stmt.where(HumanTask.due_at.is_not(None), HumanTask.due_at < datetime.now(UTC))

    total = len(db.execute(stmt).scalars().all())
    stmt = stmt.order_by(HumanTask.priority.desc(), HumanTask.created_at).limit(limit).offset(offset)
    tasks = db.execute(stmt).scalars().all()

    items = [
        MyTaskItem(
            id=t.id,
            name=t.name,
            status=t.status,
            priority=t.priority,
            due_at=t.due_at,
            created_at=t.created_at,
            claimed_by_me=(t.claimed_by_id in acting_ids),
            on_behalf_of=None,
            process_instance_id=t.process_instance_id,
            process_title=t.process_instance.title,
            process_model_name=t.process_instance.version.process_model.name,
            business_key=t.process_instance.business_key,
            process_instance_lock_version=t.process_instance.lock_version,
        )
        for t in tasks
    ]
    return Page(items=items, total=total, limit=limit, offset=offset)


@router.get("/{task_id}", response_model=HumanTaskRead)
def get_task(task_id: UUID,
             db: Session = Depends(get_db),
             current_user: CurrentUser = Depends(get_current_wf_user)):
    return _get_task_or_404(db, task_id)


@router.get("/{task_id}/form", response_model=TaskFormRead)
def get_task_form(task_id: UUID,
                  db: Session = Depends(get_db),
                  current_user: CurrentUser = Depends(get_current_wf_user)):
    """Formularz zadania: JSON Schema + uiSchema (react-jsonschema-form).

    Szablony Jinja w schematach serwer renderuje danymi zadania (Spiff-Arena),
    `form_data` to dane wstępne do wypełnienia, a `process_lock_version` jest
    potrzebne do `complete`. Brak schematu = pusty `json_schema` i klient
    pokazuje zapasowy edytor JSON."""
    task = _get_task_or_404(db, task_id)
    service = get_service()
    try:
        workflow, process_spec = service.load_workflow(task.process_instance)
        service.authorize(task, process_spec, current_user)
    except NotAuthorizedError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ProcessServiceError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    version = task.process_instance.version
    data = service.task_form_data(workflow, task)
    try:
        schema, ui_schema = service.task_form(version, task, data)
    except FormRenderError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    return TaskFormRead(
        task_id=task.id,
        task_name=task.name,
        json_schema=schema,
        ui_schema=ui_schema,
        form_data=data,
        documents=_task_documents(db, task),
        process_lock_version=task.process_instance.lock_version,
    )


@router.post("/{task_id}/claim", response_model=HumanTaskRead)
def claim_task(task_id: UUID, payload: TaskClaimRequest,
               db: Session = Depends(get_db),
               current_user: CurrentUser = Depends(get_current_wf_user)):
    """Podjęcie zadania przez zalogowanego użytkownika."""
    task = _get_task_or_404(db, task_id)
    if task.process_instance.lock_version != payload.expected_lock_version:
        raise HTTPException(status_code=409, detail="Konflikt wersji instancji procesu")
    service = get_service()
    try:
        _, process_spec = service.load_workflow(task.process_instance)
        service.claim_task(db, task, current_user, process_spec)
    except NotAuthorizedError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ProcessServiceError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return _get_task_or_404(db, task_id)


@router.post("/{task_id}/complete", response_model=HumanTaskRead)
def complete_task(task_id: UUID, payload: TaskCompleteRequest,
                  db: Session = Depends(get_db),
                  current_user: CurrentUser = Depends(get_current_wf_user)):
    """Wykonuje zadanie: waliduje dane, wykonuje krok w SpiffWorkflow,
    zapisuje stan instancji i otwiera kolejne zadania."""
    task = _get_task_or_404(db, task_id)
    if task.process_instance.lock_version != payload.expected_lock_version:
        raise HTTPException(status_code=409, detail="Konflikt wersji instancji procesu")
    if task.status not in (HumanTaskStatus.OPEN, HumanTaskStatus.CLAIMED):
        raise HTTPException(status_code=409, detail=f"Zadanie nie jest aktywne (status={task.status})")

    try:
        get_service().complete_task(db, task, current_user, payload.data)
    except NotAuthorizedError as exc:
        db.rollback()
        raise HTTPException(status_code=403, detail=str(exc))
    except InvalidTaskDataError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc))
    except ProcessServiceError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc))
    return _get_task_or_404(db, task_id)
