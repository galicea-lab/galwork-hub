# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

# Bramka SMS - sekcja [sms] w config/config.ini.
#
# backend = integracjasms - integracjasms.pl (REST/JSON, klucz API w
#                    nagłówku Bearer; tylko polskie numery komórkowe),
# backend = smsapi - SMSAPI.pl (REST, token OAuth w nagłówku Bearer),
# backend = log    - tylko środowisko deweloperskie: treść SMS-a (z kodem)
#                    trafia do logu zamiast do bramki.
# Logi produkcyjne nie zawierają treści ani pełnego numeru.

from __future__ import annotations

import requests

from connector.base import BaseConnector, ConnectorConfigError, ConnectorError

SMSAPI_URL = 'https://api.smsapi.pl/sms.do'
INTEGRACJASMS_URL = 'https://integracjasms.pl/api/v1/sms'


class SmsError(ConnectorError):
  """Nieudane wysłanie SMS-a (bez treści i numeru)."""


def _masked(phone: str) -> str:
  return f'{phone[:3]}…{phone[-3:]}' if len(phone) > 6 else '…'


class SmsConnector(BaseConnector):
  name = 'sms'

  @property
  def backend(self) -> str:
    return (self.config.get('backend', 'smsapi') or 'smsapi').strip().lower()

  def connect(self) -> requests.Session:
    if self._client is None:
      self._client = requests.Session()
    return self._client

  def test_connection(self) -> bool:
    return self.backend == 'log' or bool(self.config.get('token'))

  def send(self, phone: str, text: str) -> None:
    """Wysyła SMS na numer E.164 (`+48...`)."""
    if self.backend == 'log':
      self.logger.warning('SMS (backend log) do %s: %s', _masked(phone), text)
      return
    if self.backend == 'integracjasms':
      self._send_integracjasms(phone, text)
      return
    if self.backend != 'smsapi':
      raise ConnectorConfigError(f'[sms] backend: nieznany {self.backend!r}')
    data = {
      'to': phone.lstrip('+'),
      'message': text,
      'format': 'json',
      'encoding': 'utf-8',
    }
    sender = self.config.get('sender')
    if sender:
      data['from'] = sender
    if str(self.config.get('test', 'false')).lower() in ('1', 'true', 'yes'):
      data['test'] = '1'
    try:
      resp = self.connect().post(
        self.config.get('url', SMSAPI_URL), data=data,
        headers={'Authorization': f'Bearer {self.config.require("token")}'},
        timeout=int(self.config.get('timeout', '15')))
    except requests.RequestException as exc:
      raise SmsError(f'SMS: błąd połączenia ({type(exc).__name__})') from exc
    try:
      body = resp.json()
    except ValueError:
      body = {}
    if not resp.ok or 'error' in body:
      raise SmsError(f'SMS: odrzucony (HTTP {resp.status_code}, kod {body.get("error", "-")})')
    self.logger.info('SMS wysłany do %s', _masked(phone))

  def _send_integracjasms(self, phone: str, text: str) -> None:
    payload = {'to': phone, 'message': text}
    sender = self.config.get('sender')
    if sender:
      payload['from'] = sender
    if str(self.config.get('transliterate', 'false')).lower() in ('1', 'true', 'yes'):
      payload['transliterate'] = True
    reference = self.config.get('reference')
    if reference:
      payload['reference'] = reference
    try:
      resp = self.connect().post(
        self.config.get('url', INTEGRACJASMS_URL), json=payload,
        headers={'Authorization': f'Bearer {self.config.require("token")}'},
        timeout=int(self.config.get('timeout', '15')))
    except requests.RequestException as exc:
      raise SmsError(f'SMS: błąd połączenia ({type(exc).__name__})') from exc
    try:
      body = resp.json()
    except ValueError:
      body = {}
    if not resp.ok or not body.get('ok'):
      error = body.get('error') if isinstance(body.get('error'), dict) else {}
      raise SmsError(f'SMS: odrzucony (HTTP {resp.status_code}, kod {error.get("code", "-")})')
    left = body.get('credits_left')
    self.logger.info('SMS przyjęty do wysyłki do %s (pozostało kredytów: %s)', _masked(phone), left)


_connector: SmsConnector | None = None


def get_connector() -> SmsConnector:
  global _connector
  if _connector is None:
    _connector = SmsConnector()
  return _connector
