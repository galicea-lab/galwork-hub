# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

# portal kandydata - /v1/rekrutacja. Dane osobowe idą do
# Frappe (admissions_poland.portal.api) bez analizy w hubie i bez logowania
# treści; Frappe wywoływane w imieniu kandydata (rekrutacja.auth).

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from connector.base import ConnectorError
from connector.frappe_education.connector import FrappeRequestError, error_message, get_connector
from rekrutacja.auth import Candidate, current_candidate

router = APIRouter(tags=['rekrutacja'])
logger = logging.getLogger('rekrutacja')

API = 'admissions_poland.portal.api'


def _frappe(method: str, params: dict | None = None, http_method: str = 'GET') -> Any:
  try:
    return get_connector().call_method(f'{API}.{method}', params, http_method)
  except FrappeRequestError as exc:
    logger.warning('rekrutacja: %s', exc)
    if exc.status_code in (401, 403):
      raise HTTPException(status_code=403, detail=error_message(exc) or 'Brak dostępu')
    if exc.status_code == 404:
      raise HTTPException(status_code=404, detail=error_message(exc) or 'Nie znaleziono')
    if exc.status_code in (400, 409, 417, 422):
      raise HTTPException(status_code=400, detail=error_message(exc))
    raise HTTPException(status_code=502, detail='System rekrutacji jest chwilowo niedostępny')
  except ConnectorError as exc:
    logger.error('rekrutacja: %s', exc)
    raise HTTPException(status_code=502, detail='System rekrutacji jest chwilowo niedostępny')
  except Exception as exc:  # requests.RequestException - bez treści w logu
    logger.error('rekrutacja: %s (%s)', method, type(exc).__name__)
    raise HTTPException(status_code=502, detail='System rekrutacji jest chwilowo niedostępny')


@router.get('/profil')
def profil(candidate: Candidate = Depends(current_candidate)) -> dict:
  """Dane osobowe kandydata (konto kandydata we Frappe powstaje przy pierwszym wywołaniu)."""
  return _frappe('get_profile')


@router.put('/profil')
def zapisz_profil(data: dict = Body(...), candidate: Candidate = Depends(current_candidate)) -> dict:
  """Zapis danych osobowych - walidacja (PESEL, dokument) we Frappe."""
  return _frappe('update_profile', {'data': data}, 'POST')


@router.get('/slowniki')
def slowniki(candidate: Candidate = Depends(current_candidate)) -> dict:
  """Płcie, kraje i rodzaje dokumentów do formularza."""
  return _frappe('get_profile_options')


@router.get('/kierunki')
def kierunki() -> list:
  """Publiczny katalog kierunków opublikowanych kampanii."""
  return _frappe('list_programmes')


@router.get('/harmonogram')
def harmonogram() -> list:
  """Publiczny harmonogram opublikowanych kampanii: okres zgłoszeń i tury kierunków."""
  return _frappe('get_schedule')


@router.get('/zgloszenia')
def zgloszenia(candidate: Candidate = Depends(current_candidate)) -> list:
  """Zgłoszenia kandydata ze statusami (opłata, kwalifikacja, przyjęcie) i listą dokumentów."""
  return _frappe('list_applications')


@router.post('/zgloszenia')
def zglos(kierunek: str = Body(..., embed=True), priorytet: int | None = Body(None, embed=True),
          candidate: Candidate = Depends(current_candidate)) -> dict:
  """Wybór kierunku - zgłoszenie w otwartej turze (priorytet domyślnie kolejny wolny)."""
  params: dict = {'admission_programme': kierunek}
  if priorytet:
    params['priority'] = priorytet
  return _frappe('apply_for_programme', params, 'POST')


@router.post('/zgloszenia/{zgloszenie}/wycofanie')
def wycofaj(zgloszenie: str, candidate: Candidate = Depends(current_candidate)) -> dict:
  """Wycofanie zgłoszenia przed kwalifikacją."""
  return _frappe('withdraw_application', {'application': zgloszenie}, 'POST')


@router.get('/decyzje')
def decyzje(candidate: Candidate = Depends(current_candidate)) -> list:
  """Opublikowane i doręczone decyzje kandydata (bez treści)."""
  return _frappe('list_decisions')
