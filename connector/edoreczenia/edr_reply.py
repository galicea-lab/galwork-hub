# coding: utf-8
# Odpowiedź na korespondencję przychodzącą.
"""Słuchacz procesu `edr-korespondencja` (wf/services/process_hooks.py).

Gdy w zadaniu "Przegląd korespondencji" (`task_przeglad`) zapada decyzja
`wymaga_odpowiedzi`, startuje proces wysyłki `edr-wysylka` z adresem nadawcy
jako adresatem i tematem "Odp: ...". Kancelaria dostaje od razu zadanie
"Przygotowanie przesyłki" z wypełnionymi polami.

Powiązanie spraw: `summary.reply_instance_id` w sprawie przychodzącej,
`summary.reply_to_instance_id` i `summary.reply_to_message_id` w wysyłce.
Błąd (np. brak opublikowanego `edr-wysylka`) nie cofa przeglądu - trafia do
dziennika sprawy przychodzącej (`instance_error`).
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from connector.edoreczenia.edr_paperless import PROCESS_MODEL_KEY, SEND_PROCESS_MODEL_KEY
from wf.models import ProcessInstance
from wf.schemas.wf_schemas import EventType
from wf.services import process_hooks
from wf.services.process_service import get_service

log = logging.getLogger(__name__)

REVIEW_TASK = 'task_przeglad'
NEEDS_REPLY = 'wymaga_odpowiedzi'
_REPLY_PREFIX = 'Odp: '


def _model_key(instance: ProcessInstance) -> str | None:
  version = instance.version
  return version.process_model.key if version is not None and version.process_model else None


def reply_data(instance: ProcessInstance) -> dict:
  """Dane startowe wysyłki z danych sprawy przychodzącej."""
  summary = instance.summary or {}
  subject = (summary.get('subject') or instance.title or '').strip()
  if subject and not subject.lower().startswith(_REPLY_PREFIX.lower()):
    subject = _REPLY_PREFIX + subject
  return {
    'ade_to': summary.get('ade_from') or '',
    'subject': subject[:255],
    'reply_to_instance_id': str(instance.id),
    'reply_to_message_id': instance.business_key or '',
  }


def start_reply(db: Session, instance: ProcessInstance, actor) -> ProcessInstance:
  service = get_service()
  version = service.published_version(db, SEND_PROCESS_MODEL_KEY)
  data = reply_data(instance)
  reply = service.start_instance(
    db, version, title=data['subject'] or f'Odpowiedź: {instance.title}', business_key=None,
    data=data, actor=actor)
  instance.summary = {**(instance.summary or {}), 'reply_instance_id': str(reply.id)}
  db.add(instance)
  db.commit()
  return reply


def on_process_event(db: Session, event: process_hooks.ProcessHookEvent) -> None:
  if event.kind != process_hooks.TASK_COMPLETED or event.human_task is None:
    return
  if event.human_task.bpmn_task_id != REVIEW_TASK:
    return
  if event.form_data.get('decyzja') != NEEDS_REPLY:
    return
  instance = event.instance
  if _model_key(instance) != PROCESS_MODEL_KEY:
    return
  if (instance.summary or {}).get('reply_instance_id'):
    return  # odpowiedź już założona
  try:
    start_reply(db, instance, event.actor)
  except Exception as exc:
    db.rollback()
    log.warning('edr_reply: %s -> %s', instance.id, exc)
    try:
      get_service().record_event(db, instance, EventType.INSTANCE_ERROR, payload={
        'source': 'edr_reply', 'error': f'Nie założono wysyłki odpowiedzi: {exc}'})
    except Exception:
      db.rollback()


def register() -> None:
  process_hooks.register(on_process_event)
