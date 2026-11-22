# wf/services/document_ingest.py
# 
# Ponowna wysyłka do Paperless-ngx dokumentów huba, których
# wysyłka przy zapisie się nie powiodła (pliki wgrane, wydruki, wersje
# opieczętowane i podpisane).
"""
Wysyłka dokumentów huba do Paperless-ngx (krok cykliczny, scripts/wf_documents.py).

  * `submit_pending` - wersje w stanie `staged` (wysyłka przy zapisie nie
    powiodła się albo nie była próbowana) oraz `failed` bez zadania
    Paperless (błąd samej wysyłki, nie konsumpcji). Przed wysyłką skrót
    pliku ze stagingu jest porównywany z `sha256` wersji - plik zmieniony
    na dysku nie trafia do archiwum (stan `failed`, komunikat w wersji),
  * `poll_submitted` - wynik konsumpcji (`done`/`duplicate`/`failed`) -
    ten sam krok co dla e-Doręczeń (connector/edoreczenia/edr_paperless.py).

Wersje dokumentów e-Doręczeń (`edr_*`) obsługuje scripts/edr.py.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from wf.models import Document, DocumentVersion
from wf.schemas.wf_schemas import IngestStatus

logger = logging.getLogger(__name__)

#: typ dokumentu huba -> (typ dokumentu w Paperless albo None, tag)
HUB_DOC_TYPES: dict[str, tuple[str | None, str]] = {
    'plik': (None, 'galwork-plik'),
    'wydruk': ('Wydruk obiegu', 'galwork-wydruk'),
}
#: początek komunikatu wersji odrzuconej (nie wraca do kolejki ponowień)
REJECTED = 'Odrzucono: '
#: typy dokumentów obsługiwane przez scripts/edr.py
EDR_DOC_TYPES = ('edr_in', 'edr_out', 'edr_evidence')


@dataclass
class SubmitResult:
    submitted: int = 0
    retried_later: int = 0
    rejected: int = 0


def pending_versions(db: Session, limit: int) -> list[DocumentVersion]:
    return db.execute(
        select(DocumentVersion).join(Document, Document.id == DocumentVersion.document_id)
        .where(Document.doc_type.in_(list(HUB_DOC_TYPES)),
               DocumentVersion.staging_path.is_not(None),
               or_(DocumentVersion.ingest_status == IngestStatus.STAGED,
                   and_(DocumentVersion.ingest_status == IngestStatus.FAILED,
                        DocumentVersion.paperless_task_id.is_(None),
                        or_(DocumentVersion.ingest_error.is_(None),
                            DocumentVersion.ingest_error.not_like(f'{REJECTED}%')))))
        .order_by(DocumentVersion.uploaded_at)
        .limit(limit)
    ).scalars().all()


def submit_pending(db: Session, paperless, *, limit: int = 50) -> SubmitResult:
    result = SubmitResult()
    for version in pending_versions(db, limit):
        document = version.document
        if not os.path.exists(version.staging_path):
            version.ingest_status = IngestStatus.FAILED
            version.ingest_error = f'{REJECTED}brak pliku w stagingu: {version.staging_path}'
            result.rejected += 1
            continue
        content = paperless.read_staged(version.staging_path)
        if paperless.sha256_of(content) != version.sha256:
            version.ingest_status = IngestStatus.FAILED
            version.ingest_error = (f'{REJECTED}plik w stagingu ma inny skrót niż wersja '
                                    'dokumentu - nie wysłano do archiwum')
            logger.error('document_ingest: niezgodny skrót %s v%s', document.id, version.version_no)
            result.rejected += 1
            continue
        doc_type_name, tag = HUB_DOC_TYPES[document.doc_type]
        try:
            version.paperless_task_id = paperless.upload_document(
                content, version.file_name, title=document.title,
                document_type_id=(paperless.get_or_create_document_type(doc_type_name)
                                  if doc_type_name else None),
                tag_ids=[paperless.get_or_create_tag(tag)])
        except Exception as exc:  # Paperless niedostępny - kolejna próba w następnym cyklu
            version.ingest_status = IngestStatus.STAGED
            version.ingest_error = str(exc)[:1000]
            logger.warning('document_ingest: %s v%s: %s', document.id, version.version_no, exc)
            result.retried_later += 1
            continue
        version.ingest_status = IngestStatus.SUBMITTED
        version.ingest_error = None
        result.submitted += 1
    db.commit()
    return result


def poll_submitted(db: Session, paperless, *, actor, limit: int = 50) -> int:
    from connector.edoreczenia.edr_paperless import poll_submitted_documents
    return poll_submitted_documents(db, paperless, actor=actor, limit=limit)
