# coding: utf-8

from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from connector.base import BaseConnector, ConnectorConfigError
from connector.edoreczenia.edr_connector import EdrConnector, mime
from connector.paperless.connector import PaperlessConnector
from wf.decisions import DEFAULT_VALUE
# 2026-11-08: HumanTask/ProcessModel* - kolejka wysyłki (send_queued).
from wf.models import (
    Document,
    DocumentVersion,
    HumanTask,
    ProcessInstance,
    ProcessInstanceDocument,
    ProcessModel,
    ProcessModelVersion,
)
from wf.schemas.wf_schemas import (
    CurrentUser,
    DocumentLinkRole,
    EventType,
    HumanTaskStatus,
    IngestStatus,
    ProcessInstanceStatus,
)
from wf.services.process_service import get_service

log = logging.getLogger(__name__)

_edr_config = BaseConnector.load_ini_section('edr')

#: klucz modelu procesu dla nowej korespondencji przychodzącej - musi
#: istnieć jako ProcessModel z opublikowaną wersją, inaczej `sync_inbox`
#: zgłosi `wf.services.process_service.ProcessServiceError`.
PROCESS_MODEL_KEY = _edr_config.get('process_model_key', 'edr-korespondencja')

DOC_TYPE_IN = 'edr_in'
DOC_TYPE_EVIDENCE = 'edr_evidence'

#: typy potwierdzeń
#: (BPOP/BPWP - PDF doręczenia/odbioru, BPWX/BPOX - XML akceptacji/preawizacji).
_EVIDENCE_TYPES_PDF = ('BPOP', 'BPWP')
_EVIDENCE_TYPES_XML = ('BPWX', 'BPOX')


# ----------------------------------------------------------------------
# pomocnicze - zapytania
# ----------------------------------------------------------------------

def _instance_by_business_key(db: Session, business_key: str) -> ProcessInstance | None:
    return db.execute(
        select(ProcessInstance).where(ProcessInstance.business_key == business_key)
    ).scalars().first()


def _owning_instance(db: Session, document: Document) -> ProcessInstance | None:
    return db.execute(
        select(ProcessInstance)
        .join(ProcessInstanceDocument, ProcessInstanceDocument.process_instance_id == ProcessInstance.id)
        .where(ProcessInstanceDocument.document_id == document.id)
    ).scalars().first()


def pending_staged_instances(db: Session) -> list[ProcessInstance]:
    """Sprawy, które mają co najmniej jeden `DocumentVersion` w stanie
    `staged` - czyli oczekujące na `submit_staged_documents`, ale już
    obecne w bazie (bez pobierania czegokolwiek z e-Doręczeń). Przydatne,
    gdy `sync_inbox` nie jest wołane (np. ponowna próba wysyłki po
    wcześniejszym błędzie Paperless-ngx)."""
    return list(db.execute(
        select(ProcessInstance)
        .join(ProcessInstanceDocument, ProcessInstanceDocument.process_instance_id == ProcessInstance.id)
        .join(DocumentVersion, DocumentVersion.document_id == ProcessInstanceDocument.document_id)
        .where(DocumentVersion.ingest_status == IngestStatus.STAGED)
        .distinct()
    ).scalars().all())


def _documents_for_instance(db: Session, instance: ProcessInstance,
                             role: DocumentLinkRole) -> list[Document]:
    return list(db.execute(
        select(Document)
        .join(ProcessInstanceDocument, ProcessInstanceDocument.document_id == Document.id)
        .where(ProcessInstanceDocument.process_instance_id == instance.id,
               ProcessInstanceDocument.role == role)
    ).scalars().all())


def _attachment_bytes(att) -> bytes:
    """`Attachment.file.file` to bajty ASCII base64-tekstu (nie surowa
    treść pliku). Tu dekodujemy do
    realnych bajtów, bo Paperless chce surowej treści pliku."""
    return base64.b64decode(att.file.file)


# ----------------------------------------------------------------------
# odbiór
# ----------------------------------------------------------------------

def _stage_document(db: Session, paperless: PaperlessConnector, *,
                     process_instance: ProcessInstance, actor: CurrentUser,
                     doc_type: str, title: str, file_name: str, mime_type: str,
                     content: bytes, role: DocumentLinkRole = DocumentLinkRole.MAIN,
                     metadata: dict | None = None) -> Document:
    """ Tworzy `Document`+`DocumentVersion` (stan `staged`) powiązany z
    `ProcessInstance` przez `ProcessInstanceDocument`."""
    document = Document(
        doc_type=doc_type, title=title, doc_metadata=dict(metadata or {}),
        created_by_id=actor.id, current_version_no=1,
    )
    db.add(document)
    db.flush()  # potrzebne document.id

    staging_path = paperless.stage_bytes(content, file_name)
    version = DocumentVersion(
        document_id=document.id, version_no=1, file_name=file_name,
        mime_type=mime_type, size_bytes=len(content),
        sha256=paperless.sha256_of(content), staging_path=staging_path,
        uploaded_by_id=actor.id,
    )
    db.add(version)
    db.add(ProcessInstanceDocument(
        process_instance_id=process_instance.id, document_id=document.id, role=role,
    ))
    db.flush()
    get_service().record_event(
        db, process_instance, EventType.DOCUMENT_ADDED, actor=actor,
        payload={'document_id': str(document.id), 'file_name': file_name, 'role': role.value},
    )
    return document


def sync_inbox(db: Session, paperless: PaperlessConnector, *, actor: CurrentUser,
                test: bool = False) -> list[ProcessInstance]:
    """ Dla każdej nowej  wiadomości z `EdrConnector.inbox()`: deduplikacja przez
    `ProcessInstance.business_key` (indeks `ix_pi_business_key` - 
    zamiast dawnej bazy `[edr_db]`), start instancji procesu, treść + każdy załącznik odłożone do stagingu 
    (`staged`, czekają na `submit_staged_documents`)."""
    edrc = EdrConnector(test)
    version = get_service().published_version(db, PROCESS_MODEL_KEY)
    created: list[ProcessInstance] = []
    for message_id in edrc.inbox():
        if _instance_by_business_key(db, message_id) is not None:
            continue
        msg = edrc.message(message_id)
        if msg is None:
            log.warning('edr_paperless.sync_inbox: nie udało się pobrać %s', message_id)
            continue
        meta = msg.messageMetadata
        ctrl = msg.messageControlData
        labels = ', '.join(l.label for l in (ctrl.labels or []) if l.label) if ctrl else ''
        instance = get_service().start_instance(
            db, version,
            title=(meta.subject if meta and meta.subject else f'Wiadomość EDR {message_id}'),
            business_key=message_id,
            data={
                'direction': 'in',
                'ade_from': (meta.from_.eDeliveryAddress if meta and meta.from_ else '') or '',
                'ade_to': (meta.to[0].eDeliveryAddress if meta and meta.to else '') or '',
                'subject': (meta.subject if meta else '') or '',
                'labels': labels,
                'thread_id': (meta.threadId if meta else '') or '',
                'timestamp': str(meta.timestamp) if meta and meta.timestamp else '',
            },
            actor=actor,
        )
        _stage_document(
            db, paperless, process_instance=instance, actor=actor,
            doc_type=DOC_TYPE_IN, title=instance.title, file_name=f'{message_id}.txt',
            mime_type='text/plain', content=(msg.textBody or '').encode('utf-8'),
            role=DocumentLinkRole.MAIN, metadata={'message_id': message_id},
        )
        for att in (msg.attachments or []):
            meta_a = att.file.fileMetadata if att.file else None
            filename = (meta_a.filename if meta_a and meta_a.filename else None) or f'zalacznik-{att.attachmentId}'
            content_type = (meta_a.contentType if meta_a and meta_a.contentType else None) or mime(filename)
            _stage_document(
                db, paperless, process_instance=instance, actor=actor,
                doc_type=DOC_TYPE_IN, title=filename, file_name=filename,
                mime_type=content_type, content=_attachment_bytes(att),
                role=DocumentLinkRole.ATTACHMENT, metadata={'message_id': message_id},
            )
        created.append(instance)
    return created

def test_edr_to_paperless(db: Session, paperless: PaperlessConnector, *, actor: CurrentUser,
                test: bool = False) -> ProcessInstance:
    """Bez łączenia się z edoreczenia - próba stworzenia zapisu w oparciu o plik dev/edoreczenia/message.json."""
    import json
    from .ua_model import Message
    with open("dev/edoreczenia/message.json", "r", encoding="utf-8") as fmessage:
        json_data=fmessage.read() #json.load(fmessage)
        msg = Message.model_validate_json(json_data)
    version = get_service().published_version(db, PROCESS_MODEL_KEY)
    message_id=msg.messageMetadata.messageId
    if _instance_by_business_key(db, message_id) is not None:
        return None
    meta = msg.messageMetadata
    ctrl = msg.messageControlData
    labels = ', '.join(l.label for l in (ctrl.labels or []) if l.label) if ctrl else ''
    instance = get_service().start_instance(
        db, version,
        title=(meta.subject if meta and meta.subject else f'Wiadomość EDR {message_id}'),
        business_key=message_id,
        data={
            'direction': 'in',
            'ade_from': (meta.from_.eDeliveryAddress if meta and meta.from_ else '') or '',
            'ade_to': (meta.to[0].eDeliveryAddress if meta and meta.to else '') or '',
            'subject': (meta.subject if meta else '') or '',
            'labels': labels,
            'thread_id': (meta.threadId if meta else '') or '',
            'timestamp': str(meta.timestamp) if meta and meta.timestamp else '',
        },
        actor=actor,
    )
    _stage_document(
        db, paperless, process_instance=instance, actor=actor,
        doc_type=DOC_TYPE_IN, title=instance.title, file_name=f'{message_id}.txt',
        mime_type='text/plain', content=(msg.textBody or '').encode('utf-8'),
        role=DocumentLinkRole.MAIN, metadata={'message_id': message_id},
    )
    for att in (msg.attachments or []):
        meta_a = att.file.fileMetadata if att.file else None
        filename = (meta_a.filename if meta_a and meta_a.filename else None) or f'zalacznik-{att.attachmentId}'
        content_type = (meta_a.contentType if meta_a and meta_a.contentType else None) or mime(filename)
        _stage_document(
            db, paperless, process_instance=instance, actor=actor,
            doc_type=DOC_TYPE_IN, title=filename, file_name=filename,
            mime_type=content_type, content=_attachment_bytes(att),
            role=DocumentLinkRole.ATTACHMENT, metadata={'message_id': message_id},
        )
    return instance


# ----------------------------------------------------------------------
# ingest do Paperless (nowy krok - zob. docstring modułu)
# ----------------------------------------------------------------------

def submit_staged_documents(db: Session, paperless: PaperlessConnector, *,
                             limit: int = 50) -> int:
    """Krok 2 ingestu (zob. `connector/paperless/connector.py`): wysyła do
    Paperless-ngx wszystkie `DocumentVersion` w stanie `staged`. Wołane z
    tego samego miejsca co `sync_inbox`/`fetch_evidences` (np. crona),
    bo konsumpcja Paperless jest asynchroniczna."""
    versions = db.execute(
        select(DocumentVersion)
        .where(DocumentVersion.ingest_status == IngestStatus.STAGED)
        .limit(limit)
    ).scalars().all()
    submitted = 0
    for version in versions:
        document = version.document
        try:
            content = paperless.read_staged(version.staging_path)
            task_id = paperless.upload_document(
                content, version.file_name, title=document.title,
                document_type_id=paperless.get_or_create_document_type('e-Doręczenie'),
                tag_ids=[paperless.get_or_create_tag('EDR')],
            )
        except Exception as exc:
            version.ingest_status = IngestStatus.FAILED
            version.ingest_error = str(exc)
            log.exception('edr_paperless.submit_staged_documents: %s', version.id)
        else:
            version.ingest_status = IngestStatus.SUBMITTED
            version.paperless_task_id = task_id
            submitted += 1
        db.add(version)
    db.commit()
    return submitted


def poll_submitted_documents(db: Session, paperless: PaperlessConnector, *,
                              actor: CurrentUser, limit: int = 50) -> int:
    """Krok 3 ingestu: odpytuje `/api/tasks/` dla `DocumentVersion` w
    stanie `submitted` - przejście do `done`/`failed`/`duplicate`."""
    versions = db.execute(
        select(DocumentVersion)
        .where(DocumentVersion.ingest_status == IngestStatus.SUBMITTED)
        .limit(limit)
    ).scalars().all()
    handled = 0
    for version in versions:
        task = paperless.get_task(version.paperless_task_id)
        if task is None or task.get('status') in ('PENDING', 'STARTED'):
            continue
        document = version.document
        instance = _owning_instance(db, document)
        if task.get('status') == 'SUCCESS':
            doc_id = paperless.task_document_id(task)
            version.ingest_status = IngestStatus.DONE
            version.paperless_version_id = doc_id
            document.paperless_document_id = doc_id
            if instance is not None:
                get_service().record_event(
                    db, instance, EventType.DOCUMENT_INGESTED, actor=actor,
                    payload={'document_id': str(document.id), 'paperless_document_id': doc_id},
                )
        elif paperless.is_duplicate_task(task):
            version.ingest_status = IngestStatus.DUPLICATE
            version.ingest_error = task.get('result')
        else:
            version.ingest_status = IngestStatus.FAILED
            version.ingest_error = task.get('result')
            if instance is not None:
                get_service().record_event(
                    db, instance, EventType.DOCUMENT_INGEST_FAILED, actor=actor,
                    payload={'document_id': str(document.id), 'error': version.ingest_error},
                )
        paperless.discard_staged(version.staging_path)
        db.add(version)
        db.add(document)
        handled += 1
    db.commit()
    return handled


# ----------------------------------------------------------------------
# potwierdzenia (odpowiednik fetch_evidences/fetch_evidences_att)
# ----------------------------------------------------------------------

def fetch_evidences_for_instance(db: Session, paperless: PaperlessConnector,
                                  instance: ProcessInstance, edrc: EdrConnector, *,
                                  actor: CurrentUser) -> int:
    """Potwierdzenia dla JEDNEJ instancji - wydzielone z `fetch_evidences`,
    żeby router akcji (`wf/routers/edr_paperless_api.py::refresh_evidences`) mógł
    odpytać jedną sprawę na żądanie, bez przechodzenia przez wszystkie
    oczekujące. Nie wywołuje `db.commit()` - robi to wołający (jak przy
    pojedynczej instancji z routera, jak i przy wsadzie z `fetch_evidences`)."""
    message_id = instance.business_key
    raw = edrc.edr.PobranieDowodow(message_id)
    evidences = json.loads(raw.data).get('Evidences', [])
    known = set(instance.summary.get('evidence_ids', []))
    # 2026-11-08: typy wszystkich potwierdzeń (także niepobieranych) -
    # complete_delivered zamyka po nich "Potwierdzenie doręczenia".
    types = set(instance.summary.get('evidence_types', []))
    fetched = 0
    for ev in evidences:
        evidence_id = ev.get('evidenceId')
        ev_type = ev.get('type')
        if ev_type:
            types.add(ev_type)
        if not evidence_id or evidence_id in known:
            continue
        if ev_type not in _EVIDENCE_TYPES_PDF + _EVIDENCE_TYPES_XML:
            continue
        is_pdf = ev_type in _EVIDENCE_TYPES_PDF
        content_resp = edrc.edr.PobranieDowoduF(evidence_id)
        _stage_document(
            db, paperless, process_instance=instance, actor=actor,
            doc_type=DOC_TYPE_EVIDENCE, title=f'{ev_type} {message_id}',
            file_name=f'{evidence_id}.{"pdf" if is_pdf else "xml"}',
            mime_type='application/pdf' if is_pdf else 'text/xml',
            content=content_resp.data, role=DocumentLinkRole.RELATED,
            metadata={'message_id': message_id, 'evidence_id': evidence_id, 'type': ev_type},
        )
        known.add(evidence_id)
        fetched += 1
    instance.summary = {**instance.summary, 'evidence_ids': sorted(known),
                        'evidence_types': sorted(types)}
    db.add(instance)
    return fetched


def fetch_evidences(db: Session, paperless: PaperlessConnector, *, actor: CurrentUser,
                     test: bool = False) -> int:
    """ wsad po wszystkich oczekujących instancjach (wołane z crona).

    Każde nowe potwierdzenie trafia jako kolejny `Document` (`role=related`) - ten
    sam mechanizm stagingu/ingestu co główny list (zob. `analiza_edr.md`
    §3.2: "evidences bez child table")."""
    edrc = EdrConnector(test)
    pending = db.execute(
        select(ProcessInstance)
        .where(ProcessInstance.business_key.is_not(None))
        .where(ProcessInstance.status.in_(
            [ProcessInstanceStatus.RUNNING, ProcessInstanceStatus.WAITING]))
    ).scalars().all()
    fetched = 0
    for instance in pending:
        try:
            fetched += fetch_evidences_for_instance(db, paperless, instance, edrc, actor=actor)
        except Exception as exc:
            log.warning('edr_paperless.fetch_evidences: %s -> %s', instance.business_key, exc)
    db.commit()
    return fetched



# ----------------------------------------------------------------------
# wysyłka
# ----------------------------------------------------------------------
# 2026-11-08: proces `edr-wysylka` (spiffworkflow/kancelaria/edr-wysylka)
# Otwarte zadania "Wysyłka EDR" (grupa `edr-system`) to kolejka; 
# `send_queued` (cron `scripts/edr.py send`)
# dołącza do sprawy dokumenty z Paperless-ngx, wysyła je i zamyka zadanie.

DOC_TYPE_OUT = 'edr_out'

SEND_PROCESS_MODEL_KEY = _edr_config.get('send_process_model_key', 'edr-wysylka')
SEND_TASK_ID = 'Task_Wysylka'
SEND_GROUP = 'edr-system'
#: ścieżka gatewaya "Wynik wysyłki" przy błędzie (domyślna = wysłano)
SEND_FAILED_PATH = 'Flow_Blad'
#: ile kolejnych przebiegów crona może skończyć się błędem EDR/Paperless-ngx,
#: zanim sprawa trafi do zadania "Problem z wysyłką"
SEND_MAX_ATTEMPTS = int(_edr_config.get('send_max_attempts', '3') or 3)
DELIVERY_TASK_ID = 'Task_Doreczenie'
#: typy potwierdzeń, po których "Potwierdzenie doręczenia" zamyka się samo
#: (ścieżka "Doręczono"); pusta wartość wyłącza automat
DELIVERED_EVIDENCE_TYPES = tuple(
    t.strip() for t in (_edr_config.get('delivered_evidence_types', 'BPOP') or '').split(',')
    if t.strip())


def _document_by_paperless_id(db: Session, paperless_id: int) -> Document | None:
    return db.execute(
        select(Document).where(Document.paperless_document_id == paperless_id)
    ).scalars().first()


def link_paperless_documents(db: Session, paperless: PaperlessConnector,
                             instance: ProcessInstance, paperless_ids, *,
                             actor: CurrentUser) -> list[Document]:
    """Wiąże ze sprawą dokumenty, które już są w Paperless-ngx.

    Pierwszy z listy dostaje rolę `main`, kolejne `attachment`. Dokument
    znany już hubowi (`Document.paperless_document_id`) jest używany ponownie,
    inny dostaje nowy `Document` + `DocumentVersion` w stanie `done`. Powiązania
    `main`/`attachment` spoza listy są usuwane (po "Popraw dane przesyłki").
    Nie wywołuje `db.commit()`."""
    ids: list[int] = []
    for value in paperless_ids or []:
        if value in (None, ''):
            continue
        number = int(value)
        if number not in ids:
            ids.append(number)
    if not ids:
        raise ValueError(f'ProcessInstance {instance.id}: brak dokumentów do wysłania')

    links = db.execute(
        select(ProcessInstanceDocument)
        .where(ProcessInstanceDocument.process_instance_id == instance.id,
               ProcessInstanceDocument.role.in_([DocumentLinkRole.MAIN,
                                                 DocumentLinkRole.ATTACHMENT]))
    ).scalars().all()
    for link in links:
        if link.document.paperless_document_id not in ids:
            db.delete(link)
    db.flush()

    documents: list[Document] = []
    for index, paperless_id in enumerate(ids):
        role = DocumentLinkRole.MAIN if index == 0 else DocumentLinkRole.ATTACHMENT
        document = _document_by_paperless_id(db, paperless_id)
        if document is None:
            meta = paperless.get_document(paperless_id)
            content = paperless.download_document(paperless_id, original=True)
            file_name = meta.get('original_file_name') or f'dokument-{paperless_id}.pdf'
            document = Document(
                doc_type=DOC_TYPE_OUT, title=meta.get('title') or file_name,
                doc_metadata={'source': 'paperless'}, created_by_id=actor.id,
                current_version_no=1, paperless_document_id=paperless_id,
            )
            db.add(document)
            db.flush()
            db.add(DocumentVersion(
                document_id=document.id, version_no=1, file_name=file_name,
                mime_type=meta.get('mime_type') or mime(file_name),
                size_bytes=len(content), sha256=paperless.sha256_of(content),
                ingest_status=IngestStatus.DONE, paperless_version_id=paperless_id,
                uploaded_by_id=actor.id,
            ))
            db.flush()
            db.refresh(document)
        link = db.get(ProcessInstanceDocument, (instance.id, document.id))
        if link is None:
            db.add(ProcessInstanceDocument(
                process_instance_id=instance.id, document_id=document.id, role=role))
            get_service().record_event(
                db, instance, EventType.DOCUMENT_ADDED, actor=actor,
                payload={'document_id': str(document.id),
                         'paperless_document_id': paperless_id, 'role': role.value},
            )
        elif link.role != role:
            link.role = role
        documents.append(document)
    db.flush()
    return documents


def _edr_attachment(paperless: PaperlessConnector, document: Document) -> dict:
    content = paperless.download_document(document.paperless_document_id, original=True)
    version = document.current_version
    file_name = version.file_name if version else document.title
    return {
        'uuid': str(document.id), 'filename': file_name,
        'contentType': version.mime_type if version else mime(file_name),
        'file64': base64.b64encode(content).decode('ascii'), 'description': document.title,
    }


def send_document(db: Session, paperless: PaperlessConnector, instance: ProcessInstance, *,
                   actor: CurrentUser, test: bool = False,
                   edrc: EdrConnector | None = None) -> str:
    """ Wysyła dokumenty sprawy (`instance`) przez EDR.

    Zakłada, że:
      * `instance.summary` ma `ade_to` (wymagane) i opcjonalnie `subject`
        oraz `text_body`,
      * główny `Document` (`role=main`) jest w Paperless-ngx
        (`paperless_document_id` ustawione) - inaczej `ValueError`.

    Treść wiadomości: `text_body`, a dokument główny idzie jako pierwszy
    załącznik. Bez `text_body` treścią jest dokument główny, jeśli jest
    tekstowy (np. odpowiedź na wiadomość przychodzącą), a w przeciwnym razie
    temat. Pliki pobierane są w wersji oryginalnej (podpis PAdES).

    Zapisuje `message_id` do `instance.summary`, a gdy sprawa nie ma
    `business_key`, ustawia go na `message_id` - wtedy `fetch_evidences`
    dociąga potwierdzenia także dla wysłanych przesyłek."""
    summary = dict(instance.summary or {})
    ade = summary.get('ade_to')
    if not ade:
        raise ValueError(f'ProcessInstance {instance.id}: brak `ade_to` w summary')
    subject = summary.get('subject') or 'Przesyłka'

    main_docs = _documents_for_instance(db, instance, DocumentLinkRole.MAIN)
    if not main_docs or not main_docs[0].paperless_document_id:
        raise ValueError(f'ProcessInstance {instance.id}: brak zaingestowanego dokumentu głównego')
    main_document = main_docs[0]
    main_version = main_document.current_version
    main_mime = main_version.mime_type if main_version else mime(main_document.title or '')

    attachments = []
    text_body = (summary.get('text_body') or '').strip()
    if text_body:
        body = text_body
        attachments.append(_edr_attachment(paperless, main_document))
    elif main_mime.startswith('text/'):
        body = paperless.download_document(
            main_document.paperless_document_id, original=True).decode('utf-8', 'ignore')
    else:
        body = subject
        attachments.append(_edr_attachment(paperless, main_document))

    for doc in _documents_for_instance(db, instance, DocumentLinkRole.ATTACHMENT):
        if not doc.paperless_document_id:
            continue  # jeszcze nie zaingestowany - pominięty, nie blokuje wysyłki
        attachments.append(_edr_attachment(paperless, doc))

    edrc = edrc or EdrConnector(test)
    result = edrc.send(ade, subject, body, attachments)
    message_id = result.get('MessageId')
    if not message_id:
        raise RuntimeError(f"EDR: {result.get('Status')}")

    instance.summary = {**summary, 'message_id': message_id, 'direction': 'out'}
    if not instance.business_key:
        instance.business_key = message_id
    db.add(instance)
    db.commit()
    return message_id


@dataclass
class SendQueueResult:
    sent: int = 0
    failed: int = 0
    deferred: int = 0


def _open_tasks(db: Session, bpmn_task_id: str, *, limit: int = 20,
                instance_id=None) -> list[HumanTask]:
    """Otwarte zadania `bpmn_task_id` w trwających instancjach `edr-wysylka`."""
    query = (
        select(HumanTask)
        .join(ProcessInstance, ProcessInstance.id == HumanTask.process_instance_id)
        .join(ProcessModelVersion, ProcessModelVersion.id == ProcessInstance.process_model_version_id)
        .join(ProcessModel, ProcessModel.id == ProcessModelVersion.process_model_id)
        .where(ProcessModel.key == SEND_PROCESS_MODEL_KEY,
               HumanTask.bpmn_task_id == bpmn_task_id,
               HumanTask.status.in_([HumanTaskStatus.OPEN, HumanTaskStatus.CLAIMED]),
               ProcessInstance.status == ProcessInstanceStatus.RUNNING)
        .order_by(HumanTask.created_at)
        .limit(limit)
    )
    if instance_id is not None:
        query = query.where(ProcessInstance.id == instance_id)
    return list(db.execute(query).scalars().all())


def queued_send_tasks(db: Session, *, limit: int = 20) -> list[HumanTask]:
    """Kolejka wysyłki: otwarte zadania "Wysyłka EDR" procesu `edr-wysylka`."""
    return _open_tasks(db, SEND_TASK_ID, limit=limit)


_SEND_FIELDS = ('ade_to', 'odbiorca', 'subject', 'text_body', 'paperless_document_ids')


def send_queued(db: Session, paperless: PaperlessConnector, *, actor: CurrentUser,
                test: bool = False, limit: int = 20) -> SendQueueResult:
    """Obsługuje kolejkę wysyłki (cron `scripts/edr.py send`).

    Dla każdego zadania "Wysyłka EDR": dane przesyłki z formularzy procesu
    trafiają do `instance.summary`, dokumenty z Paperless-ngx są wiązane ze
    sprawą, a po wysyłce zadanie jest zamykane z `message_id`.

    * Błąd danych (`ValueError`: brak adresu, dokumentów) - od razu zadanie
      "Problem z wysyłką" (`chosen_path = Flow_Blad`, `send_error`).
    * Błąd EDR/Paperless-ngx - zadanie zostaje w kolejce do następnego
      przebiegu; po `SEND_MAX_ATTEMPTS` próbach trafia do "Problem z wysyłką".
    * Brak konfiguracji Paperless-ngx (`ConnectorConfigError`) przerywa cały
      przebieg - nie ma sensu oznaczać każdej sprawy jako błędnej.

    `actor` musi należeć do grupy `edr-system` (kandydat zadania w BPMN).
    Jeśli wysyłka się udała, a zamknięcie zadania nie, kolejny przebieg nie
    wysyła drugi raz (`summary.send_task_id` + `summary.message_id`)."""
    service = get_service()
    result = SendQueueResult()
    edrc: EdrConnector | None = None
    for task in queued_send_tasks(db, limit=limit):
        instance = task.process_instance
        task_id = str(task.id)
        summary = dict(instance.summary or {})
        attempts = int(summary.get('send_attempts') or 0)
        try:
            if summary.get('send_task_id') == task_id and summary.get('message_id'):
                message_id = summary['message_id']
            else:
                workflow, _spec = service.load_workflow(instance)
                data = service.task_form_data(workflow, task)
                summary.update({key: data.get(key) for key in _SEND_FIELDS if key in data})
                summary.pop('message_id', None)
                instance.summary = summary
                link_paperless_documents(db, paperless, instance,
                                         summary.get('paperless_document_ids'), actor=actor)
                if edrc is None:
                    edrc = EdrConnector(test)
                message_id = send_document(db, paperless, instance, actor=actor, edrc=edrc)
                instance.summary = {**instance.summary, 'send_task_id': task_id,
                                    'send_attempts': 0, 'send_error': ''}
                db.commit()
        except ConnectorConfigError:
            db.rollback()
            raise
        except ValueError as exc:
            db.rollback()
            log.warning('edr_paperless.send_queued: %s -> %s', instance.id, exc)
            _send_failed(db, service, task, actor, str(exc))
            result.failed += 1
            continue
        except Exception as exc:  # EDR (RuntimeError), Paperless-ngx (ConnectorError), sieć
            db.rollback()
            attempts += 1
            log.warning('edr_paperless.send_queued: %s próba %s/%s -> %s',
                        instance.id, attempts, SEND_MAX_ATTEMPTS, exc)
            if attempts >= SEND_MAX_ATTEMPTS:
                _send_failed(db, service, task, actor, str(exc))
                result.failed += 1
            else:
                instance.summary = {**(instance.summary or {}), 'send_attempts': attempts,
                                    'send_error': str(exc)}
                db.commit()
                result.deferred += 1
            continue
        service.complete_task(db, task, actor, {
            'message_id': message_id, 'send_error': '', 'chosen_path': DEFAULT_VALUE})
        result.sent += 1
    return result


def _send_failed(db: Session, service, task: HumanTask, actor: CurrentUser, error: str) -> None:
    instance = task.process_instance
    instance.summary = {**(instance.summary or {}), 'send_attempts': 0, 'send_error': error}
    db.commit()
    service.complete_task(db, task, actor, {
        'message_id': '', 'send_error': error, 'chosen_path': SEND_FAILED_PATH})


def complete_delivered(db: Session, *, actor: CurrentUser, instance_id=None,
                       limit: int = 50) -> int:
    """Zamyka "Potwierdzenie doręczenia" (ścieżka "Doręczono") w sprawach,
    które mają potwierdzenie typu z `[edr] delivered_evidence_types`
    (`summary.evidence_types`, zapisywane przez `fetch_evidences_for_instance`).

    Wołane po pobraniu potwierdzeń: cron `scripts/edr.py evidences` (wszystkie
    sprawy) i `POST /edr/process-instances/{id}/refresh-evidences` (jedna).
    `actor` musi należeć do `kancelaria` albo `edr-system` (kandydaci zadania
    w BPMN). "Nie doręczono" zostaje decyzją człowieka."""
    if not DELIVERED_EVIDENCE_TYPES:
        return 0
    service = get_service()
    completed = 0
    for task in _open_tasks(db, DELIVERY_TASK_ID, limit=limit, instance_id=instance_id):
        types = set((task.process_instance.summary or {}).get('evidence_types') or [])
        matched = sorted(types.intersection(DELIVERED_EVIDENCE_TYPES))
        if not matched:
            continue
        service.complete_task(db, task, actor, {
            'chosen_path': DEFAULT_VALUE,
            'uwagi': f"Zamknięte automatycznie: potwierdzenie {', '.join(matched)}"})
        completed += 1
    return completed
