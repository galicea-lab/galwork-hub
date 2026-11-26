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

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, selectinload

from api.dependencies import get_db
from wf.dependencies import get_current_wf_user
from wf.models import (Document, DocumentVersion, HumanTask, HumanTaskCandidate, ProcessInstance, ProcessModel,
                       ProcessModelVersion)
from wf.schemas.wf_schemas import (
    CurrentUser,
    DocumentRead,
    HumanTaskRead,
    HumanTaskStatus,
    MyTaskItem,
    MyTaskSummary,
    Page,
    ProcessInstanceRead,
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
# 2026-11-25: reguła zadań bez przypisania
from wf.services.task_access import unassigned_visible_clause

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


# 2026-11-25: zapytanie skrzynki zadań wspólne dla listy i podsumowania
def _my_tasks_stmt(current_user: CurrentUser, *, only_claimed: bool = False, overdue: bool = False,
                   process_model_keys: list[str] | None = None):
    acting_ids = current_user.acting_user_ids
    principals = (tuple({current_user.username}
                        | set(current_user.groups)
                        | set(current_user.roles)) or ('',))

    stmt = select(HumanTask)
    # 2026-10-05: widoczność liczona w SQL (a nie po pobraniu listy), żeby
    # `total` zgadzało się z `items` mimo limit/offset.
    # 2026-11-25: zadanie bez kandydatów widzi ten, kto może je wykonać
    # (wf/services/task_access.py, ta sama reguła co ProcessService.authorize).
    candidate_match = HumanTask.candidates.any(
        HumanTaskCandidate.principal_value.in_(principals))
    unassigned_match = and_(~HumanTask.candidates.any(),
                            unassigned_visible_clause(current_user))
    stmt = stmt.where(
        or_(
            HumanTask.claimed_by_id.in_(acting_ids or (0,)),
            and_(HumanTask.status == HumanTaskStatus.OPEN,
                 or_(candidate_match, unassigned_match)),
        )
    )
    if only_claimed:
        stmt = stmt.where(HumanTask.claimed_by_id.in_(acting_ids or (0,)))
    if overdue:
        stmt = stmt.where(_overdue_clause())
    if process_model_keys:
        stmt = stmt.where(HumanTask.process_instance.has(ProcessInstance.version.has(
            ProcessModelVersion.process_model.has(ProcessModel.key.in_(process_model_keys)))))
    return stmt


def _overdue_clause():
    return and_(HumanTask.due_at.is_not(None), HumanTask.due_at < datetime.now(UTC))


def _count(db: Session, stmt) -> int:
    return db.execute(select(func.count()).select_from(stmt.order_by(None).subquery())).scalar_one()


@router.get("/my", response_model=Page[MyTaskItem])
def list_my_tasks(
    limit: int = 50,
    offset: int = 0,
    only_claimed: bool = False,
    overdue: bool = False,
    # 2026-11-25: zawężenie do obiegów (parametr można powtórzyć)
    process_model_key: list[str] | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_wf_user),
):
    """Skrzynka zadań: podjęte przez użytkownika (lub osobę, którą zastępuje)
    oraz otwarte, do których ma prawo (kandydaci/grupy/role z BPMN)."""
    acting_ids = current_user.acting_user_ids
    stmt = _my_tasks_stmt(current_user, only_claimed=only_claimed, overdue=overdue,
                          process_model_keys=process_model_key)
    # 2026-11-25: liczba wierszy w SQL zamiast pobierania wszystkich zadań
    total = _count(db, stmt)
    stmt = (stmt.options(*_WITH_CONTEXT)
            .order_by(HumanTask.priority.desc(), HumanTask.created_at).limit(limit).offset(offset))
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
            process_model_key=t.process_instance.version.process_model.key,
            business_key=t.process_instance.business_key,
            process_instance_lock_version=t.process_instance.lock_version,
        )
        for t in tasks
    ]
    return Page(items=items, total=total, limit=limit, offset=offset)


# 2026-11-25: liczniki skrzynki w jednym żądaniu - liczba zadań, po terminie
# i według obiegów (te same zadania co GET /tasks/my)
@router.get("/my/summary", response_model=MyTaskSummary)
def my_tasks_summary(db: Session = Depends(get_db),
                     current_user: CurrentUser = Depends(get_current_wf_user)):
    task_ids = _my_tasks_stmt(current_user).with_only_columns(HumanTask.id).subquery()
    rows = db.execute(
        select(ProcessModel.key, func.count(HumanTask.id),
               func.count(HumanTask.id).filter(_overdue_clause()))
        .join(task_ids, task_ids.c.id == HumanTask.id)
        .join(ProcessInstance, ProcessInstance.id == HumanTask.process_instance_id)
        .join(ProcessModelVersion, ProcessModelVersion.id == ProcessInstance.process_model_version_id)
        .join(ProcessModel, ProcessModel.id == ProcessModelVersion.process_model_id)
        .group_by(ProcessModel.key)
    ).all()
    return MyTaskSummary(total=sum(r[1] for r in rows), overdue=sum(r[2] for r in rows),
                         by_process_model={r[0]: r[1] for r in rows})


@router.get("/{task_id}", response_model=HumanTaskRead)
def get_task(task_id: UUID,
             db: Session = Depends(get_db),
             current_user: CurrentUser = Depends(get_current_wf_user)):
    # 2026-11-25: szczegóły zadania - tylko dla tego, kto widzi sprawę
    # (instance_access.can_view) albo może wykonać zadanie (authorize)
    task = _get_task_or_404(db, task_id)
    from wf.services.instance_access import can_view
    if can_view(task.process_instance, current_user):
        return task
    service = get_service()
    try:
        _, process_spec = service.load_workflow(task.process_instance)
        service.authorize(task, process_spec, current_user)
    except NotAuthorizedError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ProcessServiceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return task


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

    version = service.task_version(task)  # 2026-11-13: podproces z innego modelu
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
        # 2026-11-19: bez wartości pól ukrytych w kroku (x-permissions).
        form_data=service.visible_form_data(version, task, data),
        documents=_task_documents(db, task),
        process_lock_version=task.process_instance.lock_version,
        quick_actions=service.quick_actions(db, task, process_spec),  # 2026-11-13
    )


# BEGIN 2026-11-13
@router.post("/{task_id}/quick-actions/{process_model_key}",
             response_model=ProcessInstanceRead, status_code=201)
def run_task_quick_action(task_id: UUID, process_model_key: str,
                          db: Session = Depends(get_db),
                          current_user: CurrentUser = Depends(get_current_wf_user)):
    """Szybka akcja zadania (`quickAction` w BPMN): uruchamia osobną instancję
    modelu `process_model_key` z danymi wskazanymi w `startInput`, powiązaną
    z instancją zadania. Wymaga tych samych uprawnień co zadanie."""
    task = _get_task_or_404(db, task_id)
    service = get_service()
    try:
        child = service.run_quick_action(db, task, process_model_key, current_user)
    except NotAuthorizedError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ProcessServiceError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except FormRenderError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return child
# -END


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
