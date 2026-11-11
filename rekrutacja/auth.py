# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

# Tokeny kandydatów - osobny realm Keycloak (sekcja
# [rekrutacja] w config/config.ini), niezależny od realmu pracowników
# ([keycloak]). Hub sprawdza token (podpis RS256 kluczem realmu, iss, exp,
# azp, grupa kandydatów) i wywołuje Frappe w imieniu kandydata: klucz API
# huba + X-User-Token (connector/frappe_education/user_context.py). Frappe
# (admissions_poland.keycloak.auth) sprawdza token ponownie i działa jako
# konto kandydata.

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import AsyncIterator

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient

from config.config import section
from connector.frappe_education.user_context import UserContext, set_current


@dataclass(frozen=True)
class RekrutacjaSettings:
  issuer: str
  jwks_url: str
  allowed_clients: tuple[str, ...]
  audience: tuple[str, ...]
  candidate_group: str
  leeway: int

  @classmethod
  def load(cls) -> 'RekrutacjaSettings':
    values = section('rekrutacja')
    issuer = (values.get('issuer') or '').rstrip('/')
    split = lambda key: tuple(v.strip() for v in (values.get(key) or '').split(',') if v.strip())  # noqa: E731
    return cls(
      issuer=issuer,
      jwks_url=values.get('jwks_url') or (f'{issuer}/protocol/openid-connect/certs' if issuer else ''),
      allowed_clients=split('allowed_clients'),
      audience=split('audience'),
      candidate_group=(values.get('candidate_group') or 'kandydaci').strip().lstrip('/').lower(),
      leeway=int(values.get('leeway') or 30),
    )


@dataclass(frozen=True)
class Candidate:
  sub: str
  email: str
  token: str

  def __repr__(self) -> str:
    return f'Candidate(sub={self.sub!r})'


@lru_cache(maxsize=1)
def rekrutacja_settings() -> RekrutacjaSettings:
  return RekrutacjaSettings.load()


@lru_cache(maxsize=4)
def _jwk_client(url: str) -> PyJWKClient:
  return PyJWKClient(url, cache_keys=True, lifespan=300, timeout=10)


def groups(claims: dict) -> set[str]:
  return {str(g).strip().lstrip('/').split('/')[-1].lower() for g in claims.get('groups') or []}


def _unauthorized(detail: str) -> HTTPException:
  return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail,
                       headers={'WWW-Authenticate': 'Bearer'})


def verify_candidate_token(token: str, settings: RekrutacjaSettings | None = None,
                           key=None) -> Candidate:
  settings = settings or rekrutacja_settings()
  if not settings.issuer:
    raise HTTPException(status_code=503, detail='Rekrutacja nie jest skonfigurowana ([rekrutacja] issuer)')
  try:
    signing_key = key or _jwk_client(settings.jwks_url).get_signing_key_from_jwt(token).key
    claims = jwt.decode(
      token, signing_key, algorithms=['RS256'], issuer=settings.issuer,
      audience=list(settings.audience) or None, leeway=settings.leeway,
      options={'require': ['exp', 'iat', 'sub'], 'verify_aud': bool(settings.audience)})
  except jwt.ExpiredSignatureError:
    raise _unauthorized('Sesja wygasła - zaloguj się ponownie')
  except jwt.PyJWTError:
    raise _unauthorized('Nieprawidłowy token')
  if settings.allowed_clients and claims.get('azp') not in settings.allowed_clients:
    raise _unauthorized('Token wydany dla innej aplikacji')
  if claims.get('typ') not in (None, 'Bearer'):
    raise _unauthorized('Nieprawidłowy typ tokenu')
  if settings.candidate_group not in groups(claims):
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Konto nie należy do grupy kandydatów')
  return Candidate(sub=claims['sub'], email=str(claims.get('email') or '').lower(), token=token)


_bearer = HTTPBearer(auto_error=False)


async def current_candidate(
  request: Request,
  credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> AsyncIterator[Candidate]:
  """Zalogowany kandydat; w czasie obsługi żądania wywołania Frappe idą w jego imieniu."""
  if credentials is None or credentials.scheme.lower() != 'bearer' or not credentials.credentials:
    raise _unauthorized('Zaloguj się')
  candidate = verify_candidate_token(credentials.credentials)
  set_current(UserContext(candidate.token, request.client.host if request.client else None))
  try:
    yield candidate
  finally:
    set_current(None)
