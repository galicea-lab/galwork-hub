# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

# REST usługi kodów jednorazowych (bez uwierzytelnienia -
# chronią ją CAPTCHA i limity). Ścieżki względem /v1/konto.

from __future__ import annotations

import html
import logging
from functools import lru_cache
from typing import Iterator, Literal
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field

from konto.config import KontoSettings
from konto.policy import PolicyError
from konto.service import KontoError, KontoService, TicketInfo
from konto.wk import WkService

router = APIRouter(tags=['konto'])
logger = logging.getLogger('konto.wk')


class CodeRequest(BaseModel):
  channel: Literal['sms', 'email']
  destination: str = Field(max_length=254)
  captcha: str | None = Field(default=None, max_length=4096)


class RegistrationRequest(BaseModel):
  ticket: str = Field(max_length=64)
  code: str = Field(max_length=16)
  email: str = Field(max_length=254)
  phone: str | None = Field(default=None, max_length=32)
  first_name: str = Field(max_length=100)
  last_name: str = Field(max_length=100)
  password: str = Field(min_length=1, max_length=256)
  pesel: str | None = Field(default=None, max_length=11)


class ResetCodeRequest(BaseModel):
  login: str = Field(max_length=254)
  channel: Literal['sms', 'email'] | None = None
  captcha: str | None = Field(default=None, max_length=4096)


class ResetRequest(BaseModel):
  ticket: str = Field(max_length=64)
  code: str = Field(max_length=16)
  password: str = Field(min_length=1, max_length=256)


class ResendRequest(BaseModel):
  ticket: str = Field(max_length=64)
  destination: str | None = Field(default=None, max_length=254)


class TicketResponse(BaseModel):
  ticket: str
  expires_in: int
  resend_after: int
  message: str


class RegistrationResponse(BaseModel):
  username: str
  login_url: str | None = None


@lru_cache(maxsize=1)
def konto_settings() -> KontoSettings:
  return KontoSettings.load()


def get_konto_service() -> Iterator[KontoService]:
  """Usługa z sesją bazy na czas żądania (podmieniana w testach)."""
  from connector.keycloak_admin.connector import get_connector as keycloak
  from connector.mailing.connector import get_connector as mailing
  from connector.sms.connector import get_connector as sms
  from db.session import SessionLocal
  from konto.captcha import CaptchaVerifier

  from connector.wk.connector import get_connector as wk

  settings = konto_settings()
  db = SessionLocal()
  try:
    service = KontoService(
      settings, db, keycloak(),
      sms=sms() if 'sms' in settings.channels else None,
      mail=mailing() if 'email' in settings.channels else None,
      captcha=CaptchaVerifier(settings.captcha_verify_url, settings.captcha_secret,
                              site_key=settings.captcha_site_key))
    service.wk_enabled = wk().enabled
    yield service
  finally:
    db.close()


def get_wk_service(service: KontoService = Depends(get_konto_service)) -> WkService:
  from connector.wk.connector import get_connector as wk
  return WkService(service, wk())


def _ip(request: Request) -> str | None:
  return request.client.host if request.client else None


def _call(fn, *args, **kwargs):
  try:
    return fn(*args, **kwargs)
  except PolicyError as exc:
    raise HTTPException(status_code=400, detail=str(exc))
  except KontoError as exc:
    raise HTTPException(status_code=exc.status, detail=exc.message)


def _ticket(info: TicketInfo) -> TicketResponse:
  return TicketResponse(ticket=info.ticket, expires_in=info.expires_in,
                        resend_after=info.resend_after, message=info.message)


@router.get('/konfiguracja')
def konfiguracja(service: KontoService = Depends(get_konto_service)) -> dict:
  """Dostępne kanały i parametry formularza (bez sekretów)."""
  return service.public_config()


@router.post('/rejestracja/kod', response_model=TicketResponse)
def rejestracja_kod(body: CodeRequest, request: Request,
                    service: KontoService = Depends(get_konto_service)):
  """Wysyła kod potwierdzający telefon albo e-mail przed założeniem konta."""
  return _ticket(_call(service.start_registration, body.channel, body.destination,
                       _ip(request), body.captcha))


@router.post('/rejestracja', response_model=RegistrationResponse, status_code=201)
def rejestracja(body: RegistrationRequest, service: KontoService = Depends(get_konto_service)):
  """Zakłada konto w Keycloak po poprawnym kodzie. Nie loguje."""
  return _call(service.complete_registration, body.ticket, body.code,
               email=body.email, phone=body.phone, first_name=body.first_name,
               last_name=body.last_name, password=body.password, pesel=body.pesel)


@router.post('/reset/kod', response_model=TicketResponse, status_code=202)
def reset_kod(body: ResetCodeRequest, request: Request,
              service: KontoService = Depends(get_konto_service)):
  """Wysyła kod odzyskania hasła (odpowiedź taka sama, gdy konta nie ma)."""
  return _ticket(_call(service.start_reset, body.login, _ip(request), body.captcha,
                       body.channel))


@router.post('/reset', status_code=204)
def reset(body: ResetRequest, service: KontoService = Depends(get_konto_service)):
  """Ustawia nowe hasło po poprawnym kodzie i wylogowuje wszystkie sesje. Nie loguje."""
  _call(service.complete_reset, body.ticket, body.code, body.password)
  return Response(status_code=204)


@router.post('/kod/ponow', response_model=TicketResponse)
def kod_ponow(body: ResendRequest, service: KontoService = Depends(get_konto_service)):
  """Ponownie wysyła kod (nie zużywa puli operacji)."""
  return _ticket(_call(service.resend, body.ticket, body.destination))


# --- login.gov.pl (Węzeł Krajowy): weryfikacja tożsamości, nie logowanie ---

class WkTicketRequest(BaseModel):
  ticket: str = Field(max_length=64)


class WkRegistrationRequest(BaseModel):
  ticket: str = Field(max_length=64)
  email: str = Field(max_length=254)
  password: str = Field(min_length=1, max_length=256)


class WkResetRequest(BaseModel):
  ticket: str = Field(max_length=64)
  password: str = Field(min_length=1, max_length=256)


_AUTOPOST = """<!DOCTYPE html>
<html lang="pl"><head><meta charset="utf-8"><title>login.gov.pl</title></head>
<body onload="document.forms[0].submit()">
<form method="POST" action="{action}">
<input type="hidden" name="SAMLRequest" value="{saml}"/>
<input type="hidden" name="RelayState" value="{relay}"/>
<noscript><p>JavaScript jest wyłączony. Aby kontynuować, naciśnij przycisk.</p>
<input type="submit" value="Kontynuuj"/></noscript>
</form></body></html>"""


def _portal_redirect(params: dict) -> Response:
  from connector.wk.connector import get_connector as wk
  portal = (wk().config.get('portal_url') or '').strip()
  if not portal:
    message = 'Weryfikacja zakończona.' if 'wk' in params else 'Weryfikacja nieudana.'
    return HTMLResponse(f'<!DOCTYPE html><p>{html.escape(message)}</p>',
                        status_code=200 if 'wk' in params else 400)
  # bilet w części # adresu - nie trafia do serwera portalu ani do nagłówka Referer
  return RedirectResponse(f'{portal}#{urlencode(params)}', status_code=303)


@router.get('/wk/start', response_class=HTMLResponse)
def wk_start(cel: Literal['rejestracja', 'reset'], request: Request,
             service: WkService = Depends(get_wk_service)):
  """Przekierowanie (formularz POST) do login.gov.pl."""
  start = _call(service.start, cel, _ip(request))
  return HTMLResponse(_AUTOPOST.format(action=html.escape(start.sso_url, quote=True),
                                       saml=html.escape(start.saml_request, quote=True),
                                       relay=html.escape(start.relay_state, quote=True)),
                      headers={'Cache-Control': 'no-store'})


@router.api_route('/wk/acs', methods=['GET', 'POST'], include_in_schema=False)
async def wk_acs(request: Request, service: WkService = Depends(get_wk_service)):
  """Powrót z login.gov.pl (HTTP-Artifact)."""
  from fastapi.concurrency import run_in_threadpool

  from connector.wk.saml import WkError

  params = dict(request.query_params)
  if request.method == 'POST':
    params.update({k: v for k, v in (await request.form()).items() if isinstance(v, str)})
  try:
    ticket, cel = await run_in_threadpool(service.acs, params.get('SAMLart'), params.get('RelayState'))
  except WkError as exc:
    logger.warning('wk: odpowiedź odrzucona - %s', exc)
    return _portal_redirect({'wk_blad': exc.code})
  except KontoError as exc:
    return _portal_redirect({'wk_blad': 'usluga_niedostepna' if exc.status >= 500 else 'blad'})
  return _portal_redirect({'wk': ticket, 'cel': cel})


@router.post('/wk/dane')
def wk_dane(body: WkTicketRequest, service: WkService = Depends(get_wk_service)) -> dict:
  """Imię i nazwisko z login.gov.pl oraz to, czy konto już istnieje."""
  return _call(service.info, body.ticket)


@router.post('/wk/rejestracja', response_model=RegistrationResponse, status_code=201)
def wk_rejestracja(body: WkRegistrationRequest, service: WkService = Depends(get_wk_service)):
  """Zakłada konto ze zweryfikowaną tożsamością. Nie loguje."""
  return _call(service.register, body.ticket, body.email, body.password)


@router.post('/wk/reset', response_model=RegistrationResponse)
def wk_reset(body: WkResetRequest, service: WkService = Depends(get_wk_service)):
  """Nowe hasło dla konta powiązanego z PESEL-em (bez puli kodów). Nie loguje."""
  return _call(service.reset, body.ticket, body.password)

