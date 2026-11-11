# edu/services/sealing.py
# Pieczęć elektroniczna odpowiedzi dziekanatu (proces edu-pismo).
#
# Po decyzji "Załatwione" albo "Odmowa" (edu/services/case_sync.py) hub:
#   1. pobiera PDF sprawy z szablonu wydruku Frappe (Print Format),
#   2. składa podpis PAdES biblioteką endesive; podpis atrybutów CMS wykonuje
#      usługa galwork-seal (connector/seal, klucz nie opuszcza usługi),
#   3. wgrywa wynik do `Student Correspondence.signed_document` i ustawia
#      `signature_status = Signed`,
#   4. zapisuje w dzienniku instancji `signature_requested`/`signature_completed`
#      albo `instance_error`.
#
# Pieczętowanie działa w tle (wątek), poza żądaniem użytkownika, z
# ponawianiem. Silnik nie wykonuje service tasków (wf/engine.py), więc to
# jedyna droga. Ręczne ponowienie: POST /v1/edu/jform/sprawa/seal.
#
# Konfiguracja: sekcja [seal] (config.ini.example), zmienne SEAL_<KLUCZ>.
from __future__ import annotations

import datetime
import hashlib
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable
from uuid import UUID

from connector.base import ConnectorConfig, BaseConnector
from connector.frappe_education.correspondence import (
  db_get_correspondence,
  db_get_correspondence_pdf,
  db_set_correspondence_signed,
  db_upload_correspondence_file,
)
from connector.seal.connector import RemoteSeal, SealError, get_connector as get_seal_connector
from wf.models import ProcessInstance
from wf.schemas.wf_schemas import EventType
from wf.services.process_service import get_service

logger = logging.getLogger(__name__)

SIGNATURE_MODE = 'advanced_seal'
SIGNED_FIELD = 'signed_document'
SIGNED = 'Signed'
PADES_SUBFILTER = 'ETSI.CAdES.detached'


def _as_bool(value: Any, default: bool = False) -> bool:
  if value is None or str(value).strip() == '':
    return default
  return str(value).strip().lower() in ('1', 'true', 'yes', 'on')


def _as_int(value: Any, default: int) -> int:
  try:
    return int(str(value).strip())
  except (TypeError, ValueError):
    return default


@dataclass(frozen=True)
class SealSettings:
  enabled: bool = False
  models: tuple[str, ...] = ('edu-pismo',)
  print_format: str = ''
  letterhead: str = ''
  subfilter: str = PADES_SUBFILTER
  tspurl: str = ''
  reason: str = 'Pismo opatrzone pieczęcią elektroniczną uczelni'
  location: str = ''
  contact: str = ''
  attempts: int = 3
  retry_delay: int = 30

  @classmethod
  def load(cls, config: ConnectorConfig | None = None) -> 'SealSettings':
    config = config or BaseConnector.load_ini_section('seal')
    get = config.get
    default = cls()
    models = tuple(m.strip() for m in (get('models', '') or '').split(',') if m.strip())
    return cls(
      enabled=_as_bool(get('enabled'), False),
      models=models or default.models,
      print_format=get('print_format', '') or '',
      letterhead=get('letterhead', '') or '',
      subfilter=(get('subfilter', default.subfilter) or '').strip(),
      tspurl=get('tspurl', '') or '',
      reason=get('reason', '') or default.reason,
      location=get('location', '') or '',
      contact=get('contact', '') or '',
      attempts=max(1, _as_int(get('attempts'), default.attempts)),
      retry_delay=max(0, _as_int(get('retry_delay'), default.retry_delay)),
    )


def seal_pdf(document: bytes, remote: RemoteSeal, settings: SealSettings) -> bytes:
  """PDF z pieczęcią PAdES; podpis atrybutów CMS wykonuje usługa (`remote`)."""
  from endesive.pdf import cms  # import leniwy: endesive potrzebny tylko z pieczęcią

  udct: dict[str, Any] = {
    'sigflags': 3,
    'aligned': 16384,
    'reason': settings.reason,
    'location': settings.location,
    'contact': settings.contact,
    'signingdate': datetime.datetime.now(datetime.UTC).strftime("D:%Y%m%d%H%M%S+00'00'"),
  }
  if settings.subfilter:
    udct['subfilter'] = settings.subfilter
  signature = cms.sign(document, udct, None, None, remote.chain(), 'sha256', remote,
                       settings.tspurl or None)
  return document + signature


def _session_factory() -> Callable[[], Any]:
  from db.session import SessionLocal
  return SessionLocal


def _record(db, instance_id: UUID | str, event_type: EventType, payload: dict) -> None:
  instance = db.get(ProcessInstance, UUID(str(instance_id)))
  if instance is None:
    logger.warning('sealing: brak instancji %s (%s)', instance_id, event_type)
    return
  get_service().record_event(db, instance, event_type, payload=payload)


def seal_case(case: str, instance_id: UUID | str, final_status: str | None = None, *,
              settings: SealSettings | None = None, requested_by: str = '') -> str:
  """Pieczętuje odpowiedź w sprawie `case`; zwraca `file_url` opieczętowanego PDF.

  Jedna próba, bez ponawiania (zob. run_case_seal). Sprawa już opieczętowana
  (`signature_status = Signed`) nie jest pieczętowana drugi raz.
  """
  settings = settings or SealSettings.load()
  doc = db_get_correspondence(case)
  if doc.get('signature_status') == SIGNED and doc.get(SIGNED_FIELD):
    return str(doc.get(SIGNED_FIELD))
  status = final_status or str(doc.get('status') or '')
  connector = get_seal_connector()
  requester = requested_by or f'galwork-hub:{instance_id}'

  db = _session_factory()()
  try:
    _record(db, instance_id, EventType.SIGNATURE_REQUESTED, {
      'business_key': case, 'mode': SIGNATURE_MODE, 'keyid': connector.keyid})
    document = db_get_correspondence_pdf(case, settings.print_format, settings.letterhead)
    remote = connector.remote(reference=case, requested_by=requester)
    signed = seal_pdf(document, remote, settings)
    upload = db_upload_correspondence_file(
      case, f'{case}-pieczec.pdf', signed, is_private=True, fieldname=SIGNED_FIELD,
      content_type='application/pdf')
    file_url = str((upload or {}).get('file_url') or '')
    db_set_correspondence_signed(case, status)
    _record(db, instance_id, EventType.SIGNATURE_COMPLETED, {
      'business_key': case, 'mode': SIGNATURE_MODE, 'keyid': connector.keyid,
      'seal': remote.subject(), 'file_url': file_url,
      'document_sha256': hashlib.sha256(document).hexdigest(),
      'signed_sha256': hashlib.sha256(signed).hexdigest(),
    })
    return file_url
  finally:
    db.close()


def _record_failure(case: str, instance_id: UUID | str, exc: Exception, attempts: int) -> None:
  db = _session_factory()()
  try:
    _record(db, instance_id, EventType.INSTANCE_ERROR, {
      'source': 'edu.sealing', 'business_key': case, 'attempts': attempts,
      'error': f'Nie opieczętowano odpowiedzi: {exc}'})
  except Exception:
    logger.exception('sealing: nie zapisano błędu w dzienniku instancji %s', instance_id)
    db.rollback()
  finally:
    db.close()


def run_case_seal(case: str, instance_id: UUID | str, final_status: str | None = None,
                  settings: SealSettings | None = None) -> str | None:
  """Pieczętowanie z ponawianiem; błąd końcowy trafia do dziennika instancji."""
  settings = settings or SealSettings.load()
  last: Exception | None = None
  attempt = 0
  for attempt in range(1, settings.attempts + 1):
    try:
      return seal_case(case, instance_id, final_status, settings=settings)
    except SealError as exc:
      last = exc
      if exc.permanent:
        break
    except Exception as exc:  # Frappe, sieć, Valkey
      last = exc
    logger.warning('sealing %s: próba %d/%d nieudana: %s', case, attempt, settings.attempts, last)
    if attempt < settings.attempts and settings.retry_delay:
      time.sleep(settings.retry_delay * attempt)
  logger.error('sealing %s: nie opieczętowano: %s', case, last)
  _record_failure(case, instance_id, last or RuntimeError('nieznany błąd'), attempt)
  return None


_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix='edu-seal')


def _submit(fn: Callable[..., Any], *args: Any) -> Any:
  return _executor.submit(fn, *args)


def schedule_case_seal(case: str, instance_id: UUID | str, model_key: str,
                       final_status: str) -> bool:
  """Zleca pieczętowanie w tle, jeśli pieczęć jest włączona dla modelu procesu."""
  settings = SealSettings.load()
  if not settings.enabled or model_key not in settings.models:
    return False
  _submit(run_case_seal, case, str(instance_id), final_status, settings)
  return True
