# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

# Wywołania Frappe w imieniu użytkownika portalu.
#
# Konektor łączy się z Frappe kluczem API huba (konto usługowe). W czasie
# obsługi żądania użytkownika (zob. edu.dependencies.frappe_as_user) dokłada
# do każdego wywołania token Keycloak użytkownika (X-User-Token) i jego adres
# (X-Forwarded-For). Frappe (admissions_poland.keycloak.auth) sprawdza token
# i wykonuje wywołanie jako ta osoba. Bez kontekstu - np. zadania w tle -
# wywołanie zostaje wywołaniem usługi.

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Iterator

USER_TOKEN_HEADER = 'X-User-Token'
FORWARDED_FOR_HEADER = 'X-Forwarded-For'


@dataclass(frozen=True)
class UserContext:
  token: str
  client_ip: str | None = None

  def __repr__(self) -> str:
    # bez tokenu - repr trafia do logów i śladów stosu
    return f'UserContext(client_ip={self.client_ip!r})'


_current: ContextVar[UserContext | None] = ContextVar('frappe_user_context', default=None)


def current() -> UserContext | None:
  return _current.get()


def set_current(context: UserContext | None) -> None:
  _current.set(context)


@contextmanager
def acting_as(token: str | None, client_ip: str | None = None) -> Iterator[None]:
  """Wywołania Frappe w bloku idą w imieniu użytkownika tokenu (pusty token - bez zmian)."""
  previous = _current.get()
  _current.set(UserContext(token, client_ip) if token else previous)
  try:
    yield
  finally:
    _current.set(previous)


def bearer_token(authorization: str | None) -> str | None:
  """Token z nagłówka `Authorization: Bearer ...` (inne schematy - None)."""
  scheme, _, value = (authorization or '').strip().partition(' ')
  if scheme.lower() != 'bearer':
    return None
  return value.strip() or None


def forward_user_headers(request: Any) -> Any:
  """`requests` auth (wywoływane dla każdego żądania sesji): dokłada nagłówki
  użytkownika, jeśli jest kontekst. Nie zmienia nagłówka Authorization (klucz
  API huba)."""
  context = _current.get()
  if context is not None:
    request.headers[USER_TOKEN_HEADER] = context.token
    if context.client_ip:
      request.headers[FORWARDED_FOR_HEADER] = context.client_ip
  return request
