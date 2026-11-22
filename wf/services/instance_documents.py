# wf/services/instance_documents.py

"""
Dokumenty sprawy (instancji procesu) wgrywane przez użytkownika.

  * nowy dokument albo kolejna wersja dokumentu już dołączonego do sprawy -
    wcześniejsze wersje zostają (brak usuwania i nadpisywania),
  * plik trafia do stagingu i do Paperless-ngx jak wydruk
    (wf/services/instance_print.py::archive_print),
  * `sha256` liczony przy wgraniu jest punktem odniesienia przy pobraniu:
    plik o innym skrócie nie zostanie wydany (`IntegrityError`).
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from wf.models import (
    Document,
    DocumentVersion,
    HumanTask,
    ProcessInstance,
    ProcessInstanceDocument,
)
from wf.schemas.wf_schemas import CurrentUser, DocumentLinkRole, EventType, IngestStatus

logger = logging.getLogger(__name__)

#: `Document.doc_type` plików wgranych przez użytkownika (wydruki: 'wydruk')
DOC_TYPE = 'plik'

#: limit rozmiaru pojedynczego pliku
MAX_SIZE_BYTES = 50 * 1024 * 1024

#: dozwolone rozszerzenia -> typ MIME zapisywany w wersji
ALLOWED_TYPES: dict[str, str] = {
    '.pdf': 'application/pdf',
    '.odt': 'application/vnd.oasis.opendocument.text',
    '.ods': 'application/vnd.oasis.opendocument.spreadsheet',
    '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    '.doc': 'application/msword',
    '.txt': 'text/plain',
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.tif': 'image/tiff',
    '.tiff': 'image/tiff',
    '.xml': 'application/xml',
}


class DocumentError(Exception):
    """Błąd operacji na dokumencie sprawy; `status` = kod HTTP."""

    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


class IntegrityError(DocumentError):
    """Treść pliku nie zgadza się ze skrótem zapisanym przy wgraniu."""

    def __init__(self, message: str):
        super().__init__(message, status=409)


@dataclass
class VersionContent:
    file_name: str
    mime_type: str
    sha256: str
    content: bytes


def _paperless():
    # import w funkcji - testy podmieniają `connector.paperless.get_connector`
    from connector.paperless import get_connector
    return get_connector()


def mime_type_for(file_name: str, content: bytes) -> str:
    """Typ MIME z rozszerzenia (lista dozwolonych); PDF sprawdzany po
    sygnaturze, żeby rozszerzenie nie maskowało innej treści."""
    ext = os.path.splitext(file_name or '')[1].lower()
    mime = ALLOWED_TYPES.get(ext)
    if mime is None:
        allowed = ', '.join(sorted(ALLOWED_TYPES))
        raise DocumentError(f"Niedozwolony typ pliku '{ext or file_name}'. Dozwolone: {allowed}",
                            status=415)
    if mime == 'application/pdf' and not content.startswith(b'%PDF'):
        raise DocumentError('Plik z rozszerzeniem .pdf nie jest dokumentem PDF', status=415)
    return mime


def list_documents(db: Session, instance: ProcessInstance) -> list[ProcessInstanceDocument]:
    """Dokumenty sprawy: najpierw główny, potem od najnowszego."""
    links = db.execute(
        select(ProcessInstanceDocument)
        .where(ProcessInstanceDocument.process_instance_id == instance.id)
        .options(selectinload(ProcessInstanceDocument.document).selectinload(Document.created_by),
                 selectinload(ProcessInstanceDocument.document).selectinload(Document.current_version)
                 .selectinload(DocumentVersion.uploaded_by))
    ).scalars().all()
    return sorted(links, key=lambda link: (link.role != DocumentLinkRole.MAIN,
                                           -link.added_at.timestamp()))


def linked_document(db: Session, instance: ProcessInstance, document_id: UUID) -> Document:
    """Dokument dołączony do tej sprawy (inaczej 404 - nie ujawniamy obcych)."""
    link = db.get(ProcessInstanceDocument, (instance.id, document_id))
    if link is None:
        raise DocumentError('Dokument nie jest dołączony do tej sprawy', status=404)
    return link.document


def add_document(db: Session, instance: ProcessInstance, user: CurrentUser, *,
                 content: bytes, file_name: str, title: str | None = None,
                 role: DocumentLinkRole = DocumentLinkRole.ATTACHMENT,
                 document_id: UUID | None = None, task_id: UUID | None = None,
                 label: str | None = None) -> Document:
    """Wgrywa plik jako nowy dokument sprawy albo - z `document_id` - jako
    kolejną wersję dokumentu już dołączonego do sprawy."""
    from wf.services.process_service import get_service  # bez cyklu importów

    if not content:
        raise DocumentError('Plik jest pusty')
    if len(content) > MAX_SIZE_BYTES:
        raise DocumentError(f'Plik przekracza {MAX_SIZE_BYTES // (1024 * 1024)} MB', status=413)
    file_name = os.path.basename(file_name or '').strip() or 'dokument'
    mime_type = mime_type_for(file_name, content)

    task = None
    if task_id is not None:
        task = db.get(HumanTask, task_id)
        if task is None or task.process_instance_id != instance.id:
            raise DocumentError('Zadanie nie należy do tej sprawy', status=404)

    paperless = _paperless()
    try:
        staging_path = paperless.stage_bytes(content, file_name)
    except Exception as exc:  # brak konfiguracji Paperless / katalogu stagingu
        raise DocumentError(f'Archiwum dokumentów (Paperless) nie jest dostępne: {exc}',
                            status=503) from exc

    created = document_id is None
    if created:
        document = Document(doc_type=DOC_TYPE, title=(title or '').strip() or file_name,
                            created_by_id=user.id, current_version_no=0,
                            doc_metadata={'source': 'upload'})
        db.add(document)
        db.flush()
        db.add(ProcessInstanceDocument(process_instance_id=instance.id,
                                       document_id=document.id, role=role))
    else:
        document = linked_document(db, instance, document_id)
        if title and title.strip():
            document.title = title.strip()

    version_no = max([v.version_no for v in document.versions] or [0]) + 1
    version = DocumentVersion(
        document_id=document.id, version_no=version_no, label=(label or '').strip() or None,
        file_name=file_name, mime_type=mime_type, size_bytes=len(content),
        sha256=paperless.sha256_of(content), staging_path=staging_path,
        uploaded_by_id=user.id, human_task_id=task.id if task else None)
    db.add(version)
    document.current_version_no = version_no
    db.flush()
    get_service().record_event(
        db, instance, EventType.DOCUMENT_ADDED if created else EventType.DOCUMENT_VERSION_ADDED,
        actor=user, payload={'document_id': str(document.id), 'file_name': file_name,
                             'version_no': version_no, 'sha256': version.sha256,
                             'role': role.value if created else None})
    try:
        version.paperless_task_id = paperless.upload_document(
            content, file_name, title=document.title,
            tag_ids=[paperless.get_or_create_tag('galwork-plik')])
        version.ingest_status = IngestStatus.SUBMITTED
    except Exception as exc:  # Paperless chwilowo niedostępny - zostaje `staged`
        logger.warning('Dokument %s: wysyłka do Paperless nie powiodła się: %s', document.id, exc)
        version.ingest_error = str(exc)[:1000]
    db.commit()
    db.refresh(document)
    return document


def version_content(db: Session, document: Document, version_no: int | None = None) -> VersionContent:
    """Treść wersji (domyślnie bieżącej) ze stagingu albo z Paperless-ngx,
    po sprawdzeniu skrótu z chwili wgrania."""
    version_no = version_no or document.current_version_no
    version = next((v for v in document.versions if v.version_no == version_no), None)
    if version is None:
        raise DocumentError('Brak takiej wersji dokumentu', status=404)

    paperless = _paperless()
    if version.staging_path and os.path.exists(version.staging_path):
        content = paperless.read_staged(version.staging_path)
    elif version.paperless_version_id:
        content = paperless.download_document(version.paperless_version_id, original=True)
    elif document.paperless_document_id and version_no == document.current_version_no:
        content = paperless.download_document(document.paperless_document_id, original=True)
    else:
        raise DocumentError('Plik tej wersji jest niedostępny (jeszcze nie w archiwum)', status=404)

    if paperless.sha256_of(content) != version.sha256:
        logger.error('Dokument %s v%s: niezgodny skrót treści', document.id, version_no)
        raise IntegrityError('Treść pliku nie zgadza się ze skrótem zapisanym przy wgraniu '
                             f'(dokument {document.id}, wersja {version_no})')
    return VersionContent(version.file_name, version.mime_type, version.sha256, content)
