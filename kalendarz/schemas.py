# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

# Format odpowiedzi kalendarza. Zdarzenie ma kształt zdarzenia
# FullCalendar (i `EventItem` z galwork-edu): `allDay` z WYŁĄCZNYM `end`,
# czasy w ISO 8601 z przesunięciem strefy.

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class ZdarzenieProps(BaseModel):
  zrodlo: str  # id źródła (zajecia, akademickie, zadania, uczelnia)
  typ: str  # rodzaj zdarzenia w źródle (np. exam, fee_due, zajecia, termin_zadania)
  ref: Optional[str] = None  # ścieżka ekranu szczegółów w portalu (np. /oplaty)
  miejsce: Optional[str] = None
  opis: Optional[str] = None
  kolor: Optional[str] = None
  szczegoly: Dict[str, Any] = Field(default_factory=dict)


class Zdarzenie(BaseModel):
  id: str  # stały w ramach źródła - nadaje się na UID w ICS
  title: str
  start: str
  end: Optional[str] = None
  allDay: bool = False
  url: Optional[str] = None
  organizer: Optional[str] = None
  extendedProps: ZdarzenieProps


class StatusZrodla(BaseModel):
  id: str
  nazwa: str
  # False - źródło nie dotyczy tego użytkownika lub roli (np. zajęcia bez
  # rekordu studenta); wtedy `ok` jest True i nie ma zdarzeń
  dotyczy: bool = True
  ok: bool = True
  liczba: int = 0
  komunikat: str = ''


class KalendarzResponse(BaseModel):
  code: int = 0
  message: str = 'OK'
  od: str = ''
  do: str = ''
  strefa: str = ''
  ostrzezenie: str = ''
  zrodla: List[StatusZrodla] = []
  events: List[Zdarzenie] = []


class ZrodloInfo(BaseModel):
  id: str
  nazwa: str
  opis: str = ''


class SubskrypcjaRequest(BaseModel):
  szczegolowosc: Literal['minimalna', 'pelna'] = 'minimalna'
  # rola, dla której zbierać terminy akademickie; brak - ustalana z Education
  rola: Optional[Literal['student', 'wykladowca', 'dziekanat', 'administrator', 'kandydat']] = None


class SubskrypcjaStatus(BaseModel):
  aktywna: bool = False
  szczegolowosc: Optional[str] = None
  rola: Optional[str] = None
  utworzono: Optional[datetime] = None
  ostatnie_pobranie: Optional[datetime] = None
  liczba_pobran: int = 0


class SubskrypcjaNowa(SubskrypcjaStatus):
  """Odpowiedź na utworzenie - adres z tokenem widoczny tylko teraz."""
  url: str
  webcal_url: str

