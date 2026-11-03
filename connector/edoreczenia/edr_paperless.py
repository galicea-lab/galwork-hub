# coding: utf-8

from __future__ import annotations

import base64
import json
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from connector.base import BaseConnector
from connector.edoreczenia.edr_connector import EdrConnector, mime
from connector.paperless.connector import PaperlessConnector
from wf.models import Document, DocumentVersion, ProcessInstance, ProcessInstanceDocument
from wf.schemas.wf_schemas import (
    CurrentUser,
    DocumentLinkRole,
    EventType,
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

#: typy potwierdzeń obsługiwane dziś przez edr_webcon.py::fetch_evidences_att
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
    treść pliku) - tak samo odczytuje to `edr_webcon.py::inbox_to_webcon`
    (`a.file.file.decode()` -> string base64 -> Webcon). Tu dekodujemy do
    realnych bajtów, bo Paperless chce surowej treści pliku."""
    return base64.b64decode(att.file.file)


# ----------------------------------------------------------------------
# odbiór (odpowiednik inbox_to_webcon)
# ----------------------------------------------------------------------

def _stage_document(db: Session, paperless: PaperlessConnector, *,
                     process_instance: ProcessInstance, actor: CurrentUser,
                     doc_type: str, title: str, file_name: str, mime_type: str,
                     content: bytes, role: DocumentLinkRole = DocumentLinkRole.MAIN,
                     metadata: dict | None = None) -> Document:
    """Odpowiednik `post_addAttach`/`insert_evidences` z `edr_webcon.py` -
    zamiast dopisywać załącznik/wiersz do instancji WEBCON, tworzy
    `Document`+`DocumentVersion` (stan `staged`) powiązany z
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
    """Odpowiednik `edr_webcon.py::inbox_to_webcon`. Dla każdej nowej
    wiadomości z `EdrConnector.inbox()`: deduplikacja przez
    `ProcessInstance.business_key` (indeks `ix_pi_business_key` - zob.
    `analiza_edr.md`, zamiast dawnej bazy `[edr_db]`), start instancji
    procesu, treść + każdy załącznik odłożone do stagingu (`staged`,
    czekają na `submit_staged_documents`)."""
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
    żeby router akcji (`wf/routers/edr_api.py::refresh_evidences`) mógł
    odpytać jedną sprawę na żądanie, bez przechodzenia przez wszystkie
    oczekujące. Nie wywołuje `db.commit()` - robi to wołający (jak przy
    pojedynczej instancji z routera, jak i przy wsadzie z `fetch_evidences`)."""
    message_id = instance.business_key
    raw = edrc.edr.PobranieDowodow(message_id)
    evidences = json.loads(raw.data).get('Evidences', [])
    known = set(instance.summary.get('evidence_ids', []))
    fetched = 0
    for ev in evidences:
        evidence_id = ev.get('evidenceId')
        ev_type = ev.get('type')
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
    instance.summary = {**instance.summary, 'evidence_ids': sorted(known)}
    db.add(instance)
    return fetched


def fetch_evidences(db: Session, paperless: PaperlessConnector, *, actor: CurrentUser,
                     test: bool = False) -> int:
    """Odpowiednik `edr_webcon.py::fetch_evidences`/`fetch_evidences_att` -
    wsad po wszystkich oczekujących instancjach (wołane z crona).

    Zamiast dopisywać wiersze do "item list" instancji WEBCON, każde nowe
    potwierdzenie trafia jako kolejny `Document` (`role=related`) - ten
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
# wysyłka (odpowiednik send1_from_webcon)
# ----------------------------------------------------------------------

def send_document(db: Session, paperless: PaperlessConnector, instance: ProcessInstance, *,
                   actor: CurrentUser, test: bool = False) -> str:
    """Odpowiednik `edr_webcon.py::send1_from_webcon` - wysyła GŁÓWNY
    dokument już istniejącej sprawy (`instance`) przez EDR.

    Zakłada, że:
      * `instance.summary` ma `ade_to` (wymagane) i opcjonalnie `subject`
        - ustawione przy starcie sprawy (generyczny
        `POST /wf/process-instances`, patrz docstring modułu),
      * główny `Document` (`role=main`) jest już zaingestowany do
        Paperless (`paperless_document_id` ustawione przez
        `poll_submitted_documents`) - w przeciwnym razie zgłasza
        `ValueError` (brak sensu wysyłać niekompletną sprawę).

    Nie zapisuje dedykowanego zdarzenia audytu "wiadomość wysłana" - zob.
    docstring modułu (brak odpowiedniego `EventType` bez migracji); zapisuje
    `message_id` do `instance.summary`, co jest widoczne w podglądzie
    instancji."""
    ade = instance.summary.get('ade_to')
    if not ade:
        raise ValueError(f'ProcessInstance {instance.id}: brak `ade_to` w summary')
    subject = instance.summary.get('subject') or 'Przesyłka'

    main_docs = _documents_for_instance(db, instance, DocumentLinkRole.MAIN)
    if not main_docs or not main_docs[0].paperless_document_id:
        raise ValueError(f'ProcessInstance {instance.id}: brak zaingestowanego dokumentu głównego')
    main_document = main_docs[0]
    body = paperless.download_document(main_document.paperless_document_id).decode('utf-8', 'ignore')

    attachments = []
    for doc in _documents_for_instance(db, instance, DocumentLinkRole.ATTACHMENT):
        if not doc.paperless_document_id:
            continue  # jeszcze nie zaingestowany - pominięty, nie blokuje wysyłki
        content = paperless.download_document(doc.paperless_document_id)
        file_name = doc.current_version.file_name if doc.current_version else doc.title
        content_type = doc.current_version.mime_type if doc.current_version else mime(file_name)
        attachments.append({
            'uuid': str(doc.id), 'filename': file_name, 'contentType': content_type,
            'file64': base64.b64encode(content).decode('ascii'), 'description': doc.title,
        })

    edrc = EdrConnector(test)
    result = edrc.send(ade, subject, body, attachments)
    message_id = result.get('MessageId')
    if not message_id:
        raise RuntimeError(f"EDR: {result.get('Status')}")

    instance.summary = {**instance.summary, 'message_id': message_id, 'direction': 'out'}
    db.add(instance)
    db.commit()
    return message_id
