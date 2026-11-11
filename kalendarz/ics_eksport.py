# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

# Plik ICS (RFC 5545) ze zdarzeń kalendarza - kanał
# subskrypcji w Google Calendar / Outlook.
#
# Szczegółowość:
#   minimalna - tytuł, termin, miejsce zajęć i egzaminów; bez kwot, nazw
#               spraw, opisów i odnośników (adres subskrypcji może wyciec:
#               trafia do ustawień cudzego konta Google/Microsoft),
#   pelna     - także opis (grupa, prowadzący, kwota, sprawa) i odnośnik do portalu.
# Czasy w UTC (bez VTIMEZONE), zdarzenia całodniowe jako daty.

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime

import icalendar

from kalendarz.schemas import Zdarzenie

SZCZEGOLOWOSCI = ('minimalna', 'pelna')
PRODID = '-//Galwork//Kalendarz//PL'
DOMENA_UID = 'kalendarz.galwork'
ODSWIEZANIE = 'PT4H'

# tytuły w wersji minimalnej (bez kwot i nazw spraw)
TYTUL_MINIMALNY = {
  'fee_due': 'Termin płatności',
  'termin_zadania': 'Termin zadania',
  'leave': 'Nieobecność',
}
# rodzaje, przy których miejsce nie zdradza nic ponad plan uczelni
MIEJSCE_MINIMALNE = {'zajecia', 'exam', 'wydarzenie'}


def _uid(zdarzenie: Zdarzenie) -> str:
  # stały UID - klient kalendarza aktualizuje zdarzenie zamiast je dublować;
  # skrót, żeby nie ujawniać identyfikatorów dokumentów Frappe
  return f'{hashlib.sha256(zdarzenie.id.encode()).hexdigest()[:32]}@{DOMENA_UID}'


def _czas(text: str, all_day: bool) -> date | datetime:
  if all_day:
    return date.fromisoformat(text[:10])
  value = datetime.fromisoformat(text)
  if value.tzinfo is None:
    return value
  return value.astimezone(UTC)


def _kwota(value, waluta) -> str | None:
  try:
    tekst = f'{float(value):,.2f}'.replace(',', ' ').replace('.', ',')
  except (TypeError, ValueError):
    return None
  return f'{tekst} {waluta or ""}'.strip()


def _opis(zdarzenie: Zdarzenie, portal_url: str) -> str:
  props = zdarzenie.extendedProps
  s = props.szczegoly or {}
  przedmiot = (s.get('przedmiot') or s.get('course') or {}).get('name')
  linie = []
  pola = {
    'zajecia': [('Przedmiot', przedmiot), ('Grupa', s.get('grupa')), ('Prowadzący', s.get('wykladowca'))],
    'exam': [('Przedmiot', przedmiot), ('Grupa', s.get('group')), ('Egzaminator', s.get('examiner'))],
    'fee_due': [('Do zapłaty', _kwota(s.get('outstanding'), s.get('currency')))],
    'termin_zadania': [('Sprawa', s.get('proces'))],
  }.get(props.typ, [])
  linie += [f'{etykieta}: {wartosc}' for etykieta, wartosc in pola if wartosc]
  if props.opis and props.typ != 'termin_zadania':
    linie.append(props.opis)
  if portal_url and props.ref:
    linie.append(portal_url.rstrip('/') + props.ref)
  return '\n'.join(linie)


def zbuduj_ics(zdarzenia: list[Zdarzenie], *, nazwa: str, strefa: str,
               szczegolowosc: str = 'minimalna', portal_url: str = '') -> bytes:
  pelna = szczegolowosc == 'pelna'
  cal = icalendar.Calendar()
  cal.add('prodid', PRODID)
  cal.add('version', '2.0')
  cal.add('calscale', 'GREGORIAN')
  cal.add('method', 'PUBLISH')
  cal.add('x-wr-calname', nazwa)
  cal.add('x-wr-timezone', strefa)
  # sugestia częstotliwości odświeżania (Outlook, Apple; Google ma własny rytm)
  cal.add('refresh-interval', icalendar.vDuration.from_ical(ODSWIEZANIE), parameters={'VALUE': 'DURATION'})
  cal.add('x-published-ttl', ODSWIEZANIE)

  teraz = datetime.now(UTC)
  for zdarzenie in zdarzenia:
    props = zdarzenie.extendedProps
    event = icalendar.Event()
    event.add('uid', _uid(zdarzenie))
    event.add('dtstamp', teraz)
    tytul = zdarzenie.title if pelna else TYTUL_MINIMALNY.get(props.typ, zdarzenie.title)
    event.add('summary', tytul)
    event.add('dtstart', _czas(zdarzenie.start, zdarzenie.allDay))
    if zdarzenie.end:
      event.add('dtend', _czas(zdarzenie.end, zdarzenie.allDay))
    if props.miejsce and (pelna or props.typ in MIEJSCE_MINIMALNE):
      event.add('location', props.miejsce)
    if pelna:
      opis = _opis(zdarzenie, portal_url)
      if opis:
        event.add('description', opis)
      if portal_url and props.ref:
        event.add('url', portal_url.rstrip('/') + props.ref)
    event.add('categories', [props.zrodlo])
    event.add('transp', 'TRANSPARENT' if zdarzenie.allDay else 'OPAQUE')
    cal.add_component(event)
  return cal.to_ical()
