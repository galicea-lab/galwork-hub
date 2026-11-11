# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

# 2026-11-10: kalendarz ogólny uczelni z adresu ICS (Google Calendar / Microsoft 365, 
# hub tylko czyta).
# Adresy: [kalendarz] uczelnia_ics. Plik w pamięci procesu przez cache_ttl;
# zdarzenia cykliczne rozwijane przez recurring-ical-events.

from __future__ import annotations

import hashlib
import logging
import threading
import time
from datetime import date, datetime, timedelta

import icalendar
import recurring_ical_events
import requests

from kalendarz.config import get_settings
from kalendarz.schemas import Zdarzenie, ZdarzenieProps
from kalendarz.zrodla import Kontekst, Zrodlo, czas_lokalny

log = logging.getLogger(__name__)

MAX_ROZMIAR = 5 * 1024 * 1024  # bajtów jednego pliku ICS
MAX_OPIS = 500
TIMEOUT = 10


class IcsError(Exception):
  pass


_cache: dict[str, tuple[float, str]] = {}
_lock = threading.Lock()


def pobierz(url: str, ttl: int) -> str:
  """Treść ICS spod adresu, z pamięci procesu przez `ttl` sekund."""
  now = time.monotonic()
  with _lock:
    cached = _cache.get(url)
    if cached and cached[0] > now:
      return cached[1]
  try:
    response = requests.get(url, timeout=TIMEOUT, stream=True,
                            headers={'Accept': 'text/calendar'})
    response.raise_for_status()
    body = response.raw.read(MAX_ROZMIAR + 1, decode_content=True)
  except requests.RequestException as exc:
    # bez adresu w komunikacie - tajne adresy kalendarzy zawierają klucz
    raise IcsError(f'Nie udało się pobrać kalendarza ({type(exc).__name__})') from exc
  if len(body) > MAX_ROZMIAR:
    raise IcsError('Plik kalendarza jest zbyt duży')
  text = body.decode(response.encoding or 'utf-8', errors='replace')
  with _lock:
    _cache[url] = (now + ttl, text)
  return text


def wyczysc_cache() -> None:
  with _lock:
    _cache.clear()


def _tekst(component, key: str) -> str | None:
  value = component.get(key)
  return str(value).strip() if value else None


def zdarzenia_ics(text: str, od: date, do: date, ctx: Kontekst, *, zrodlo: str,
                  prefiks: str, typ: str = 'wydarzenie') -> list[Zdarzenie]:
  """VEVENT-y z treści ICS w zakresie [od, do] (włącznie)."""
  try:
    calendar = icalendar.Calendar.from_ical(text)
    components = recurring_ical_events.of(calendar).between(od, do + timedelta(days=1))
  except Exception as exc:
    raise IcsError(f'Niepoprawny plik kalendarza ({type(exc).__name__})') from exc

  wynik = []
  for component in components:
    if str(component.get('STATUS', '')).upper() == 'CANCELLED':
      continue
    start = component.get('DTSTART')
    if start is None:
      continue
    start = start.dt
    end = component.get('DTEND')
    end = end.dt if end is not None else None
    if end is None and component.get('DURATION') is not None:
      end = start + component.get('DURATION').dt

    caly_dzien = isinstance(start, date) and not isinstance(start, datetime)
    if caly_dzien:
      start_txt = start.isoformat()
      # DTEND całodniowego zdarzenia w ICS jest już wyłączny
      end_txt = (end if isinstance(end, date) else start + timedelta(days=1)).isoformat()
    else:
      start_txt = czas_lokalny(start, ctx.strefa)
      end_txt = czas_lokalny(end, ctx.strefa) if isinstance(end, datetime) else None

    uid = _tekst(component, 'UID') or ''
    klucz = hashlib.sha1(f'{uid}|{start_txt}'.encode()).hexdigest()[:16]
    opis = _tekst(component, 'DESCRIPTION')
    wynik.append(Zdarzenie(
      id=f'{prefiks}:{klucz}',
      title=_tekst(component, 'SUMMARY') or '(bez tytułu)',
      start=start_txt,
      end=end_txt,
      allDay=caly_dzien,
      extendedProps=ZdarzenieProps(
        zrodlo=zrodlo,
        typ=typ,
        miejsce=_tekst(component, 'LOCATION'),
        opis=opis[:MAX_OPIS] if opis else None,
        szczegoly={'uid': uid} if uid else {},
      ),
    ))
  return wynik


class UczelniaZrodlo(Zrodlo):
  id = 'uczelnia'
  opis = 'Wydarzenia ogólne uczelni (kalendarz współdzielony, adres ICS)'

  @property
  def nazwa(self) -> str:  # type: ignore[override]
    return get_settings().uczelnia_nazwa

  def skonfigurowane(self) -> bool:
    return bool(get_settings().uczelnia_ics)

  def zdarzenia(self, ctx: Kontekst, od: date, do: date) -> list[Zdarzenie]:
    settings = get_settings()
    wynik: list[Zdarzenie] = []
    for index, url in enumerate(settings.uczelnia_ics):
      text = pobierz(url, settings.cache_ttl)
      wynik += zdarzenia_ics(text, od, do, ctx, zrodlo=self.id, prefiks=f'uczelnia:{index}')
    return wynik
