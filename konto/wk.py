# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

# login.gov.pl w usłudze kont - weryfikacja tożsamości, nie
# logowanie. Po zalogowaniu w Węźle Krajowym kandydat może:
#   * założyć konto Keycloak ze zweryfikowanym PESEL-em, imieniem i
#     nazwiskiem (bez kodu jednorazowego),
#   * ustawić nowe hasło konta powiązanego z jego PESEL-em - bez puli kodów.
# Usługa nie wydaje tokenów ani sesji; logowanie dalej w Keycloak.

from __future__ import annotations

import hmac
import logging
import re
import secrets
from dataclasses import dataclass
from datetime import timedelta, timezone

from sqlalchemy import delete, func, select

from connector.keycloak_admin.connector import (
  KeycloakAdminError,
  KeycloakConflict,
  KeycloakPasswordPolicy,
)
from connector.wk.saml import WkError
from konto.models import KontoWkSession
from konto.policy import PolicyError, normalize_email
from konto.service import MSG_UNAVAILABLE, KontoError, KontoService

logger = logging.getLogger('konto.wk')

PURPOSES = {'rejestracja': 'register', 'reset': 'reset'}
LOGIN_TTL = timedelta(minutes=10)
TICKET_TTL = timedelta(minutes=15)
IDENTITY_ATTRIBUTE = {'pesel': 'pesel_hmac', 'eid': 'eid_hmac'}
MSG_INVALID = 'Weryfikacja login.gov.pl wygasła albo jest nieprawidłowa. Zacznij od nowa.'


@dataclass(frozen=True)
class WkStart:
  sso_url: str
  saml_request: str
  relay_state: str


def identity_key(person_identifier: str) -> tuple[str, str]:
  """('pesel', PESEL) dla obywateli Polski, ('eid', identyfikator) dla eIDAS."""
  value = person_identifier.strip()
  if re.fullmatch(r'\d{11}', value):
    return 'pesel', value
  return 'eid', value.upper()


class WkService:

  def __init__(self, konto: KontoService, wk):
    self.konto = konto
    self.db = konto.db
    self.keycloak = konto.keycloak
    self.settings = konto.settings
    self.wk = wk
    self.now = konto.now

  # --- przekierowanie do WK ---

  def start(self, cel: str, client_ip: str | None) -> WkStart:
    purpose = PURPOSES.get(cel)
    if purpose is None:
      raise KontoError('Nieznany cel weryfikacji.')
    if self.wk is None or not self.wk.enabled:
      raise KontoError('Logowanie przez login.gov.pl jest niedostępne.', 503)
    now = self.now()
    self._purge(now)
    ip_hash = self.konto._hash('ip', client_ip or '')
    recent = self.db.scalar(select(func.count()).select_from(KontoWkSession).where(
      KontoWkSession.ip_hash == ip_hash, KontoWkSession.created_at >= now - timedelta(hours=1))) or 0
    if recent >= self.settings.tickets_per_ip_per_hour:
      raise KontoError('Zbyt wiele prób. Spróbuj za godzinę.', 429)
    request_id, saml_request = self.wk.authn_request(now.replace(tzinfo=timezone.utc))
    relay_state = secrets.token_urlsafe(24)
    self.db.add(KontoWkSession(id=relay_state, purpose=purpose, authn_request_id=request_id,
                               ip_hash=ip_hash, created_at=now, expires_at=now + LOGIN_TTL))
    self.db.commit()
    return WkStart(self.wk.sso_url, saml_request, relay_state)

  # --- powrót z WK (Assertion Consumer Service) ---

  def acs(self, artifact: str | None, relay_state: str | None) -> tuple[str, str]:
    """(ticket, cel) albo WkError z kodem dla portalu."""
    now = self.now()
    session = self.db.get(KontoWkSession, relay_state or '')
    if not artifact or session is None or session.verified_at or session.completed_at \
        or session.expires_at <= now:
      raise WkError('sesja_wygasla')
    # jednorazowo - kolejne użycie RelayState zostanie odrzucone
    session.completed_at = now
    self.db.commit()
    identity = self.wk.resolve_artifact(artifact, session.authn_request_id,
                                        now.replace(tzinfo=timezone.utc))
    kind, value = identity_key(identity.person_identifier)
    session.identity_kind = kind
    session.identity_hash = self.konto._hash(kind, value)
    session.first_name = identity.first_name[:100]
    session.last_name = identity.last_name[:100]
    session.ticket = secrets.token_urlsafe(32)
    session.verified_at = now
    session.completed_at = None
    session.expires_at = now + TICKET_TTL
    self.db.commit()
    logger.info('wk: tożsamość potwierdzona (sesja %s, %s)', session.id[:8], kind)
    cel = next(k for k, v in PURPOSES.items() if v == session.purpose)
    return session.ticket, cel

  # --- dane dla formularza portalu ---

  def info(self, ticket: str) -> dict:
    session = self._ticket(ticket)
    return {
      'cel': next(k for k, v in PURPOSES.items() if v == session.purpose),
      'first_name': session.first_name,
      'last_name': session.last_name,
      'account_exists': bool(self._accounts(session)),
      'expires_in': int((session.expires_at - self.now()).total_seconds()),
    }

  # --- założenie konta ---

  def register(self, ticket: str, email: str, password: str) -> dict:
    session = self._ticket(ticket, 'register')
    email = normalize_email(email)
    if not password:
      raise PolicyError('Podaj hasło.')
    if self._accounts(session):
      raise KontoError('Masz już konto powiązane z tym numerem PESEL. Ustaw nowe hasło '
                       'przez login.gov.pl.', 409)
    attribute = IDENTITY_ATTRIBUTE[session.identity_kind]
    try:
      if self.keycloak.find_by_email(email):
        raise KontoError('Ten adres e-mail jest już używany. Podaj inny.', 409)
      payload = {
        'username': email,
        'email': email,
        'firstName': session.first_name,
        'lastName': session.last_name,
        'enabled': True,
        'emailVerified': False,
        'attributes': {attribute: [session.identity_hash], 'identity_verified': ['wk']},
        'credentials': [{'type': 'password', 'value': password, 'temporary': False}],
      }
      if self.settings.group:
        payload['groups'] = [self.settings.group]
      self.keycloak.create_user(payload)
    except KeycloakConflict:
      raise KontoError('Ten adres e-mail jest już używany. Podaj inny.', 409)
    except KeycloakPasswordPolicy as exc:
      raise KontoError(f'Hasło nie spełnia wymagań ({exc.error}).', 400)
    except KeycloakAdminError as exc:
      logger.error('wk: rejestracja %s - %s', session.id[:8], exc)
      raise KontoError(MSG_UNAVAILABLE, 503)
    self._complete(session)
    logger.info('wk: założono konto (sesja %s)', session.id[:8])
    return {'username': email, 'login_url': self.settings.login_url or None}

  # --- nowe hasło ---

  def reset(self, ticket: str, password: str) -> dict:
    session = self._ticket(ticket, 'reset')
    if not password:
      raise PolicyError('Podaj nowe hasło.')
    users = self._accounts(session)
    if not users:
      raise KontoError('Nie znaleziono konta powiązanego z tym numerem PESEL. Załóż konto '
                       'albo skontaktuj się z komisją rekrutacyjną.', 404)
    if len(users) > 1:
      raise KontoError('Z tym numerem PESEL powiązano kilka kont. Skontaktuj się z komisją '
                       'rekrutacyjną.', 409)
    user = users[0]
    try:
      self.keycloak.reset_password(user['id'], password)
      self.keycloak.update_attributes(user['id'], {'identity_verified': ['wk']})
    except KeycloakPasswordPolicy as exc:
      raise KontoError(f'Hasło nie spełnia wymagań ({exc.error}).', 400)
    except KeycloakAdminError as exc:
      logger.error('wk: reset %s - %s', session.id[:8], exc)
      raise KontoError(MSG_UNAVAILABLE, 503)
    try:
      self.keycloak.logout(user['id'])
    except KeycloakAdminError as exc:
      logger.warning('wk: reset %s - wylogowanie sesji nieudane: %s', session.id[:8], exc)
    self._complete(session)
    logger.info('wk: zmieniono hasło (sesja %s)', session.id[:8])
    return {'username': user.get('username'), 'login_url': self.settings.login_url or None}

  # --- pomocnicze ---

  def _ticket(self, ticket: str, purpose: str | None = None) -> KontoWkSession:
    session = self.db.scalar(select(KontoWkSession).where(KontoWkSession.ticket == (ticket or '')))
    if (session is None or not session.verified_at or session.completed_at
        or session.expires_at <= self.now() or (purpose and session.purpose != purpose)
        or not hmac.compare_digest(session.ticket or '', ticket or '')):
      raise KontoError(MSG_INVALID)
    return session

  def _accounts(self, session: KontoWkSession) -> list[dict]:
    try:
      return self.keycloak.find_by_attribute(IDENTITY_ATTRIBUTE[session.identity_kind],
                                             session.identity_hash)
    except KeycloakAdminError as exc:
      logger.error('wk: wyszukiwanie konta - %s', exc)
      raise KontoError(MSG_UNAVAILABLE, 503)

  def _complete(self, session: KontoWkSession) -> None:
    session.completed_at = self.now()
    session.first_name = None
    session.last_name = None
    session.identity_hash = None
    self.db.commit()

  def _purge(self, now) -> None:
    # sesje starsze niż doba - razem z ewentualnymi danymi z WK
    self.db.execute(delete(KontoWkSession).where(KontoWkSession.created_at < now - timedelta(days=1)))
    expired = self.db.scalars(select(KontoWkSession).where(
      KontoWkSession.expires_at <= now, KontoWkSession.first_name.is_not(None))).all()
    for session in expired:
      session.first_name = session.last_name = session.identity_hash = None
    self.db.commit()
