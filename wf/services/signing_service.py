# wf/services/signing_service.py
# 2026-11-13: podpis dokumentu przez Signius jako kroki systemowe procesu
# (`serviceTask` z `handler` = signius.request / signius.wait) - wspólny
# podproces galwork-models/wspolne/podpis-dokumentu.
#
# W odróżnieniu od wf/routers/signius_api.py (korespondencja studencka we
# Frappe) podpisywany jest dokument huba (`Document`, Paperless-ngx) wskazany
# w danych procesu jako `dokument_id`; podpisany PDF zostaje kolejną wersją
# tego dokumentu (staging -> istniejąca wysyłka do Paperless).
from __future__ import annotations

import os
import uuid

from connector.paperless.connector import get_connector as get_paperless_connector
from connector.signius.connector import get_connector as get_signius_connector
from wf.models import Document, DocumentVersion
from wf.schemas.wf_schemas import DocumentLinkRole, EventType
from wf.services import service_tasks
from wf.services.service_tasks import ServiceContext, ServiceResult, ServiceTaskError

#: status Signius dokumentu podpisanego / zakończonego bez podpisu
SIGNED = 'SIGNED'
NOT_SIGNED = {'REJECTED', 'DECLINED', 'CANCELLED', 'CANCELED', 'EXPIRED', 'DELETED'}


def _document(ctx: ServiceContext) -> Document:
  raw = ctx.data.get('dokument_id')
  try:
    document = ctx.db.get(Document, uuid.UUID(str(raw)))
  except (TypeError, ValueError):
    document = None
  if document is None:
    raise ServiceTaskError(f"Brak dokumentu do podpisu (dokument_id={raw!r})")
  return document


def _content(document: Document) -> tuple[str, bytes]:
  """Nazwa pliku i treść bieżącej wersji dokumentu."""
  version = document.current_version or (document.versions[-1] if document.versions else None)
  if version is None:
    raise ServiceTaskError(f"Dokument '{document.title}' nie ma żadnej wersji pliku")
  if version.staging_path and os.path.exists(version.staging_path):
    with open(version.staging_path, 'rb') as handle:
      return version.file_name, handle.read()
  paperless_id = version.paperless_version_id or document.paperless_document_id
  if paperless_id:
    return version.file_name, get_paperless_connector().download_document(paperless_id, original=True)
  raise ServiceTaskError(f"Plik dokumentu '{document.title}' jest niedostępny")


@service_tasks.handler('dokumenty.glowny')
def main_document(ctx: ServiceContext) -> ServiceResult:
  """Dokument do dalszych kroków: `dokument_id` z procesu wywołującego, a bez
  niego główny dokument instancji (rola `main`, inaczej ostatnio dodany).
  Brak dokumentu - ponowienie (dodaj dokument i "Sprawdź teraz")."""
  if ctx.data.get('dokument_id'):
    document = _document(ctx)
  else:
    links = sorted(ctx.instance.document_links,
                   key=lambda link: (link.role != DocumentLinkRole.MAIN, -link.added_at.timestamp()))
    if not links:
      raise ServiceTaskError("Sprawa nie ma dołączonego dokumentu do podpisu - dodaj dokument")
    document = links[0].document
  return service_tasks.done({'dokument_id': str(document.id), 'dokument_tytul': document.title})


@service_tasks.handler('signius.request')
def request_signature(ctx: ServiceContext) -> ServiceResult:
  """Wysyła dokument do podpisu. Wejście: `dokument_id`,
  `podpisujacy_email`, opcjonalnie `podpisujacy_telefon`,
  `podpis_kwalifikowany`, `tytul`. Wyjście: `signius_folder_id`,
  `signius_document_id`."""
  document = _document(ctx)
  email = (ctx.data.get('podpisujacy_email') or '').strip()
  if not email:
    raise ServiceTaskError("Brak adresu e-mail podpisującego (podpisujacy_email)")
  file_name, content = _content(document)
  signius = get_signius_connector()
  folder_id = ctx.data.get('signius_folder_id')
  if not folder_id:
    folder_id = signius.create_folder(f"{ctx.instance.title} ({ctx.instance.id})")['documentFolderId']
  document_id = signius.upload_document(folder_id, file_name, content)['documentId']
  signius.request_signature(
    document_id, email, (ctx.data.get('podpisujacy_telefon') or '').strip(),
    title=ctx.data.get('tytul') or document.title,
    qualified=bool(ctx.data.get('podpis_kwalifikowany')))
  _event(ctx, EventType.SIGNATURE_REQUESTED, {
    'document_id': str(document.id), 'signius_document_id': document_id, 'signee_email': email})
  return service_tasks.done({'signius_folder_id': folder_id, 'signius_document_id': document_id})


@service_tasks.handler('signius.wait')
def wait_for_signature(ctx: ServiceContext) -> ServiceResult:
  """Sprawdza status w Signius (brak webhooka - odpytywanie). Podpisany:
  nowa wersja dokumentu i `wynik_podpisu = "podpisany"`; odrzucony/
  wygasły: `wynik_podpisu = "odrzucony"`; inaczej ponowienie po
  `retryMinutes`."""
  folder_id, signius_id = ctx.data.get('signius_folder_id'), ctx.data.get('signius_document_id')
  if not folder_id or not signius_id:
    raise ServiceTaskError("Brak identyfikatorów Signius - krok 'signius.request' nie został wykonany")
  signius = get_signius_connector()
  status = signius.get_document_status(signius_id, folder_id)
  if status == SIGNED:
    document = _document(ctx)
    content = signius.get_signed_document(signius_id)
    version_no = _add_signed_version(ctx, document, content)
    _event(ctx, EventType.SIGNATURE_COMPLETED, {
      'document_id': str(document.id), 'signius_document_id': signius_id, 'version_no': version_no})
    return service_tasks.done({'wynik_podpisu': 'podpisany', 'podpisany_dokument_id': str(document.id),
                               'status_signius': status})
  if status is None or str(status).upper() in NOT_SIGNED:
    # wyjście ioSpecification musi istnieć także bez podpisu
    return service_tasks.done({'wynik_podpisu': 'odrzucony', 'podpisany_dokument_id': None,
                               'status_signius': status or 'brak w folderze'})
  return service_tasks.retry(ctx.retry_after())


def _add_signed_version(ctx: ServiceContext, document: Document, content: bytes) -> int:
  paperless = get_paperless_connector()
  version_no = max([v.version_no for v in document.versions] or [0]) + 1
  base = os.path.splitext((document.current_version.file_name if document.current_version
                           else document.title) or 'dokument')[0]
  file_name = f"{base}-podpisany.pdf"
  ctx.db.add(DocumentVersion(
    document_id=document.id, version_no=version_no, label='Podpisany (Signius)',
    file_name=file_name, mime_type='application/pdf', size_bytes=len(content),
    sha256=paperless.sha256_of(content), staging_path=paperless.stage_bytes(content, file_name),
    uploaded_by_id=ctx.actor.id))
  document.current_version_no = version_no
  ctx.db.flush()
  _event(ctx, EventType.DOCUMENT_VERSION_ADDED, {'document_id': str(document.id), 'version_no': version_no})
  return version_no


def _event(ctx: ServiceContext, event_type: EventType, payload: dict) -> None:
  from wf.services.process_service import get_service  # bez cyklu importów
  # bez commita - zapis razem z postępem procesu (ProcessService.advance_system),
  # żeby ponowienie po awarii nie dodało drugiej podpisanej wersji
  get_service()._add_event(ctx.db, ctx.instance, event_type, actor=ctx.actor, payload=payload)
