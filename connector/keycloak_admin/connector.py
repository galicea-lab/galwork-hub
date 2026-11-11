# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

# Keycloak Admin REST API dla usługi kodów (konto/).
#
# Uwierzytelnienie: konto usługowe klienta (client_credentials), nie hasło
# administratora. Klientowi nadaje się tylko uprawnienia do kandydatów
# (osobny realm albo fine-grained admin permissions na grupę). Komunikaty
# błędów nie zawierają haseł ani treści żądań.

from __future__ import annotations

import time
from typing import Any
from urllib.parse import quote

import requests

from connector.base import BaseConnector, ConnectorError


class KeycloakAdminError(ConnectorError):
  """Błąd Admin API (bez treści żądania)."""

  def __init__(self, what: str, status_code: int | None = None, error: str = ''):
    self.what = what
    self.status_code = status_code
    self.error = error
    super().__init__(f'Keycloak: {what} (HTTP {status_code}{", " + error if error else ""})')


class KeycloakConflict(KeycloakAdminError):
  """Użytkownik o tej nazwie lub e-mailu już istnieje."""


class KeycloakPasswordPolicy(KeycloakAdminError):
  """Hasło nie spełnia polityki haseł realmu."""


def _error_key(resp: requests.Response) -> str:
  try:
    body = resp.json()
  except ValueError:
    return ''
  if not isinstance(body, dict):
    return ''
  return str(body.get('errorMessage') or body.get('error') or '')[:120]


def _raise_for(resp: requests.Response, what: str) -> None:
  if resp.ok:
    return
  error = _error_key(resp)
  if resp.status_code == 409:
    raise KeycloakConflict(what, resp.status_code, error)
  if resp.status_code == 400 and 'password' in error.lower():
    raise KeycloakPasswordPolicy(what, resp.status_code, error)
  raise KeycloakAdminError(what, resp.status_code, error)


class KeycloakAdminConnector(BaseConnector):
  name = 'keycloak_admin'

  def __init__(self, config=None) -> None:
    super().__init__(config)
    self._token: str | None = None
    self._token_expires = 0.0

  # --- połączenie ---

  @property
  def server_url(self) -> str:
    return self.config.require('server_url').rstrip('/')

  @property
  def realm(self) -> str:
    return self.config.require('realm')

  @property
  def timeout(self) -> int:
    return int(self.config.get('timeout', '15'))

  def connect(self) -> requests.Session:
    if self._client is None:
      session = requests.Session()
      session.verify = str(self.config.get('verify_tls', 'true')).lower() not in ('0', 'false', 'no')
      self._client = session
    return self._client

  def test_connection(self) -> bool:
    try:
      self._access_token()
      return True
    except ConnectorError as exc:
      self.logger.warning('keycloak_admin: test_connection() nieudany: %s', exc)
      return False

  def _access_token(self) -> str:
    if self._token and time.monotonic() < self._token_expires:
      return self._token
    url = f'{self.server_url}/realms/{quote(self.realm)}/protocol/openid-connect/token'
    try:
      resp = self.connect().post(url, data={
        'grant_type': 'client_credentials',
        'client_id': self.config.require('client_id'),
        'client_secret': self.config.require('client_secret'),
      }, timeout=self.timeout)
    except requests.RequestException as exc:
      raise KeycloakAdminError('token', None, type(exc).__name__) from exc
    _raise_for(resp, 'token')
    body = resp.json()
    self._token = body['access_token']
    self._token_expires = time.monotonic() + max(int(body.get('expires_in', 60)) - 30, 5)
    return self._token

  def _request(self, method: str, path: str, what: str, **kwargs) -> requests.Response:
    url = f'{self.server_url}/admin/realms/{quote(self.realm)}{path}'
    headers = {'Authorization': f'Bearer {self._access_token()}'}
    try:
      resp = self.connect().request(method, url, headers=headers, timeout=self.timeout, **kwargs)
    except requests.RequestException as exc:
      raise KeycloakAdminError(what, None, type(exc).__name__) from exc
    _raise_for(resp, what)
    return resp

  # --- użytkownicy ---

  def find_users(self, **params: Any) -> list[dict]:
    params.setdefault('max', 50)
    return self._request('GET', '/users', 'find_users', params=params).json()

  def find_by_attribute(self, name: str, value: str) -> list[dict]:
    users = self.find_users(q=f'{name}:{value}')
    # wyszukiwanie `q` w części wersji jest niedokładne - filtr po stronie huba
    return [u for u in users if value in (u.get('attributes') or {}).get(name, [])]

  def find_by_email(self, email: str) -> list[dict]:
    return self.find_users(email=email, exact='true')

  def find_by_login(self, login: str) -> dict | None:
    """Użytkownik po nazwie użytkownika albo e-mailu (dokładnie)."""
    for user in self.find_users(username=login, exact='true'):
      if str(user.get('username', '')).lower() == login.lower():
        return user
    users = self.find_by_email(login)
    return users[0] if len(users) == 1 else None

  def get_user(self, user_id: str) -> dict:
    return self._request('GET', f'/users/{quote(user_id)}', 'get_user').json()

  def update_attributes(self, user_id: str, attributes: dict[str, list[str]]) -> None:
    """Dopisuje atrybuty (PUT pełnej reprezentacji - profil użytkownika
    Keycloak 26 traktuje brakujące atrybuty jak usunięte)."""
    user = self.get_user(user_id)
    merged = dict(user.get('attributes') or {})
    merged.update(attributes)
    user['attributes'] = merged
    user.pop('access', None)
    user.pop('userProfileMetadata', None)
    self._request('PUT', f'/users/{quote(user_id)}', 'update_user', json=user)

  def create_user(self, payload: dict) -> str:
    resp = self._request('POST', '/users', 'create_user', json=payload)
    location = resp.headers.get('Location', '')
    return location.rstrip('/').rsplit('/', 1)[-1]

  def reset_password(self, user_id: str, password: str) -> None:
    self._request('PUT', f'/users/{quote(user_id)}/reset-password', 'reset_password',
                  json={'type': 'password', 'value': password, 'temporary': False})

  def logout(self, user_id: str) -> None:
    self._request('POST', f'/users/{quote(user_id)}/logout', 'logout')


_connector: KeycloakAdminConnector | None = None


def get_connector() -> KeycloakAdminConnector:
  global _connector
  if _connector is None:
    _connector = KeycloakAdminConnector()
  return _connector
