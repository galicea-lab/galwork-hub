# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

# 2026-11-10: źródła z Frappe Education - zajęcia (get_schedule) i
# kalendarz akademicki (get_calendar: semestry, sesje, egzaminy, opłaty,
# urlopy, dni wolne). Student albo wykładowca, nigdy obaj naraz.

from __future__ import annotations

from datetime import date, datetime

from connector.frappe_education.kalendarz import (
  EduZdarzenie,
  db_get_kalendarz_studenta,
  db_get_kalendarz_wykladowcy,
)
from connector.frappe_education.plan import (
  EduScheduleEntry,
  db_get_plan_studenta,
  db_get_plan_wykladowcy,
)
from kalendarz.schemas import Zdarzenie, ZdarzenieProps
from kalendarz.zrodla import Kontekst, Zrodlo, czas_lokalny, dzien_wylacznie, godzina


def _osoba_edu(ctx: Kontekst) -> bool:
  return bool(ctx.nr_albumu or ctx.email_wykladowcy)


class ZajeciaZrodlo(Zrodlo):
  id = 'zajecia'
  nazwa = 'Zajęcia'
  opis = 'Plan zajęć studenta albo wykładowcy (Frappe Education)'

  def dotyczy(self, ctx: Kontekst) -> bool:
    return _osoba_edu(ctx)

  def zdarzenia(self, ctx: Kontekst, od: date, do: date) -> list[Zdarzenie]:
    if ctx.nr_albumu:
      plan = db_get_plan_studenta(ctx.nr_albumu, od.isoformat(), do.isoformat())
    else:
      plan = db_get_plan_wykladowcy(ctx.email_wykladowcy, od.isoformat(), do.isoformat())
    return [self._zdarzenie(ctx, wpis) for wpis in plan.wpisy if wpis.data and wpis.od]

  def _zdarzenie(self, ctx: Kontekst, wpis: EduScheduleEntry) -> Zdarzenie:
    dzien = date.fromisoformat(wpis.data[:10])
    start = datetime.combine(dzien, godzina(wpis.od))
    koniec = datetime.combine(dzien, godzina(wpis.do)) if wpis.do else None
    nazwa = wpis.tytul or wpis.przedmiot.name or wpis.przedmiot.code or 'Zajęcia'
    return Zdarzenie(
      id=f'zajecia:{wpis.id}',
      title=nazwa,
      start=czas_lokalny(start, ctx.strefa),
      end=czas_lokalny(koniec, ctx.strefa) if koniec else None,
      extendedProps=ZdarzenieProps(
        zrodlo=self.id,
        typ='zajecia',
        ref='/plan',
        miejsce=wpis.sala,
        kolor=wpis.kolor,
        szczegoly={
          'przedmiot': wpis.przedmiot.model_dump(),
          'grupa': wpis.grupa,
          'wykladowca': wpis.wykladowca,
          'kierunek': wpis.kierunek,
        },
      ),
    )


# tytuły zdarzeń akademickich; {t} = tytuł z Frappe (nazwa semestru itd.)
TYTULY = {
  'academic_year_start': 'Początek roku akademickiego {t}',
  'academic_year_end': 'Koniec roku akademickiego {t}',
  'term_start': 'Początek semestru {t}',
  'term_end': 'Koniec semestru {t}',
  'exam_session': 'Sesja egzaminacyjna {t}',
  'exam': 'Egzamin: {t}',
  'fee_due': 'Termin płatności',
  'leave': 'Usprawiedliwiona nieobecność',
  'holiday': '{t}',
}

# ekran szczegółów w portalu
REF = {
  'exam': '/oceny',
  'fee_due': '/oplaty',
  'leave': '/frekwencja',
}


def _kwota(value, waluta: str | None) -> str:
  try:
    tekst = f'{float(value):,.2f}'.replace(',', ' ').replace('.', ',')
  except (TypeError, ValueError):
    return ''
  return f'{tekst} {waluta or ""}'.strip()


class AkademickieZrodlo(Zrodlo):
  id = 'akademickie'
  nazwa = 'Kalendarz akademicki'
  opis = 'Rok i semestry, sesja, egzaminy, terminy płatności, dni wolne (Frappe Education)'

  def dotyczy(self, ctx: Kontekst) -> bool:
    return _osoba_edu(ctx)

  def zdarzenia(self, ctx: Kontekst, od: date, do: date) -> list[Zdarzenie]:
    if ctx.nr_albumu:
      kalendarz = db_get_kalendarz_studenta(ctx.nr_albumu, od.isoformat(), do.isoformat())
    else:
      kalendarz = db_get_kalendarz_wykladowcy(ctx.email_wykladowcy, od.isoformat(), do.isoformat())
    return [self._zdarzenie(ctx, z) for z in kalendarz.zdarzenia if z.start]

  def _tytul(self, z: EduZdarzenie) -> str:
    if z.typ == 'fee_due':
      szczegoly = z.szczegoly
      if szczegoly.get('state') == 'paid':
        return 'Termin płatności (opłacone)'
      kwota = _kwota(szczegoly.get('outstanding'), szczegoly.get('currency'))
      return f'Termin płatności: {kwota}' if kwota else TYTULY['fee_due']
    if z.typ == 'exam':
      przedmiot = (z.szczegoly.get('course') or {}).get('name')
      return TYTULY['exam'].format(t=z.tytul or przedmiot or '')
    return TYTULY.get(z.typ, '{t}').format(t=z.tytul).strip()

  def _zdarzenie(self, ctx: Kontekst, z: EduZdarzenie) -> Zdarzenie:
    if z.caly_dzien:
      start, end = z.start[:10], dzien_wylacznie(z.koniec or z.start)
    else:
      start = czas_lokalny(datetime.fromisoformat(z.start), ctx.strefa)
      end = czas_lokalny(datetime.fromisoformat(z.koniec), ctx.strefa) if z.koniec else None
    return Zdarzenie(
      id=f'akademickie:{z.id}',
      title=self._tytul(z),
      start=start,
      end=end,
      allDay=z.caly_dzien,
      extendedProps=ZdarzenieProps(
        zrodlo=self.id,
        typ=z.typ,
        ref=REF.get(z.typ),
        miejsce=z.szczegoly.get('room'),
        szczegoly=z.szczegoly,
      ),
    )
