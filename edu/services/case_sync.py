# edu/services/case_sync.py
# Synchronizacja sprawy studenta (Frappe Education, `Student Correspondence`)
# z przebiegiem procesu `edu-*` w `wf`.
#
# Słuchacz zdarzeń z wf/services/process_hooks.py (rejestrowany w router.py).
# Sprawę wskazuje `ProcessInstance.business_key` (nazwa dokumentu Frappe),
# ustawiany przez POST /v1/edu/jform/sprawa/submit. Hub zapisuje we Frappe
# tylko identyfikator instancji, status i odpowiedź dziekanatu - przebieg
# (zadania, dane formularzy) zostaje w `wf`.
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from connector.frappe_education.correspondence import (
    correspondence_form_data,
    db_get_correspondence,
    db_record_correspondence_response,
    db_set_correspondence_process,
    db_set_correspondence_status,
    db_update_correspondence_form,
)
# 2026-11-06: pieczęć elektroniczna odpowiedzi (edu/services/sealing.py).
from edu.services.sealing import schedule_case_seal
from wf.models import Notification, ProcessInstance
from wf.schemas.wf_schemas import EventType, ProcessInstanceStatus
from wf.services import process_hooks
from wf.services.process_service import get_service

logger = logging.getLogger(__name__)

MODEL_PREFIX = 'edu-'

# Proces edu-pismo (spiffworkflow/edu/edu-pismo/edu-pismo.bpmn).
REVIEW_TASK = 'Task_Rozpatrzenie'
SUPPLEMENT_TASK = 'Task_Uzupelnienie'
FLOW_REJECTED = 'Flow_Odmowa'
FLOW_SUPPLEMENT = 'Flow_Uzupelnienie'

STATUS_SUBMITTED = 'Submitted'
STATUS_IN_PROGRESS = 'In Progress'
STATUS_COMPLETED = 'Completed'
STATUS_REJECTED = 'Rejected'


def model_key(instance: ProcessInstance) -> str:
  try:
    return instance.version.process_model.key or ''
  except AttributeError:
    return ''


def case_name(instance: ProcessInstance) -> str | None:
  """Nazwa sprawy Frappe, jeśli instancja jest procesem modułu edu."""
  key = model_key(instance)
  if not key or not key.startswith(MODEL_PREFIX) or not instance.business_key:
    return None
  return instance.business_key


def review_status(instance: ProcessInstance, chosen_path: str | None) -> str:
  """Status sprawy po decyzji dziekanatu (zadanie "Rozpatrzenie")."""
  if chosen_path == FLOW_SUPPLEMENT or instance.status != ProcessInstanceStatus.COMPLETED:
    return STATUS_IN_PROGRESS
  if chosen_path == FLOW_REJECTED:
    return STATUS_REJECTED
  return STATUS_COMPLETED


_STATUS_TITLES = {
  STATUS_IN_PROGRESS: 'Dziekanat prosi o uzupełnienie pisma',
  STATUS_COMPLETED: 'Dziekanat odpowiedział na pismo',
  STATUS_REJECTED: 'Dziekanat odmówił załatwienia sprawy',
}


def _notify_student(db: Session, instance: ProcessInstance, case: str, status: str) -> None:
  if not instance.started_by_id:
    return
  db.add(Notification(
    user_id=instance.started_by_id,
    kind='edu-pismo',
    title=f"{_STATUS_TITLES.get(status, 'Zmiana statusu sprawy')}: {instance.title}",
    link='/docs',
    payload={'case': case, 'status': status, 'process_instance_id': str(instance.id)},
  ))
  db.commit()


def _record_failure(db: Session, instance: ProcessInstance, kind: str, exc: Exception) -> None:
  """Błąd zapisu do Frappe trafia do dziennika instancji (krok procesu zostaje)."""
  logger.warning('case_sync %s (%s): %s', kind, instance.business_key, exc)
  try:
    get_service().record_event(db, instance, EventType.INSTANCE_ERROR, payload={
      'source': 'edu.case_sync', 'error': f'Nie zapisano zmiany w Frappe ({kind}): {exc}'})
  except Exception:
    db.rollback()


def on_process_event(db: Session, event: process_hooks.ProcessHookEvent) -> None:
  instance = event.instance
  case = case_name(instance)
  if case is None:
    return
  try:
    if event.kind == process_hooks.INSTANCE_STARTED:
      db_set_correspondence_process(case, str(instance.id))
      db_set_correspondence_status(case, STATUS_SUBMITTED)
      return
    if event.kind != process_hooks.TASK_COMPLETED or event.human_task is None:
      return
    task_id = event.human_task.bpmn_task_id
    if task_id == REVIEW_TASK:
      status = review_status(instance, event.form_data.get('chosen_path'))
      db_record_correspondence_response(case, str(event.form_data.get('odpowiedz') or ''), status)
      _notify_student(db, instance, case, status)
      if status in (STATUS_COMPLETED, STATUS_REJECTED):
        # odpowiedź jest już we Frappe, więc trafi do wydruku z pieczęcią
        schedule_case_seal(case, instance.id, model_key(instance), status)
    elif task_id == SUPPLEMENT_TASK:
      # Uzupełnienie wraca do dziekanatu; treść dopisujemy do danych sprawy.
      form = correspondence_form_data(db_get_correspondence(case))
      form['uzupelnienie'] = event.form_data.get('uzupelnienie') or ''
      db_update_correspondence_form(case, form)
      db_set_correspondence_status(case, STATUS_SUBMITTED)
  except Exception as exc:
    _record_failure(db, instance, f'{event.kind}', exc)


def register() -> None:
  process_hooks.register(on_process_event)
