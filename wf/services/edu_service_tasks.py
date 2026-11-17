# wf/services/edu_service_tasks.py
# 2026-11-15: kroki systemowe procesów dziekanatowych (`serviceTask`
# z właściwością `handler`, zob. wf/services/service_tasks.py).
"""Kroki systemowe z danymi z Frappe Education.

`edu.studia-studenta` - po formularzu startowym prośby studenta
(galwork-models/dziekanat/prosba_studenta): z wybranych studiów studenta
(Program Enrollment, słownik `studia-studenta`) wypełnia sekcje
"DANE STUDENTA" i "TOK STUDIÓW", a z atrybutów rodzaju pisma (słownik
`rodzaj-pisma-studenta`, `DictionaryEntry.extra`) - adresata i potrzebne
opinie, na których opierają się bramki procesu. `wnioskodawca` = osoba,
która złożyła prośbę (przypisanie kroku "Wypełnienie wniosku").

Właściwości kroku w BPMN (opcjonalne):
  * `pole` - pole ze studiami studenta (domyślnie "Studia studenta"),
  * `poleRodzaju` - pole z kodem rodzaju (domyślnie "Rodzaj pisma"),
  * `slownikRodzajow` - słownik rodzajów (domyślnie "rodzaj-pisma-studenta").
"""
from __future__ import annotations

from typing import Any, Mapping

from sqlalchemy import select

from connector.frappe_education.connector import get_connector
from connector.frappe_education.studia import MojeStudia, ZapisNaKierunek, db_get_moje_studia
from wf.models import DictionaryEntry
from wf.services import service_tasks
from wf.services.service_tasks import ServiceContext, ServiceResult, ServiceTaskError

STUDIES_FIELD = 'Studia studenta'
KIND_FIELD = 'Rodzaj pisma'
KIND_DICTIONARY = 'rodzaj-pisma-studenta'

# Atrybuty rodzaju w `DictionaryEntry.extra` -> zmienne procesu (warunki bramek).
# Brak atrybutu = ostrożnie: opinia Dyrektora i Dziekana potrzebna (każdy
# z nich może przekazać sprawę dalej), Promotora - nie.
OPINIONS = {
  'opinia_promotora': False,
  'opinia_dyrektora': True,
  'opinia_dziekana': True,
}
ADDRESSEES = ('dyrektor', 'dziekan', 'rektor')


def kind_attributes(db, dictionary: str, code: str) -> dict[str, Any]:
  """Adresat i opinie dla rodzaju `code` z `DictionaryEntry.extra`."""
  entry = db.execute(select(DictionaryEntry).where(
    DictionaryEntry.dictionary_key == dictionary, DictionaryEntry.code == code)).scalars().first()
  if entry is None:
    raise ServiceTaskError(f"Rodzaj pisma '{code}' nie występuje w słowniku '{dictionary}'")
  extra = entry.extra or {}
  result: dict[str, Any] = {name: bool(extra.get(name, default)) for name, default in OPINIONS.items()}
  # bez adresata (np. podanie uniwersalne) pole zostaje puste - wskazuje go
  # sekcja ADRESAT; null nie przeszedłby walidacji pola tekstowego
  if extra.get('adresat') in ADDRESSEES:
    result['adresat'] = extra['adresat']
  return result


def _number(value: Any) -> int | None:
  try:
    return int(value)
  except (TypeError, ValueError):
    return None


def _compact(data: Mapping[str, Any]) -> dict[str, Any]:
  return {k: v for k, v in data.items() if v not in (None, '')}


def study_sections(studia: MojeStudia, enrollment_id: str) -> dict[str, dict[str, Any]]:
  """Sekcje formularza z profilu studenta i zapisu `enrollment_id`.

  Nazwy pól = nazwy w galwork-models/dziekanat/prosba_studenta/src/sekcje
  (dane-studenta/wspolny.json, tok-studiow/wspolny.json)."""
  zapis: ZapisNaKierunek | None = next((z for z in studia.zapisy if z.id == enrollment_id), None)
  if zapis is None:
    raise ServiceTaskError(f"Student nie ma zapisu na studia '{enrollment_id}'")
  student = studia.student
  # semestr: rozliczenie z tego samego roku i semestru akademickiego, a bez
  # niego bieżące (aktualny_semestr) - o ile dotyczy tego roku
  term = next((s for s in studia.semestry
               if s.semestr and s.semestr == zapis.semestr), None)
  if term is None and studia.aktualny_semestr \
          and studia.aktualny_semestr.rok_akademicki == zapis.rok_akademicki:
    term = studia.aktualny_semestr
  semester = term.nr_semestru if term else None
  full_name = student.nazwisko_pelne or ' '.join(
    p for p in (student.imie, student.drugie_imie, student.nazwisko) if p)
  dane = _compact({
    'Imię i nazwisko': full_name,
    'Nr albumu': student.nr_albumu,
    'Email': student.email,
    'Numer telefonu': student.telefon,
    'Adres zamieszkania': ', '.join(p for p in (student.adres, student.miasto) if p) or None,
    'Data urodzenia': student.data_urodzenia,
    'Obywatelstwo': student.obywatelstwo,
  })
  tok = _compact({
    'Kierunek': zapis.kierunek_nazwa or zapis.kierunek,
    'Poziom studiów': zapis.stopien,
    'Tryb studiów': zapis.tryb,
    'Rok akademicki': zapis.rok_akademicki,
    'Semestr akademicki': zapis.semestr,
    'Semestr': _number(semester),
    'Rok studiów': (_number(semester) + 1) // 2 if _number(semester) else None,
    'Data rozpoczęcia kształcenia': student.data_przyjecia,
    'Zasady studiowania': 'odplatne' if zapis.platne else 'nieodplatne',
  })
  return {'DANE STUDENTA': dane, 'TOK STUDIÓW': tok}


def _album_number(enrollment_id: str) -> str:
  connector = get_connector()
  enrollment = connector.get_doc('Program Enrollment', enrollment_id)
  student_id = enrollment.get('student')
  if not student_id:
    raise ServiceTaskError(f"Zapis '{enrollment_id}' nie wskazuje studenta")
  album = connector.get_doc('Student', student_id).get('album_number')
  if not album:
    raise ServiceTaskError(f"Student '{student_id}' nie ma numeru albumu")
  return album


@service_tasks.handler('edu.studia-studenta')
def fill_student_data(ctx: ServiceContext) -> ServiceResult:
  """Wejście: studia studenta (id Program Enrollment) i kod rodzaju pisma.
  Wyjście: sekcje "DANE STUDENTA", "TOK STUDIÓW" (nadpisują to, co było -
  krok jest zaraz po starcie), `adresat`, `opinia_*`, `wnioskodawca`."""
  field = ctx.params.get('pole') or STUDIES_FIELD
  enrollment_id = (ctx.data.get(field) or '').strip()
  if not enrollment_id:
    raise ServiceTaskError(f"Nie wybrano studiów studenta (pole '{field}')")
  kind_field = ctx.params.get('poleRodzaju') or KIND_FIELD
  code = (ctx.data.get(kind_field) or '').strip()
  if not code:
    raise ServiceTaskError(f"Nie wybrano rodzaju pisma (pole '{kind_field}')")
  attributes = kind_attributes(ctx.db, ctx.params.get('slownikRodzajow') or KIND_DICTIONARY, code)
  sections = study_sections(db_get_moje_studia(_album_number(enrollment_id)), enrollment_id)
  return service_tasks.done({**sections, **attributes, 'wnioskodawca': ctx.actor.username})
