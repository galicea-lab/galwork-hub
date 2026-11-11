# wf/services/process_hooks.py
# Słuchacze zdarzeń instancji procesu.
#
# Silnik nie wykonuje serviceTask/scriptTask (wf/engine.py), więc integracje,
# które mają zareagować na przebieg procesu (np. moduł edu aktualizuje sprawę
# w Frappe Education), rejestrują tu funkcję. ProcessService wywołuje
# słuchaczy PO commicie: błąd integracji nie cofa kroku procesu, tylko trafia
# do logu (i ewentualnie do dziennika instancji, jeśli słuchacz tak zrobi).
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from sqlalchemy.orm import Session

from wf.models import HumanTask, ProcessInstance
from wf.schemas.wf_schemas import CurrentUser

logger = logging.getLogger(__name__)

INSTANCE_STARTED = 'instance_started'
TASK_COMPLETED = 'task_completed'


@dataclass(frozen=True)
class ProcessHookEvent:
  kind: str
  instance: ProcessInstance
  actor: CurrentUser | None = None
  human_task: HumanTask | None = None
  form_data: Mapping[str, Any] = field(default_factory=dict)


ProcessListener = Callable[[Session, ProcessHookEvent], None]

_listeners: list[ProcessListener] = []


def register(listener: ProcessListener) -> None:
  if listener not in _listeners:
    _listeners.append(listener)


def unregister(listener: ProcessListener) -> None:
  if listener in _listeners:
    _listeners.remove(listener)


def emit(db: Session, event: ProcessHookEvent) -> None:
  for listener in list(_listeners):
    try:
      listener(db, event)
    except Exception:
      logger.exception('Słuchacz %s zdarzenia %s (instancja %s) zgłosił błąd',
                       getattr(listener, '__name__', listener), event.kind, event.instance.id)
      db.rollback()
