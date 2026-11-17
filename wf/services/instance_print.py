# coding: utf-8
# 2026-11-16: wydruki PDF instancji procesu - wybór szablonu, dane,
# uprawnienia i archiwizacja w Paperless (TODO-wydruki.md, kroki 1 i 3).
"""
Wydruk instancji procesu albo konkretnego zadania.

  * dane: jak w formularzu zadania (`ProcessService.task_form_data`); bez
    `task_id` - najnowsze zadanie instancji, a gdy instancja nie ma zadań -
    dane procesu,
  * dostępne wydruki: wpisy `x-print` pasujące do zadania i danych
    (wf/services/print_service.py) + zawsze wydruk standardowy,
  * uprawnienia: administrator procesów, osoba, która uruchomiła instancję,
    oraz osoby związane z jej zadaniami (kandydat, podejmujący, wykonujący),
  * archiwizacja (na żądanie): `Document` + `DocumentVersion` + powiązanie
    z instancją (`main`/`attachment`) i wysyłka do Paperless-ngx; gdy
    Paperless chwilowo nie odpowiada, wersja zostaje w stanie `staged`
    i wyśle ją `submit_staged_documents` (cron).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from wf.dependencies import is_process_admin
from wf.models import (
    Document,
    DocumentVersion,
    HumanTask,
    ProcessInstance,
    ProcessInstanceDocument,
)
from wf.schemas.wf_schemas import CurrentUser, DocumentLinkRole, EventType, IngestStatus
from wf.services import print_service as ps
from wf.services.process_files import files_root
from wf.services.process_service import (
    FormRenderError,
    get_service,
    render_form_json,
)

logger = logging.getLogger(__name__)

DOC_TYPE = 'wydruk'


@dataclass(frozen=True)
class PrintOption:
    key: str
    label: str
    template: str | None
    task_id: UUID | None
    bpmn_task_id: str | None
    task_name: str | None
    is_default: bool = False


@dataclass
class PrintResult:
    rendered: ps.RenderedPrint
    pdf: bytes | None
    task: HumanTask | None


# --- uprawnienia ----------------------------------------------------------

def can_print(instance: ProcessInstance, user: CurrentUser) -> bool:
    if is_process_admin(user):
        return True
    acting = user.acting_user_ids
    if instance.started_by_id in acting:
        return True
    principals = {user.username} | set(user.groups) | set(user.roles)
    for task in instance.tasks or []:
        if task.claimed_by_id in acting or task.completed_by_id in acting:
            return True
        if any(c.principal_value in principals for c in (task.candidates or [])):
            return True
    return False


# --- zadanie i dane -------------------------------------------------------

def _tasks_newest_first(instance: ProcessInstance) -> list[HumanTask]:
    return sorted(instance.tasks or [], key=lambda t: t.created_at or datetime.min, reverse=True)


def resolve_task(instance: ProcessInstance, task_id: UUID | None) -> HumanTask | None:
    if task_id is None:
        tasks = _tasks_newest_first(instance)
        return tasks[0] if tasks else None
    for task in instance.tasks or []:
        if task.id == task_id:
            return task
    raise ps.PrintError('Zadanie nie należy do tej instancji procesu.', status=404)


def _data(instance: ProcessInstance, task: HumanTask | None, workflow) -> dict[str, Any]:
    service = get_service()
    if task is not None:
        if workflow is not None:
            return service.task_form_data(workflow, task)
        return dict(task.submitted_data or instance.summary or {})
    return dict(getattr(workflow, 'data', None) or instance.summary or {})


def _workflow(instance: ProcessInstance):
    try:
        workflow, _spec = get_service().load_workflow(instance)
        return workflow
    except Exception as exc:  # stan nieodtwarzalny - zostają dane zapisane
        logger.info('Wydruk %s: stan instancji nieodtwarzalny (%s)', instance.id, exc)
        return None


def _version(instance: ProcessInstance, task: HumanTask | None):
    return get_service().task_version(task) if task is not None else instance.version


def _entries_for(instance: ProcessInstance, task: HumanTask | None,
                 data: dict[str, Any]) -> list[ps.PrintEntry]:
    spec = get_service().form_spec(_version(instance, task))
    bpmn_task_id = task.bpmn_task_id if task is not None else None
    return [e for e in ps.version_entries(spec['forms'], spec['form_files'], bpmn_task_id)
            if ps.entry_matches(e, bpmn_task_id, data)]


def available_prints(instance: ProcessInstance, task_id: UUID | None = None) -> list[PrintOption]:
    """Wydruki dostępne dla zadania (`task_id`) albo całej instancji: szablony
    wszystkich zadań instancji (dla każdego szablonu najnowsze zadanie)
    i wydruk standardowy najnowszego zadania."""
    workflow = _workflow(instance)
    if task_id is not None:
        tasks = [resolve_task(instance, task_id)]
    else:
        tasks = _tasks_newest_first(instance) or [None]
    options: list[PrintOption] = []
    seen: set[str] = set()
    for task in tasks:
        data = _data(instance, task, workflow)
        for entry in _entries_for(instance, task, data):
            if entry.template in seen:
                continue
            seen.add(entry.template)
            options.append(PrintOption(
                key=entry.template, label=entry.label, template=entry.template,
                task_id=task.id if task else None, bpmn_task_id=task.bpmn_task_id if task else None,
                task_name=task.name if task else None))
    first = tasks[0]
    options.append(PrintOption(
        key=ps.DEFAULT_KEY, label=ps.DEFAULT_LABEL, template=None,
        task_id=first.id if first else None, bpmn_task_id=first.bpmn_task_id if first else None,
        task_name=first.name if first else None, is_default=True))
    return options


# --- renderowanie ---------------------------------------------------------

def _schemas(instance: ProcessInstance, task: HumanTask | None, data: dict[str, Any],
             entry: ps.PrintEntry | None) -> tuple[dict | None, dict | None]:
    """Schemat i uiSchema dla `fields`: formularz zadania (przefiltrowany
    `x-permissions` dla kroku); gdy zadanie nie wskazuje formularza (obiegi
    wariantowe) - schemat, w którym zdefiniowano wydruk."""
    service = get_service()
    version = _version(instance, task)
    spec = service.form_spec(version)
    linked = task is not None and (spec['form_files'].get(task.bpmn_task_id) or {}).get('schema')
    try:
        if entry is not None and entry.schema_file and not linked:
            schema = spec['forms'].get(entry.schema_file)
            ui = spec['uischemas'].get(entry.schema_file.removesuffix('-schema') + '-uischema')
            return (render_form_json(schema, data) if schema is not None else None,
                    render_form_json(ui, data) if ui is not None else None)
        if task is not None:
            return service.task_form(version, task, data)
        return service.form_schema(version, None, data), service.form_ui_schema(version, None, data)
    except FormRenderError as exc:
        logger.info('Wydruk %s: formularz nie renderuje się (%s)', instance.id, exc)
        return None, None


def _meta(instance: ProcessInstance, task: HumanTask | None, user: CurrentUser,
          label: str) -> dict[str, Any]:
    now = datetime.now()
    model = getattr(getattr(instance, 'version', None), 'process_model', None)
    return {
        **ps.organization_meta(),
        'data': ps.polish_date(now.date()),
        'wygenerowano': now.strftime('%d.%m.%Y %H:%M'),
        'znak': instance.business_key or '',
        'tytul': label,
        'proces': getattr(model, 'name', '') or '',
        'proces_klucz': getattr(model, 'key', '') or '',
        'instancja_id': str(instance.id),
        'instancja_tytul': instance.title,
        'zadanie': task.name if task else '',
        'zadanie_bpmn': task.bpmn_task_id if task else '',
        'uzytkownik': user.display_name,
    }


def build_print(db: Session, instance: ProcessInstance, user: CurrentUser, *,
                task_id: UUID | None = None, template: str | None = None,
                pdf: bool = True) -> PrintResult:
    """Wydruk zadania/instancji. `template` = nazwa szablonu z `x-print`
    (None albo `__default__` - wydruk standardowy)."""
    task = resolve_task(instance, task_id)
    workflow = _workflow(instance)
    data = _data(instance, task, workflow)
    entry = None
    if template and template != ps.DEFAULT_KEY:
        entry = next((e for e in _entries_for(instance, task, data) if e.template == template), None)
        if entry is None:
            raise ps.PrintError(f'Wydruk "{template}" nie jest dostępny w tym kroku.', status=404)
    schema, ui = _schemas(instance, task, data, entry)
    meta = _meta(instance, task, user, entry.label if entry else (
        getattr(instance.version.process_model, 'name', None) or ps.DEFAULT_LABEL))
    rendered = ps.render_html(
        sources=ps.template_sources(_version(instance, task), files_root()),
        template=entry.template if entry else None, data=data, meta=meta,
        schema=schema, ui_schema=ui, title=(entry.title if entry else None) or meta['tytul'],
        db=db)
    return PrintResult(rendered=rendered, pdf=ps.html_to_pdf(rendered.html) if pdf else None, task=task)


# --- archiwizacja ---------------------------------------------------------

def archive_print(db: Session, instance: ProcessInstance, user: CurrentUser, *,
                  task_id: UUID | None = None, template: str | None = None,
                  role: DocumentLinkRole = DocumentLinkRole.MAIN) -> Document:
    """PDF jako dokument sprawy: nowa wersja istniejącego dokumentu z tego
    samego szablonu albo nowy dokument; wysyłka do Paperless-ngx."""
    from connector.base import ConnectorConfigError
    from connector.paperless import get_connector

    result = build_print(db, instance, user, task_id=task_id, template=template)
    content = result.pdf or b''
    template_key = template or ps.DEFAULT_KEY
    paperless = get_connector()
    try:
        staging_path = paperless.stage_bytes(content, result.rendered.file_name)
    except ConnectorConfigError as exc:
        raise ps.PrintError(f'Archiwum dokumentów (Paperless) nie jest skonfigurowane: {exc}',
                            status=503) from exc

    document = db.execute(
        select(Document)
        .join(ProcessInstanceDocument, ProcessInstanceDocument.document_id == Document.id)
        .where(ProcessInstanceDocument.process_instance_id == instance.id,
               Document.doc_type == DOC_TYPE)
    ).scalars().all()
    document = next((d for d in document if (d.doc_metadata or {}).get('template') == template_key), None)
    created = document is None
    if created:
        document = Document(
            doc_type=DOC_TYPE, title=result.rendered.title, created_by_id=user.id,
            current_version_no=0,
            doc_metadata={'template': template_key,
                          'bpmn_task_id': result.task.bpmn_task_id if result.task else None,
                          'process_model_version_id': str(instance.process_model_version_id)})
        db.add(document)
        db.flush()
        db.add(ProcessInstanceDocument(process_instance_id=instance.id,
                                       document_id=document.id, role=role))
    version_no = (document.current_version_no or 0) + 1
    version = DocumentVersion(
        document_id=document.id, version_no=version_no, file_name=result.rendered.file_name,
        mime_type='application/pdf', size_bytes=len(content), sha256=paperless.sha256_of(content),
        staging_path=staging_path, uploaded_by_id=user.id,
        human_task_id=result.task.id if result.task else None,
        label=f'Wydruk {datetime.now():%Y-%m-%d %H:%M}')
    db.add(version)
    document.current_version_no = version_no
    document.title = result.rendered.title
    db.flush()
    get_service().record_event(
        db, instance, EventType.DOCUMENT_ADDED if created else EventType.DOCUMENT_VERSION_ADDED,
        actor=user, payload={'document_id': str(document.id), 'file_name': version.file_name,
                             'role': role.value, 'template': template_key, 'version_no': version_no})
    try:
        version.paperless_task_id = paperless.upload_document(
            content, version.file_name, title=document.title,
            document_type_id=paperless.get_or_create_document_type('Wydruk obiegu'),
            tag_ids=[paperless.get_or_create_tag('galwork-wydruk')])
        version.ingest_status = IngestStatus.SUBMITTED
    except Exception as exc:  # Paperless chwilowo niedostępny - zostaje `staged`
        logger.warning('Wydruk %s: wysyłka do Paperless nie powiodła się: %s', instance.id, exc)
        version.ingest_error = str(exc)[:1000]
    db.commit()
    db.refresh(document)
    return document


def find_instance(db: Session, instance_id: UUID) -> ProcessInstance | None:
    return db.execute(select(ProcessInstance).where(ProcessInstance.id == instance_id)).scalars().first()
