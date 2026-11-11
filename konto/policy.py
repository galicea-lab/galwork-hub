# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

# Normalizacja kanałów (telefon, e-mail), PESEL, skróty HMAC
# i kody jednorazowe. Bez zależności od bazy i sieci.

from __future__ import annotations

import hashlib
import hmac
import re
import secrets


class PolicyError(ValueError):
  """Niepoprawne dane wejściowe (komunikat dla użytkownika)."""


_EMAIL_RE = re.compile(r'^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$')
_PHONE_SEPARATORS = re.compile(r'[\s\-().]')


def normalize_email(value: str | None) -> str:
  email = (value or '').strip().lower()
  if len(email) > 254 or not _EMAIL_RE.match(email):
    raise PolicyError('Niepoprawny adres e-mail')
  return email


def normalize_phone(value: str | None, default_country_code: str = '48') -> str:
  """Numer w formacie E.164 (`+48500100200`)."""
  phone = _PHONE_SEPARATORS.sub('', value or '')
  if phone.startswith('00'):
    phone = '+' + phone[2:]
  elif not phone.startswith('+'):
    if len(phone) == 9 and default_country_code:
      phone = f'+{default_country_code}{phone}'
    else:
      raise PolicyError('Podaj numer telefonu z numerem kierunkowym kraju, np. +48 500 100 200')
  if not re.fullmatch(r'\+[1-9]\d{7,14}', phone):
    raise PolicyError('Niepoprawny numer telefonu')
  return phone


def phone_country_allowed(phone: str, country_codes: tuple[str, ...]) -> bool:
  digits = phone.lstrip('+')
  return any(digits.startswith(code) for code in country_codes)


_PESEL_WEIGHTS = (1, 3, 7, 9, 1, 3, 7, 9, 1, 3)


def normalize_pesel(value: str | None) -> str:
  pesel = (value or '').strip()
  if not re.fullmatch(r'\d{11}', pesel):
    raise PolicyError('PESEL musi mieć 11 cyfr')
  checksum = (10 - sum(int(d) * w for d, w in zip(pesel, _PESEL_WEIGHTS)) % 10) % 10
  if checksum != int(pesel[10]):
    raise PolicyError('Niepoprawny PESEL (suma kontrolna)')
  return pesel


def keyed_hash(secret: bytes, kind: str, value: str) -> str:
  """Skrót HMAC-SHA256 - w bazie i w Keycloak zamiast danych jawnych."""
  return hmac.new(secret, f'{kind}:{value}'.encode(), hashlib.sha256).hexdigest()


def new_code(length: int) -> str:
  return ''.join(secrets.choice('0123456789') for _ in range(length))


def new_ticket_id() -> str:
  return secrets.token_urlsafe(24)


def mask_phone(phone: str) -> str:
  return f'{phone[:3]}…{phone[-3:]}' if len(phone) > 6 else '…'


def mask_email(email: str) -> str:
  local, _, domain = email.partition('@')
  return f'{local[:1]}…@{domain}' if domain else '…'
