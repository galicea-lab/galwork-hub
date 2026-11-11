# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

# Zbieranie zdarzeń ze źródeł. Źródła działają równolegle,
# każde w kopii kontekstu wywołania (contextvars - np. wywołania Frappe w
# imieniu użytkownika, connector/frappe_education/user_context.py). Wynik
# jest częściowy: awaria jednego źródła nie zostawia pustego kalendarza,
# tylko oznacza je w `zrodla` (ok=False, komunikat).

from __future__ import annotations

import contextvars
import logging
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from datetime import date

from connector.frappe_education.connector import error_message
from kalendarz.schemas import StatusZrodla, Zdarzenie
from kalendarz.zrodla import Kontekst, Zrodlo

log = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix='kalendarz')


def _komunikat(exc: Exception) -> str:
  # komunikat Frappe dla użytkownika albo bezpieczny opis; bez szczegółów
  # technicznych (adresy, tokeny) w odpowiedzi
  try:
    return error_message(exc)
  except Exception:
    return type(exc).__name__


def _sort_key(zdarzenie: Zdarzenie) -> tuple:
  # całodniowe ('2026-01-01') przed czasowymi tego dnia ('2026-01-01T08:00')
  return (zdarzenie.start[:10], not zdarzenie.allDay, zdarzenie.start, zdarzenie.id)


def zbierz(ctx: Kontekst, zrodla: list[Zrodlo], od: date, do: date,
           timeout: float) -> tuple[list[Zdarzenie], list[StatusZrodla]]:
  statusy: dict[str, StatusZrodla] = {}
  zadania = {}
  for zrodlo in zrodla:
    status = StatusZrodla(id=zrodlo.id, nazwa=zrodlo.nazwa)
    statusy[zrodlo.id] = status
    if not zrodlo.dotyczy(ctx):
      status.dotyczy = False
      continue
    run = contextvars.copy_context().run
    zadania[zrodlo.id] = _executor.submit(run, zrodlo.zdarzenia, ctx, od, do)

  zdarzenia: list[Zdarzenie] = []
  koniec = time.monotonic() + timeout
  for zrodlo_id, future in zadania.items():
    status = statusy[zrodlo_id]
    try:
      wynik = future.result(timeout=max(0.0, koniec - time.monotonic()))
    except FutureTimeout:
      status.ok = False
      status.komunikat = 'Źródło nie odpowiedziało na czas'
      log.warning('kalendarz: %s - przekroczony czas', zrodlo_id)
      continue
    except Exception as exc:
      status.ok = False
      status.komunikat = _komunikat(exc)
      log.warning('kalendarz: %s - %s', zrodlo_id, type(exc).__name__)
      continue
    status.liczba = len(wynik)
    zdarzenia += wynik

  return sorted(zdarzenia, key=_sort_key), list(statusy.values())
