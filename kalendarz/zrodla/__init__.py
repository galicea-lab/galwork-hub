# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

# 2026-11-10: źródła zdarzeń kalendarza. Każde źródło dostaje kontekst
# użytkownika i zakres dat (włącznie) i zwraca listę `Zdarzenie`. Rejestr
# kolejnością wyznacza kolejność na liście źródeł w odpowiedzi.

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Optional
from zoneinfo import ZoneInfo

from kalendarz.schemas import Zdarzenie
from wf.schemas.wf_schemas import CurrentUser


@dataclass
class Kontekst:
  """Dla kogo zbieramy kalendarz.

  `nr_albumu` / `email_wykladowcy` są już sprawdzone (edu.dependencies):
  student dostaje tylko siebie, dziekanat - wskazaną osobę.
  """
  current_user: CurrentUser
  strefa: ZoneInfo
  db: Any = None  # sesja SQLAlchemy (źródła z bazy huba)
  nr_albumu: Optional[str] = None
  email_wykladowcy: Optional[str] = None


class Zrodlo:
  id: str = ''
  nazwa: str = ''
  opis: str = ''

  def skonfigurowane(self) -> bool:
    """Czy źródło jest włączone w tej instalacji (np. podany adres ICS)."""
    return True

  def dotyczy(self, ctx: Kontekst) -> bool:
    """Czy źródło ma sens dla tego użytkownika (np. zajęcia - dla studenta)."""
    return True

  def zdarzenia(self, ctx: Kontekst, od: date, do: date) -> list[Zdarzenie]:
    raise NotImplementedError


_REJESTR: dict[str, Zrodlo] = {}


def zarejestruj(zrodlo: Zrodlo) -> Zrodlo:
  _REJESTR[zrodlo.id] = zrodlo
  return zrodlo


def zrodla() -> list[Zrodlo]:
  return [z for z in _REJESTR.values() if z.skonfigurowane()]


# --- daty ------------------------------------------------------------------

def czas_lokalny(value: datetime, strefa: ZoneInfo) -> str:
  """ISO 8601 z przesunięciem; czas bez strefy traktujemy jako lokalny."""
  if value.tzinfo is None:
    value = value.replace(tzinfo=strefa)
  return value.astimezone(strefa).isoformat()


def godzina(text: str) -> time:
  """'9:00:00' / '09:00' / '09:00:00.000000' -> time."""
  parts = (str(text).split('.')[0].split(':') + ['0', '0'])[:3]
  return time(int(parts[0]), int(parts[1]), int(parts[2]))


def dzien_wylacznie(day: date | str) -> str:
  """Koniec zdarzenia całodniowego: dzień PO ostatnim dniu (konwencja ICS
  i FullCalendar)."""
  if isinstance(day, str):
    day = date.fromisoformat(day[:10])
  return (day + timedelta(days=1)).isoformat()


def poczatek_dnia(day: date, strefa: ZoneInfo) -> datetime:
  return datetime.combine(day, time.min, tzinfo=strefa)
