# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

# 2026-11-10: GET /v1/kalendarz - kalendarz zalogowanego użytkownika
# (albo wskazanej osoby, dla dziekanatu) ze wszystkich źródeł.
#
# Rola decyduje, czyje dane akademickie są w kalendarzu:
#   rola=student    - własne (dziekanat/administrator: nr_albumu),
#   rola=wykladowca - własne (dziekanat: email),
#   inna rola       - bez źródeł akademickich (zadania, kalendarz uczelni),
#   brak roli       - ustalana z Education po e-mailu z tokenu (jak /v1/edu/ja):
#                     student, a gdy nim nie jest - wykładowca.

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.orm import Session

from api.dependencies import get_db
from connector.frappe_education.connector import error_message
from connector.frappe_education.tozsamosc import db_get_tozsamosc
from edu.dependencies import allowed_album, lecturer_email, user_email
from kalendarz.config import get_settings
from kalendarz import subskrypcja as subskrypcje
from kalendarz.schemas import (
  KalendarzResponse,
  SubskrypcjaNowa,
  SubskrypcjaRequest,
  SubskrypcjaStatus,
  ZrodloInfo,
)
from kalendarz.service import zbierz
from kalendarz.zrodla import Kontekst, zarejestruj, zrodla
from kalendarz.zrodla.edu import AkademickieZrodlo, ZajeciaZrodlo
from kalendarz.zrodla.ics import UczelniaZrodlo
from kalendarz.zrodla.zadania import ZadaniaZrodlo
from wf.dependencies import get_current_wf_user
from wf.schemas.wf_schemas import CurrentUser

log = logging.getLogger(__name__)

# kolejność = kolejność na liście źródeł w odpowiedzi
for _zrodlo in (ZajeciaZrodlo(), AkademickieZrodlo(), ZadaniaZrodlo(), UczelniaZrodlo()):
  zarejestruj(_zrodlo)

router = APIRouter(tags=['kalendarz'], responses={404: {'description': 'Not found'}})

Rola = Literal['student', 'wykladowca', 'dziekanat', 'administrator', 'kandydat']


def _data(value: Optional[str], nazwa: str) -> Optional[date]:
  if not value:
    return None
  try:
    return date.fromisoformat(value[:10])
  except ValueError:
    raise HTTPException(status_code=400, detail=f'Niepoprawna data {nazwa}: {value} (RRRR-MM-DD)')


def zakres(od: Optional[str], do: Optional[str]) -> tuple[date, date]:
  settings = get_settings()
  start = _data(od, 'od') or date.today()
  end = _data(do, 'do') or start + timedelta(days=settings.domyslne_dni)
  if end < start:
    raise HTTPException(status_code=400, detail='Data "do" jest wcześniejsza niż "od"')
  if (end - start).days > settings.max_dni:
    raise HTTPException(status_code=400, detail=f'Zakres kalendarza może mieć najwyżej {settings.max_dni} dni')
  return start, end


def _wybrane(wartosc: Optional[str]) -> list:
  dostepne = zrodla()
  if not wartosc:
    return dostepne
  ids = {item.strip() for item in wartosc.split(',') if item.strip()}
  nieznane = ids - {z.id for z in dostepne}
  if nieznane:
    raise HTTPException(status_code=400, detail=f'Nieznane źródła: {", ".join(sorted(nieznane))}')
  return [z for z in dostepne if z.id in ids]


def osoba_edu(current_user: CurrentUser, rola: Optional[str], nr_albumu: Optional[str],
              email: Optional[str]) -> tuple[Optional[str], Optional[str], str]:
  """(nr_albumu, email_wykladowcy, ostrzeżenie) - po kontroli dostępu."""
  if rola == 'student' or (rola is None and nr_albumu):
    return allowed_album(current_user, nr_albumu), None, ''
  if rola == 'wykladowca' or (rola is None and email):
    return None, lecturer_email(current_user, email), ''
  if rola is not None:
    return None, None, ''

  own = user_email(current_user)
  if not own:
    return None, None, 'W tokenie brak adresu e-mail - bez danych z Education'
  try:
    tozsamosc = db_get_tozsamosc(own)
  except Exception as exc:
    log.info('kalendarz: tożsamość: %s', type(exc).__name__)
    return None, None, f'Nie udało się ustalić roli w Education: {error_message(exc)}'
  if tozsamosc.student:
    return tozsamosc.student.nr_albumu or tozsamosc.student.student, None, ''
  if tozsamosc.wykladowca:
    return None, own, ''
  return None, None, ''


@router.get('/', response_model=KalendarzResponse)
def get_kalendarz(
  od: Optional[str] = Query(None, description='Pierwszy dzień (RRRR-MM-DD), domyślnie dziś'),
  do: Optional[str] = Query(None, description='Ostatni dzień (włącznie), domyślnie od + [kalendarz] domyslne_dni'),
  zrodla: Optional[str] = Query(None, description='Id źródeł po przecinku (GET /zrodla), domyślnie wszystkie'),
  rola: Optional[Rola] = Query(None, description='Aktywna rola w interfejsie'),
  nr_albumu: Optional[str] = Query(None, description='Student (tylko dziekanat/administrator)'),
  email: Optional[str] = Query(None, description='Wykładowca (tylko dziekanat)'),
  db: Session = Depends(get_db),
  current_user: CurrentUser = Depends(get_current_wf_user),
) -> KalendarzResponse:
  """
  Zdarzenia kalendarza z wybranych źródeł w zakresie dat.

  Zdarzenia mają format FullCalendar (`allDay` z wyłącznym `end`, czasy ISO
  8601 z przesunięciem strefy). `zrodla` w odpowiedzi opisuje każde źródło:
  czy dotyczy użytkownika, czy odpowiedziało i ile dało zdarzeń - awaria
  jednego źródła nie przerywa całego kalendarza.
  """
  settings = get_settings()
  start, end = zakres(od, do)
  wybrane = _wybrane(zrodla)
  album, wykladowca, ostrzezenie = osoba_edu(current_user, rola, nr_albumu, email)

  ctx = Kontekst(current_user=current_user, strefa=settings.tz, db=db,
                 nr_albumu=album, email_wykladowcy=wykladowca)
  events, statusy = zbierz(ctx, wybrane, start, end, settings.timeout)
  return KalendarzResponse(
    code=0,
    message='OK',
    od=start.isoformat(),
    do=end.isoformat(),
    strefa=settings.timezone,
    ostrzezenie=ostrzezenie,
    zrodla=statusy,
    events=events,
  )


@router.get('/zrodla', response_model=List[ZrodloInfo])
def get_zrodla(current_user: CurrentUser = Depends(get_current_wf_user)) -> List[ZrodloInfo]:
  """Źródła kalendarza włączone w tej instalacji."""
  return [ZrodloInfo(id=z.id, nazwa=z.nazwa, opis=z.opis) for z in zrodla()]


# --- subskrypcja (kanał ICS) -----------------------------------------------
# adres z osobistym tokenem do dodania w Google Calendar / Outlook 
# ("Dodaj z adresu URL"). Opis ograniczeń: kalendarz/subskrypcja.py.

def _status(rekord) -> SubskrypcjaStatus:
  if rekord is None:
    return SubskrypcjaStatus(aktywna=False)
  return SubskrypcjaStatus(aktywna=True, szczegolowosc=rekord.szczegolowosc, rola=rekord.rola,
                           utworzono=rekord.utworzono, ostatnie_pobranie=rekord.ostatnie_pobranie,
                           liczba_pobran=rekord.liczba_pobran or 0)


def adres_kanalu(request: Request, token: str) -> str:
  public_url = get_settings().public_url
  if public_url:
    return f"{public_url.rstrip('/')}/ics/{token}.ics"
  return str(request.url_for('kalendarz_ics', token=token))


@router.get('/subskrypcja', response_model=SubskrypcjaStatus)
def get_subskrypcja(db: Session = Depends(get_db),
                    current_user: CurrentUser = Depends(get_current_wf_user)) -> SubskrypcjaStatus:
  """Czy użytkownik ma aktywną subskrypcję (bez adresu - token nie jest przechowywany)."""
  return _status(subskrypcje.pobierz(db, current_user.id))


@router.post('/subskrypcja', response_model=SubskrypcjaNowa)
def post_subskrypcja(body: SubskrypcjaRequest, request: Request,
                     db: Session = Depends(get_db),
                     current_user: CurrentUser = Depends(get_current_wf_user)) -> SubskrypcjaNowa:
  """Nowy adres subskrypcji. Poprzedni adres przestaje działać."""
  token, rekord = subskrypcje.utworz(db, current_user.id, body.szczegolowosc, body.rola)
  url = adres_kanalu(request, token)
  webcal = 'webcal://' + url.split('://', 1)[1] if '://' in url else url
  return SubskrypcjaNowa(**_status(rekord).model_dump(), url=url, webcal_url=webcal)


@router.delete('/subskrypcja', response_model=SubskrypcjaStatus)
def delete_subskrypcja(db: Session = Depends(get_db),
                       current_user: CurrentUser = Depends(get_current_wf_user)) -> SubskrypcjaStatus:
  """Odwołanie subskrypcji - adres przestaje działać."""
  subskrypcje.usun(db, current_user.id)
  return SubskrypcjaStatus(aktywna=False)


@router.get('/ics/{token}.ics', name='kalendarz_ics', include_in_schema=False)
def get_ics(token: str, db: Session = Depends(get_db)) -> Response:
  """Kanał ICS - bez logowania, tożsamość z tokenu w adresie."""
  if len(token) > 128:
    raise HTTPException(status_code=404, detail='Nie znaleziono kalendarza')
  tresc = subskrypcje.kanal(db, token, get_settings())
  return Response(content=tresc, media_type='text/calendar; charset=utf-8', headers={
    'Cache-Control': 'private, max-age=600',
    'Content-Disposition': 'inline; filename="kalendarz.ics"',
    'X-Robots-Tag': 'noindex',
  })

