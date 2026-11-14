# wf/services/service_tasks.py
# 2026-11-13: kroki systemowe procesu (`serviceTask` z właściwością
# `handler`). Silnik zatrzymuje proces na serviceTask (stan STARTED);
# ProcessService.advance_system wywołuje tu zarejestrowaną obsługę, a ta
# kończy krok od razu (`done`) albo prosi o ponowienie po czasie (`retry`) -
# wtedy instancja dostaje `next_wakeup_at` i budzi ją scripts/wf_wake.py
# (cron) albo POST /process-instances/{id}/wake.
#
# Rejestracja obsługi:
#     from wf.services import service_tasks
#
#     @service_tasks.handler('modul.akcja')
#     def akcja(ctx: service_tasks.ServiceContext) -> service_tasks.ServiceResult:
#         ...
#         return service_tasks.done({'wynik': 'ok'})
from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Callable, Mapping

from sqlalchemy.orm import Session

from wf.models import ProcessInstance
from wf.schemas.wf_schemas import CurrentUser

DEFAULT_RETRY = timedelta(minutes=15)

# moduły z wbudowaną obsługą - importowane leniwie (bez cyklu importów)
_BUILTIN_MODULES = ('wf.services.signing_service',)


class ServiceTaskError(RuntimeError):
  """Obsługa nie może wykonać kroku (np. brak danych wejściowych)."""


@dataclass(frozen=True)
class ServiceContext:
  db: Session
  instance: ProcessInstance
  actor: CurrentUser
  element_id: str
  name: str
  # dane kroku (dla podprocesu - tylko jego dane, zob. ioSpecification)
  data: Mapping[str, Any]
  # pozostałe właściwości elementu w BPMN
  params: Mapping[str, str] = field(default_factory=dict)

  def retry_after(self) -> timedelta:
    """Odstęp ponowienia z właściwości `retryMinutes` (domyślnie 15 min)."""
    try:
      return timedelta(minutes=float(self.params.get('retryMinutes') or 0)) or DEFAULT_RETRY
    except ValueError:
      return DEFAULT_RETRY


@dataclass(frozen=True)
class ServiceResult:
  completed: bool
  data: Mapping[str, Any] = field(default_factory=dict)
  retry_after: timedelta | None = None


def done(data: Mapping[str, Any] | None = None) -> ServiceResult:
  """Krok zakończony - `data` trafia do danych procesu."""
  return ServiceResult(completed=True, data=dict(data or {}))


def retry(after: timedelta | None = None) -> ServiceResult:
  """Wynik jeszcze niegotowy - sprawdź ponownie po `after`."""
  return ServiceResult(completed=False, retry_after=after or DEFAULT_RETRY)


ServiceHandler = Callable[[ServiceContext], ServiceResult]
_handlers: dict[str, ServiceHandler] = {}
_builtins_loaded = False


def handler(name: str) -> Callable[[ServiceHandler], ServiceHandler]:
  """Dekorator rejestrujący obsługę `name` (wartość `handler` w BPMN)."""
  def register(fn: ServiceHandler) -> ServiceHandler:
    _handlers[name] = fn
    return fn
  return register


def get_handler(name: str) -> ServiceHandler | None:
  global _builtins_loaded
  if not _builtins_loaded:
    _builtins_loaded = True
    for module in _BUILTIN_MODULES:
      importlib.import_module(module)
  return _handlers.get(name)


def registered() -> list[str]:
  get_handler('')
  return sorted(_handlers)
