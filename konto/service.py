# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

# Usługa kodów jednorazowych.
#
# Kod służy wyłącznie do założenia konta w Keycloak albo ustawienia nowego
# hasła - usługa nie wydaje tokenów ani sesji, po operacji użytkownik loguje
# się zwykle w Keycloak. Kanały (SMS / e-mail), pula operacji i limity
# wysyłek - konto.config.KontoSettings. W bazie tylko skróty HMAC.

from __future__ import annotations

import hmac
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from connector.base import ConnectorError
from connector.keycloak_admin.connector import (
  KeycloakAdminError,
  KeycloakConflict,
  KeycloakPasswordPolicy,
)
from konto.captcha import CaptchaVerifier
from konto.config import KontoSettings
from konto.models import KontoOperation, KontoTicket
from konto.policy import (
  PolicyError,
  keyed_hash,
  new_code,
  new_ticket_id,
  normalize_email,
  normalize_pesel,
  normalize_phone,
  phone_country_allowed,
)

logger = logging.getLogger('konto.service')

REGISTER = 'register'
RESET = 'reset'

MSG_NEUTRAL_REGISTER = ('Nie można założyć konta z tymi danymi. Jeśli masz już konto, '
                        'odzyskaj hasło; w innym razie skontaktuj się z komisją rekrutacyjną.')
MSG_NEUTRAL_RESET = 'Jeśli konto istnieje, wysłaliśmy kod na zweryfikowany telefon lub e-mail.'
MSG_CODE_SENT = 'Wysłaliśmy kod jednorazowy.'
MSG_QUOTA = ('Wykorzystano limit kodów jednorazowych. Skontaktuj się z komisją rekrutacyjną '
             'albo zaloguj się przez login.gov.pl.')
MSG_INVALID_CODE = 'Nieprawidłowy lub nieważny kod.'
MSG_UNAVAILABLE = 'Usługa kont jest chwilowo niedostępna. Spróbuj później.'
MSG_SEND_FAILED = 'Nie udało się wysłać kodu. Spróbuj później albo wybierz inny kanał.'


class KontoError(Exception):
  """Błąd z komunikatem dla użytkownika i kodem HTTP."""

  def __init__(self, message: str, status: int = 400):
    super().__init__(message)
    self.message = message
    self.status = status


@dataclass(frozen=True)
class TicketInfo:
  ticket: str
  expires_in: int
  resend_after: int
  message: str


def utcnow() -> datetime:
  return datetime.now(timezone.utc).replace(tzinfo=None)


def _first(attributes: dict, name: str) -> str:
  values = (attributes or {}).get(name) or ['']
  return str(values[0])


class KontoService:

  def __init__(self, settings: KontoSettings, db: Session, keycloak: Any,
               sms: Any = None, mail: Any = None, captcha: CaptchaVerifier | None = None,
               now: Callable[[], datetime] = utcnow):
    self.settings = settings
    self.secret = settings.require_secret()
    self.db = db
    self.keycloak = keycloak
    self.sms = sms
    self.mail = mail
    self.captcha = captcha or CaptchaVerifier('', '')
    self.now = now
    # ustawiane przez router, gdy [wk] enabled=true
    self.wk_enabled = False

  # --- konfiguracja publiczna (dla formularza portalu) ---

  def public_config(self) -> dict:
    s = self.settings
    return {
      'channels': [ch for ch in s.channels if self._sender(ch) is not None],
      'sms_country_codes': list(s.sms_country_codes),
      'pesel': s.pesel,
      'captcha_site_key': s.captcha_site_key or None,
      'code_length': s.code_length,
      'code_ttl': s.code_ttl,
      'operations_quota': s.operations_quota,
      'login_gov_pl': bool(self.wk_enabled),
    }

  # --- założenie konta ---

  def start_registration(self, channel: str, destination: str, client_ip: str | None,
                         captcha_token: str | None) -> TicketInfo:
    destination = self._destination(channel, destination)
    self._check_captcha(captcha_token, client_ip)
    destination_hash = self._hash(channel, destination)
    ip_hash = self._hash('ip', client_ip or '')
    self._limit_ip(ip_hash)
    self._limit_destination(destination_hash)
    if channel == 'sms':
      self._limit_sms_budget()
    if self._operations_used(destination_hash) >= self.settings.operations_quota:
      raise KontoError(MSG_QUOTA, 403)
    self._ensure_channel_free(channel, destination)
    ticket, code = self._new_ticket(REGISTER, channel, destination_hash, ip_hash)
    self._send_code(ticket, destination, code)
    return self._info(ticket, MSG_CODE_SENT)

  def complete_registration(self, ticket_id: str, code: str, *, email: str,
                            first_name: str, last_name: str, password: str,
                            phone: str | None = None, pesel: str | None = None) -> dict:
    ticket = self._verify(ticket_id, code, REGISTER)
    email = normalize_email(email)
    phone = normalize_phone(phone, self.settings.default_country_code) if phone else None
    destination = phone if ticket.channel == 'sms' else email
    if not destination or not hmac.compare_digest(
        self._hash(ticket.channel, destination), ticket.destination_hash):
      raise KontoError('Dane kontaktowe nie zgadzają się z kanałem, na który wysłano kod.')
    first_name = self._name(first_name, 'imię')
    last_name = self._name(last_name, 'nazwisko')
    if not password:
      raise KontoError('Podaj hasło.')
    pesel_hash = self._pesel_hash(pesel)

    attributes: dict[str, list[str]] = {}
    if ticket.channel == 'sms':
      attributes['phone_number'] = [phone]
      attributes['phone_number_verified'] = ['true']
    if pesel_hash:
      attributes['pesel_hmac'] = [pesel_hash]
    username = phone.lstrip('+') if (self.settings.username == 'phone' and phone) else email

    try:
      if pesel_hash and self.keycloak.find_by_attribute('pesel_hmac', pesel_hash):
        logger.info('konto: rejestracja %s odrzucona - PESEL już użyty', ticket.id[:8])
        raise KontoError(MSG_NEUTRAL_REGISTER, 409)
      self._ensure_channel_free(ticket.channel, destination)
      if ticket.channel == 'sms' and self.keycloak.find_by_email(email):
        raise KontoError(MSG_NEUTRAL_REGISTER, 409)
      payload = {
        'username': username,
        'email': email,
        'firstName': first_name,
        'lastName': last_name,
        'enabled': True,
        'emailVerified': ticket.channel == 'email',
        'attributes': attributes,
        'credentials': [{'type': 'password', 'value': password, 'temporary': False}],
      }
      if self.settings.group:
        payload['groups'] = [self.settings.group]
      user_id = self.keycloak.create_user(payload)
    except KeycloakConflict:
      raise KontoError(MSG_NEUTRAL_REGISTER, 409)
    except KeycloakPasswordPolicy as exc:
      raise KontoError(f'Hasło nie spełnia wymagań ({exc.error}).', 400)
    except KeycloakAdminError as exc:
      logger.error('konto: rejestracja %s - %s', ticket.id[:8], exc)
      raise KontoError(MSG_UNAVAILABLE, 503)

    self._complete(ticket, user_id)
    logger.info('konto: założono konto (przepustka %s, kanał %s)', ticket.id[:8], ticket.channel)
    return {'username': username, 'login_url': self.settings.login_url or None}

  # --- odzyskanie hasła ---

  def start_reset(self, login: str, client_ip: str | None, captcha_token: str | None,
                  channel: str | None = None) -> TicketInfo:
    login = (login or '').strip().lower()
    if not login or len(login) > 254:
      raise PolicyError('Podaj login albo adres e-mail')
    if channel is not None and channel not in self.settings.channels:
      raise KontoError('Ten kanał jest niedostępny.')
    self._check_captcha(captcha_token, client_ip)
    ip_hash = self._hash('ip', client_ip or '')
    self._limit_ip(ip_hash)
    try:
      user = self.keycloak.find_by_login(login)
    except KeycloakAdminError as exc:
      logger.error('konto: reset - %s', exc)
      raise KontoError(MSG_UNAVAILABLE, 503)

    chosen = self._reset_channel(user, channel)
    if chosen:
      channel, destination = chosen
      destination_hash = self._hash(channel, destination)
    else:
      destination = None
      destination_hash = self._hash('login', login)
    self._limit_destination(destination_hash)

    real = bool(chosen) and self._operations_used(
      destination_hash, user['id']) < self.settings.operations_quota
    if real and channel == 'sms' and self._sms_sent_today() >= self.settings.sms_per_day:
      real = False
      logger.warning('konto: dzienny limit SMS wyczerpany - reset bez wysyłki')
    if not real:
      # kanał 'none' - przepustka pozorna nie zużywa dziennego limitu SMS
      ticket, _ = self._new_ticket(RESET, 'none', destination_hash, ip_hash, with_code=False)
      return self._info(ticket, MSG_NEUTRAL_RESET)

    ticket, code = self._new_ticket(RESET, channel, destination_hash, ip_hash, user_id=user['id'])
    try:
      self._send_code(ticket, destination, code)
    except KontoError:
      # bez zdradzania, że konto istnieje
      pass
    return self._info(ticket, MSG_NEUTRAL_RESET)

  def complete_reset(self, ticket_id: str, code: str, password: str) -> None:
    ticket = self._verify(ticket_id, code, RESET)
    if not password:
      raise KontoError('Podaj nowe hasło.')
    try:
      self.keycloak.reset_password(ticket.user_id, password)
    except KeycloakPasswordPolicy as exc:
      raise KontoError(f'Hasło nie spełnia wymagań ({exc.error}).', 400)
    except KeycloakAdminError as exc:
      logger.error('konto: reset %s - %s', ticket.id[:8], exc)
      raise KontoError(MSG_UNAVAILABLE, 503)
    self._complete(ticket, ticket.user_id)
    try:
      self.keycloak.logout(ticket.user_id)
    except KeycloakAdminError as exc:
      logger.warning('konto: reset %s - wylogowanie sesji nieudane: %s', ticket.id[:8], exc)
    self._notify_reset(ticket)
    logger.info('konto: zmieniono hasło (przepustka %s, kanał %s)', ticket.id[:8], ticket.channel)

  # --- ponowne wysłanie ---

  def resend(self, ticket_id: str, destination: str | None = None) -> TicketInfo:
    ticket = self.db.get(KontoTicket, ticket_id or '')
    now = self.now()
    if ticket is None or ticket.completed_at or ticket.expires_at <= now:
      raise KontoError('Kod wygasł. Zacznij od nowa.', 400)
    if ticket.last_sent_at and (now - ticket.last_sent_at).total_seconds() < self.settings.resend_interval:
      raise KontoError('Odczekaj chwilę przed ponownym wysłaniem kodu.', 429)
    if ticket.sends >= self.settings.max_sends:
      raise KontoError('Wykorzystano limit ponownych wysłań. Zacznij od nowa.', 429)
    neutral = MSG_NEUTRAL_RESET if ticket.purpose == RESET else MSG_CODE_SENT

    if ticket.code_hash is None:
      ticket.sends += 1
      ticket.last_sent_at = now
      self.db.commit()
      return self._info(ticket, neutral)

    if ticket.purpose == REGISTER:
      destination = self._destination(ticket.channel, destination)
      if not hmac.compare_digest(self._hash(ticket.channel, destination), ticket.destination_hash):
        raise KontoError('Dane kontaktowe nie zgadzają się z kanałem, na który wysłano kod.')
    else:
      destination = self._user_destination(ticket)
    if ticket.channel == 'sms':
      self._limit_sms_budget()

    code = new_code(self.settings.code_length)
    ticket.code_hash = self._code_hash(ticket.id, code)
    ticket.expires_at = now + timedelta(seconds=self.settings.code_ttl)
    try:
      self._send_code(ticket, destination, code)
    except KontoError:
      if ticket.purpose == RESET:
        return self._info(ticket, neutral)
      raise
    return self._info(ticket, neutral)

  # --- pomocnicze ---

  def _hash(self, kind: str, value: str) -> str:
    return keyed_hash(self.secret, kind, value)

  def _code_hash(self, ticket_id: str, code: str) -> str:
    return keyed_hash(self.secret, f'code:{ticket_id}', code)

  def _sender(self, channel: str):
    return self.sms if channel == 'sms' else self.mail

  def _destination(self, channel: str, raw: str | None) -> str:
    if channel not in self.settings.channels or self._sender(channel) is None:
      raise KontoError('Ten kanał jest niedostępny.')
    if channel == 'email':
      return normalize_email(raw)
    phone = normalize_phone(raw, self.settings.default_country_code)
    if not phone_country_allowed(phone, self.settings.sms_country_codes):
      raise KontoError('Dla tego numeru nie wysyłamy SMS-ów - wybierz kod e-mailem.')
    return phone

  @staticmethod
  def _name(value: str | None, label: str) -> str:
    value = ' '.join((value or '').split())
    if not value or len(value) > 100:
      raise PolicyError(f'Podaj {label}')
    return value

  def _pesel_hash(self, pesel: str | None) -> str | None:
    mode = self.settings.pesel
    if mode == 'off':
      return None
    if not pesel:
      if mode == 'required':
        raise PolicyError('Podaj PESEL')
      return None
    return self._hash('pesel', normalize_pesel(pesel))

  def _check_captcha(self, token: str | None, client_ip: str | None) -> None:
    if not self.captcha.verify(token, client_ip):
      raise KontoError('Potwierdź, że nie jesteś robotem (CAPTCHA).', 400)

  def _count_tickets(self, *conditions) -> int:
    return self.db.scalar(select(func.count()).select_from(KontoTicket).where(*conditions)) or 0

  def _limit_ip(self, ip_hash: str) -> None:
    since = self.now() - timedelta(hours=1)
    if self._count_tickets(KontoTicket.ip_hash == ip_hash,
                           KontoTicket.created_at >= since) >= self.settings.tickets_per_ip_per_hour:
      raise KontoError('Zbyt wiele prób. Spróbuj za godzinę.', 429)

  def _limit_destination(self, destination_hash: str) -> None:
    since = self.now() - timedelta(days=1)
    if self._count_tickets(KontoTicket.destination_hash == destination_hash,
                           KontoTicket.created_at >= since) >= self.settings.tickets_per_destination_per_day:
      raise KontoError('Zbyt wiele prób. Spróbuj jutro.', 429)

  def _sms_sent_today(self) -> int:
    since = self.now() - timedelta(days=1)
    return self.db.scalar(select(func.coalesce(func.sum(KontoTicket.sends), 0)).where(
      KontoTicket.channel == 'sms', KontoTicket.created_at >= since)) or 0

  def _limit_sms_budget(self) -> None:
    if self._sms_sent_today() >= self.settings.sms_per_day:
      logger.warning('konto: dzienny limit SMS wyczerpany')
      raise KontoError('Wysyłka SMS jest chwilowo niedostępna - wybierz kod e-mailem.', 503)

  def _operations_used(self, destination_hash: str, user_id: str | None = None) -> int:
    condition = KontoOperation.destination_hash == destination_hash
    if user_id:
      condition = or_(condition, KontoOperation.user_id == user_id)
    return self.db.scalar(select(func.count()).select_from(KontoOperation).where(condition)) or 0

  def _ensure_channel_free(self, channel: str, destination: str) -> None:
    try:
      if channel == 'sms':
        taken = len(self.keycloak.find_by_attribute('phone_number', destination)) >= \
          self.settings.accounts_per_phone
      else:
        taken = bool(self.keycloak.find_by_email(destination))
    except KeycloakAdminError as exc:
      logger.error('konto: sprawdzenie kanału - %s', exc)
      raise KontoError(MSG_UNAVAILABLE, 503)
    if taken:
      raise KontoError(MSG_NEUTRAL_REGISTER, 409)

  def _reset_channel(self, user: dict | None, requested: str | None) -> tuple[str, str] | None:
    if not user or not user.get('enabled', True):
      return None
    attributes = user.get('attributes') or {}
    phone = _first(attributes, 'phone_number')
    phone_ok = _first(attributes, 'phone_number_verified').lower() == 'true'
    for channel in self.settings.channels:
      if requested and channel != requested:
        continue
      if self._sender(channel) is None:
        continue
      if channel == 'sms' and phone and phone_ok and \
          phone_country_allowed(phone, self.settings.sms_country_codes):
        return 'sms', phone
      if channel == 'email' and user.get('email') and user.get('emailVerified'):
        return 'email', str(user['email']).lower()
    return None

  def _user_destination(self, ticket: KontoTicket) -> str:
    try:
      user = self.keycloak.get_user(ticket.user_id)
    except KeycloakAdminError as exc:
      logger.error('konto: ponowienie %s - %s', ticket.id[:8], exc)
      raise KontoError(MSG_UNAVAILABLE, 503)
    chosen = self._reset_channel(user, ticket.channel)
    if not chosen or not hmac.compare_digest(self._hash(*chosen), ticket.destination_hash):
      raise KontoError('Kod wygasł. Zacznij od nowa.', 400)
    return chosen[1]

  def _new_ticket(self, purpose: str, channel: str, destination_hash: str, ip_hash: str,
                  user_id: str | None = None, with_code: bool = True) -> tuple[KontoTicket, str | None]:
    now = self.now()
    ticket = KontoTicket(
      id=new_ticket_id(), purpose=purpose, channel=channel,
      destination_hash=destination_hash, user_id=user_id, ip_hash=ip_hash,
      created_at=now, expires_at=now + timedelta(seconds=self.settings.code_ttl),
      sends=0, attempts=0)
    code = None
    if with_code:
      code = new_code(self.settings.code_length)
      ticket.code_hash = self._code_hash(ticket.id, code)
    else:
      # przepustka pozorna liczy się do limitów jak prawdziwa
      ticket.sends = 1
      ticket.last_sent_at = now
    self.db.add(ticket)
    self.db.commit()
    return ticket, code

  def _send_code(self, ticket: KontoTicket, destination: str, code: str) -> None:
    # wysyłka liczy się do limitów także wtedy, gdy bramka ją odrzuci
    ticket.sends += 1
    ticket.last_sent_at = self.now()
    self.db.commit()
    minutes = max(self.settings.code_ttl // 60, 1)
    try:
      if ticket.channel == 'sms':
        self.sms.send(destination, self.settings.sms_template.format(code=code, minutes=minutes))
      else:
        self.mail.send(destination, self.settings.mail_subject,
                       self.settings.mail_template.format(code=code, minutes=minutes))
    except ConnectorError as exc:
      logger.error('konto: wysyłka kodu %s nieudana - %s', ticket.id[:8], exc)
      raise KontoError(MSG_SEND_FAILED, 502)

  def _verify(self, ticket_id: str, code: str, purpose: str) -> KontoTicket:
    ticket = self.db.get(KontoTicket, ticket_id or '')
    now = self.now()
    if (ticket is None or ticket.purpose != purpose or ticket.completed_at
        or ticket.expires_at <= now or ticket.attempts >= self.settings.max_attempts):
      raise KontoError(MSG_INVALID_CODE)
    code = (code or '').strip()
    if ticket.code_hash is None or not hmac.compare_digest(
        self._code_hash(ticket.id, code), ticket.code_hash):
      ticket.attempts += 1
      self.db.commit()
      raise KontoError(MSG_INVALID_CODE)
    return ticket

  def _complete(self, ticket: KontoTicket, user_id: str | None) -> None:
    now = self.now()
    ticket.completed_at = now
    self.db.add(KontoOperation(purpose=ticket.purpose, destination_hash=ticket.destination_hash,
                               user_id=user_id, created_at=now))
    self.db.commit()

  def _notify_reset(self, ticket: KontoTicket) -> None:
    if self.mail is None:
      return
    try:
      user = self.keycloak.get_user(ticket.user_id)
      if user.get('email') and user.get('emailVerified'):
        self.mail.send(user['email'], self.settings.reset_notice_subject,
                       self.settings.reset_notice_template)
    except ConnectorError as exc:
      logger.warning('konto: powiadomienie o zmianie hasła nieudane - %s', exc)

  def _info(self, ticket: KontoTicket, message: str) -> TicketInfo:
    remaining = int((ticket.expires_at - self.now()).total_seconds())
    return TicketInfo(ticket=ticket.id, expires_in=max(remaining, 0),
                      resend_after=self.settings.resend_interval, message=message)
