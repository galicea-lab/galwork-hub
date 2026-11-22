# wf/services/seal_service_tasks.py
# 2026-11-17:  pieczęć elektroniczna dokumentu sprawy jako krok systemowy
# (`serviceTask` z `handler` = pieczec.dokument), np. opublikowany akt
# wewnętrzny w repozytorium regulacji.
"""
Pieczęć PAdES na dokumencie huba (`Document`, Paperless-ngx).

Wejście: `dokument_id` w danych procesu (np. z kroku `dokumenty.glowny`),
a bez niego główny dokument sprawy. Pieczętowana jest bieżąca wersja, po
sprawdzeniu jej skrótu sha256 (wf/services/instance_documents.py) - hub nie
opieczętuje treści podmienionej w magazynie. Wynik to kolejna wersja tego
samego dokumentu; poprzednie zostają.

Składanie PAdES jak w edu/services/sealing.py (`seal_pdf`, endesive);
podpis atrybutów CMS wykonuje usługa galwork-seal (klucz nie opuszcza HSM).

Właściwości elementu BPMN:
  * `reason` - powód w podpisie (domyślnie `[seal] reason`),
  * `required` - `false`: przy wyłączonej pieczęci (`[seal] enabled=false`)
    krok kończy się bez niej (zdarzenie w dzienniku); domyślnie krok czeka,
  * `source` - źródło zlecenia dla polityki galwork-seal
    (`keys.<keyid>.sources`); domyślnie klucz modelu procesu,
  * `retryMinutes` - odstęp ponowień przy błędzie.

Krok jest idempotentny: dokument, którego bieżąca wersja jest już
opieczętowana (`Document.metadata.pieczec`), nie jest pieczętowany drugi raz -
ten sam krok może więc stać i przy publikacji zarządzenia, i przy rejestracji
w repozytorium.
"""
from __future__ import annotations

import dataclasses
import hashlib
import logging
import os
import uuid
from datetime import UTC, datetime

from sqlalchemy.orm.attributes import flag_modified

from connector.base import ConnectorConfigError
from wf.models import Document, DocumentVersion
from wf.schemas.wf_schemas import DocumentLinkRole, EventType, IngestStatus
from wf.services import instance_documents, service_tasks
from wf.services.service_tasks import ServiceContext, ServiceResult, ServiceTaskError

logger = logging.getLogger(__name__)

SIGNATURE_MODE = 'advanced_seal'
DEFAULT_REASON = 'Dokument opatrzony pieczęcią elektroniczną'
# rezygnacja z pieczęci dla pojedynczej sprawy (formularz startowy).
SEAL_OPT_OUT_FIELD = 'Czy opieczętować elektronicznie?'


def _false(value) -> bool:
  # False jest falsy - stare `value or ''` gubiło wartość z JSON.
  if isinstance(value, bool):
    return not value
  return str(value if value is not None else '').strip().lower() in ('false', '0', 'nie', 'no')


def _paperless():
  from connector.paperless import get_connector
  return get_connector()


def _seal_connector():
  from connector.seal.connector import get_connector
  return get_connector()


def _target_document(ctx: ServiceContext) -> Document:
  raw = ctx.data.get('dokument_id')
  if raw:
    try:
      document = ctx.db.get(Document, uuid.UUID(str(raw)))
    except (TypeError, ValueError):
      document = None
    if document is None:
      raise ServiceTaskError(f"Brak dokumentu do opieczętowania (dokument_id={raw!r})")
    return document
  links = sorted(ctx.instance.document_links,
                 key=lambda link: (link.role != DocumentLinkRole.MAIN, -link.added_at.timestamp()))
  if not links:
    raise ServiceTaskError("Sprawa nie ma dokumentu do opieczętowania - dodaj dokument")
  return links[0].document


def _event(ctx: ServiceContext, event_type: EventType, payload: dict) -> None:
  from wf.services.process_service import get_service  # bez cyklu importów
  # bez commita - zapis razem z postępem procesu (ProcessService.advance_system)
  get_service()._add_event(ctx.db, ctx.instance, event_type, actor=ctx.actor, payload=payload)


def _settings(ctx: ServiceContext):
  from edu.services.sealing import SealSettings
  settings = SealSettings.load()
  reason = (ctx.params.get('reason') or '').strip()
  if reason:
    return dataclasses.replace(settings, reason=reason)
  if settings.reason == SealSettings().reason:  # domyślny powód dotyczy pism edu
    return dataclasses.replace(settings, reason=DEFAULT_REASON)
  return settings


@service_tasks.handler('pieczec.dokument')
def seal_document(ctx: ServiceContext) -> ServiceResult:
  from connector.seal.connector import RemoteSeal, SealError
  from edu.services.sealing import seal_pdf

  document = _target_document(ctx)
  sealed = (document.doc_metadata or {}).get('pieczec') or {}
  if sealed.get('version_no') and sealed.get('version_no') == document.current_version_no:
    return service_tasks.done({'dokument_id': str(document.id), 'pieczec_wersja': sealed['version_no'],
                               'pieczec_status': 'opieczętowany'})

  # odznaczone pole w danych startowych wyłącza ten krok dla tej sprawy.
  if SEAL_OPT_OUT_FIELD in ctx.data and _false(ctx.data[SEAL_OPT_OUT_FIELD]):
    _event(ctx, EventType.SIGNATURE_COMPLETED, {
      'document_id': str(document.id), 'mode': SIGNATURE_MODE, 'skipped': True,
      'reason': f'rezygnacja z pieczęci w formularzu ({SEAL_OPT_OUT_FIELD})'})
    return service_tasks.done({'dokument_id': str(document.id),
                               'pieczec_status': 'pominięta (formularz)'})

  try:
    source_content = instance_documents.version_content(ctx.db, document)
  except instance_documents.DocumentError as exc:
    raise ServiceTaskError(f"Dokument '{document.title}': {exc}") from exc
  if source_content.mime_type != 'application/pdf':
    raise ServiceTaskError(f"Pieczęć wymaga PDF - dokument '{document.title}' to "
                           f"{source_content.file_name}; dodaj wersję PDF i użyj „Sprawdź teraz”")

  # 2026-11-17:  sprawa testowa (wdrożenie obiegu) - bez pieczęci uczelni
  if ctx.instance.usage_mode == 'test':
    _event(ctx, EventType.SIGNATURE_COMPLETED, {
      'document_id': str(document.id), 'mode': SIGNATURE_MODE, 'skipped': True,
      'reason': 'sprawa prowadzona w trybie testowym'})
    return service_tasks.done({'dokument_id': str(document.id), 'pieczec_status': 'pominięta (test)'})

  settings = _settings(ctx)
  if not settings.enabled:
    if _false(ctx.params.get('required')):
      _event(ctx, EventType.SIGNATURE_COMPLETED, {
        'document_id': str(document.id), 'mode': SIGNATURE_MODE, 'skipped': True,
        'reason': 'pieczęć wyłączona w konfiguracji ([seal] enabled)'})
      return service_tasks.done({'dokument_id': str(document.id), 'pieczec_status': 'pominięta'})
    raise ServiceTaskError("Pieczęć elektroniczna jest wyłączona w konfiguracji huba "
                           "([seal] enabled) - krok czeka na jej włączenie")

  connector = _seal_connector()
  keyid = connector.keyid
  # źródło zlecenia - galwork-seal dopuszcza klucz tylko dla wskazanych źródeł
  # (keys.<keyid>.sources); domyślnie klucz modelu procesu, jak `edu-pismo`
  source = (ctx.params.get('source') or '').strip() or ctx.instance.process_model_key
  source_version_no = document.current_version_no
  _event(ctx, EventType.SIGNATURE_REQUESTED, {
    'document_id': str(document.id), 'version_no': source_version_no, 'mode': SIGNATURE_MODE,
    'keyid': keyid, 'source': source, 'document_sha256': source_content.sha256})
  try:
    remote = RemoteSeal(connector.connect(), keyid, source=source, reference=str(document.id),
                        requested_by=f'galwork-hub:{ctx.instance.id}', timeout=connector.timeout)
    signed = seal_pdf(source_content.content, remote, settings)
  except SealError as exc:
    raise ServiceTaskError(f"Usługa pieczęci odmówiła ({exc.code}): {exc}") from exc
  except ConnectorConfigError as exc:
    raise ServiceTaskError(f"Usługa pieczęci jest błędnie skonfigurowana: {exc}") from exc

  version_no = _add_sealed_version(ctx, document, source_content.file_name, signed)
  signed_sha256 = hashlib.sha256(signed).hexdigest()
  metadata = dict(document.doc_metadata or {})
  metadata['pieczec'] = {
    'version_no': version_no, 'source_version_no': source_version_no,
    'sha256': signed_sha256, 'source_sha256': source_content.sha256,
    'seal': remote.subject(), 'keyid': keyid, 'at': datetime.now(UTC).isoformat()}
  # 2026-11-17:  wszystkie wersje opieczętowane (kolejne redakcje aktu)
  metadata['pieczec_wersje'] = sorted({*metadata.get('pieczec_wersje', []), version_no})
  document.doc_metadata = metadata
  flag_modified(document, 'doc_metadata')
  ctx.db.flush()
  _event(ctx, EventType.SIGNATURE_COMPLETED, {
    'document_id': str(document.id), 'mode': SIGNATURE_MODE, 'keyid': keyid,
    'seal': remote.subject(), 'version_no': version_no, 'source_version_no': source_version_no,
    'document_sha256': source_content.sha256, 'signed_sha256': signed_sha256})
  return service_tasks.done({'dokument_id': str(document.id), 'pieczec_wersja': version_no,
                             'pieczec_status': 'opieczętowany'})


def _add_sealed_version(ctx: ServiceContext, document: Document, source_name: str,
                        content: bytes) -> int:
  paperless = _paperless()
  source = document.current_version
  version_no = max([v.version_no for v in document.versions] or [0]) + 1
  base = os.path.splitext(source_name or document.title or 'dokument')[0]
  file_name = f"{base}-pieczec.pdf"
  version = DocumentVersion(
    document_id=document.id, version_no=version_no, label='Opieczętowany (pieczęć elektroniczna)',
    file_name=file_name, mime_type='application/pdf', size_bytes=len(content),
    sha256=paperless.sha256_of(content), staging_path=paperless.stage_bytes(content, file_name),
    uploaded_by_id=ctx.actor.id,
    # 2026-11-17:  wersja opieczętowana to ta sama redakcja aktu
    effective_from=source.effective_from if source is not None else None)
  ctx.db.add(version)
  document.current_version_no = version_no
  ctx.db.flush()
  try:
    version.paperless_task_id = paperless.upload_document(
      content, file_name, title=document.title,
      tag_ids=[paperless.get_or_create_tag('galwork-pieczec')])
    version.ingest_status = IngestStatus.SUBMITTED
  except Exception as exc:  # Paperless chwilowo niedostępny - zostaje `staged`
    logger.warning('Pieczęć %s: wysyłka do Paperless nie powiodła się: %s', document.id, exc)
    version.ingest_error = str(exc)[:1000]
  _event(ctx, EventType.DOCUMENT_VERSION_ADDED, {
    'document_id': str(document.id), 'version_no': version_no, 'sha256': version.sha256,
    'label': version.label})
  return version_no
