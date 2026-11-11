# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

# 2026-11-10: subskrypcja kalendarza - token osobisty i kanał ICS.
#
# Klient kalendarza (Google, Outlook) nie wysyła tokenu Keycloak, więc kanał
# jest dostępny pod adresem z tajnym tokenem. Kanał budowany jest w imieniu
# właściciela tokenu, ale BEZ grup i ról z Keycloak (nie ma ich poza
# tokenem): terminy zadań obejmują tylko zadania podjęte przez użytkownika,
# a dziekanat nie dostaje danych innych osób.

from __future__ import annotations

import hashlib
import logging
import secrets
import threading
import time
from datetime import date, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from kalendarz.config import KalendarzSettings
from kalendarz.ics_eksport import zbuduj_ics
from kalendarz.models import KalendarzSubskrypcja
from kalendarz.service import zbierz
from kalendarz.zrodla import Kontekst, zrodla
from models.user import User
from wf.schemas.wf_schemas import CurrentUser

log = logging.getLogger(__name__)

NAZWA_KANALU = 'Galwork - mój kalendarz'


def skrot(token: str) -> str:
  return hashlib.sha256(token.encode()).hexdigest()


def nowy_token() -> str:
  return secrets.token_urlsafe(32)


# --- pamięć gotowych kanałów -----------------------------------------------

_cache: dict[str, tuple[float, bytes]] = {}
_lock = threading.Lock()


def _z_cache(token_hash: str) -> bytes | None:
  with _lock:
    wpis = _cache.get(token_hash)
    if wpis and wpis[0] > time.monotonic():
      return wpis[1]
    _cache.pop(token_hash, None)
    return None


def _do_cache(token_hash: str, tresc: bytes, ttl: int) -> None:
  with _lock:
    _cache[token_hash] = (time.monotonic() + ttl, tresc)


def zapomnij(token_hash: str | None = None) -> None:
  with _lock:
    if token_hash is None:
      _cache.clear()
    else:
      _cache.pop(token_hash, None)


# --- zarządzanie ------------------------------------------------------------

def pobierz(db: Session, user_id: int) -> KalendarzSubskrypcja | None:
  return db.execute(select(KalendarzSubskrypcja).where(
    KalendarzSubskrypcja.user_id == user_id)).scalars().first()


def utworz(db: Session, user_id: int, szczegolowosc: str, rola: str | None) -> tuple[str, KalendarzSubskrypcja]:
  """Nowy token (stary przestaje działać). Zwraca token jawny - jedyny raz."""
  token = nowy_token()
  subskrypcja = pobierz(db, user_id)
  if subskrypcja is None:
    subskrypcja = KalendarzSubskrypcja(user_id=user_id, liczba_pobran=0)
    db.add(subskrypcja)
  else:
    zapomnij(subskrypcja.token_hash)
  subskrypcja.token_hash = skrot(token)
  subskrypcja.szczegolowosc = szczegolowosc
  subskrypcja.rola = rola
  subskrypcja.utworzono = datetime.now()
  subskrypcja.ostatnie_pobranie = None
  subskrypcja.liczba_pobran = 0
  db.commit()
  db.refresh(subskrypcja)
  return token, subskrypcja


def usun(db: Session, user_id: int) -> bool:
  subskrypcja = pobierz(db, user_id)
  if subskrypcja is None:
    return False
  zapomnij(subskrypcja.token_hash)
  db.delete(subskrypcja)
  db.commit()
  return True


# --- kanał ------------------------------------------------------------------

def _uzytkownik_kanalu(user: User) -> CurrentUser:
  # bez grup, ról i zastępstw; pusty login nie pasuje do żadnego kandydata
  return CurrentUser(id=user.id, sub=user.keycloak_sub or '', username='',
                     email=user.email, display_name=user.name or user.email)


def kanal(db: Session, token: str, settings: KalendarzSettings) -> bytes:
  """Plik ICS dla tokenu; nieznany lub odwołany token -> 404."""
  token_hash = skrot(token)
  gotowy = _z_cache(token_hash)
  if gotowy is not None:
    return gotowy

  subskrypcja = db.execute(select(KalendarzSubskrypcja).where(
    KalendarzSubskrypcja.token_hash == token_hash)).scalars().first()
  if subskrypcja is None:
    raise HTTPException(status_code=404, detail='Nie znaleziono kalendarza')
  user = db.get(User, subskrypcja.user_id)
  if user is None or not user.is_active:
    raise HTTPException(status_code=404, detail='Nie znaleziono kalendarza')

  # import lokalny - router importuje ten moduł
  from kalendarz.router import osoba_edu

  current_user = _uzytkownik_kanalu(user)
  try:
    album, wykladowca, _ = osoba_edu(current_user, subskrypcja.rola, None, None)
  except HTTPException:
    album, wykladowca = None, None

  dzis = date.today()
  od = dzis - timedelta(days=settings.ics_dni_wstecz)
  do = dzis + timedelta(days=settings.ics_dni_naprzod)
  ctx = Kontekst(current_user=current_user, strefa=settings.tz, db=db,
                 nr_albumu=album, email_wykladowcy=wykladowca)
  zdarzenia, statusy = zbierz(ctx, zrodla(), od, do, settings.timeout)
  bledy = [s.id for s in statusy if s.dotyczy and not s.ok]
  if bledy:
    # niepełny plik usunąłby u klienta zdarzenia z brakującego źródła (np.
    # egzaminy); 503 - klient zostawia poprzednią wersję i ponawia później
    log.warning('kalendarz ics: niedostępne źródła %s', ', '.join(bledy))
    raise HTTPException(status_code=503, detail='Kalendarz chwilowo niedostępny',
                        headers={'Retry-After': '900'})

  tresc = zbuduj_ics(zdarzenia, nazwa=NAZWA_KANALU, strefa=settings.timezone,
                     szczegolowosc=subskrypcja.szczegolowosc, portal_url=settings.portal_url)

  subskrypcja.ostatnie_pobranie = datetime.now()
  subskrypcja.liczba_pobran = (subskrypcja.liczba_pobran or 0) + 1
  db.commit()
  _do_cache(token_hash, tresc, settings.ics_cache_ttl)
  return tresc
