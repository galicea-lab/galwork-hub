# services/auth_service.py
# Własne wystawianie JWT (HS256, sekcja [auth]) zostało
# wycofane - jedynym mechanizmem uwierzytelniania huba jest Keycloak
# Ten moduł komunikuje się teraz z Keycloakem:
#   * hasła zakłada i resetuje administrator realms (python-keycloak),
#   * token użytkownika pozyskuje z token endpointu Keycloak
#     (grant_type=password - wymaga włączonych Direct Access Grants na kliencie).
import logging
from typing import Any

import bcrypt
import requests
from keycloak import KeycloakAdmin, KeycloakError
from sqlalchemy.orm import Session

from core.config import settings
from crud.crud_user import crud_user
from models.user import User

class KeycloakRegistrationError(Exception):
  """Rejestracja w Keycloaku odrzucona (istniejące konto / polityka haseł)."""


class KeycloakAuthenticationError(Exception):
  """Logowanie w Keycloaku odrzucone (złe dane lub brak direct access grants)."""


logger = logging.getLogger(__name__)

TOKEN_REQUEST_TIMEOUT = 10  # sekund - żeby wiszący Keycloak nie blokował workera


def hash_password(password: str) -> str:
  """Kontrahent Keycloaka przechowuje hasła sam; funkcja zostaje wyłącznie
  na potrzeby narzędzi administracyjnych (np. migracji kont lokalnych)."""
  return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed_password: str) -> bool:
  try:
    return bcrypt.checkpw(password.encode("utf-8"), hashed_password.encode("utf-8"))
  except ValueError:
    return False


def _openid_client() -> KeycloakAdmin:
  """Klient admina realms - pozwala zakładać konta w Keycloaku."""
  return KeycloakAdmin(
    server_url=settings.keycloak.server_url,
    realm_name=settings.keycloak.realm_name,
    username=settings.keycloak.admin,
    password=settings.keycloak.admin_password,
    verify=True,
  )


def keycloak_user_id(email: str) -> str | None:
  """Zwraca id (sub) użytkownika w Keycloaku albo None."""
  try:
    return _openid_client().get_user_id(email)
  except KeycloakError as exc:
    logger.warning('Keycloak: nie udało się pobrać użytkownika %s: %s', email, exc)
    return None


def register_in_keycloak(email: str, password: str, first_name: str = '', last_name: str = '') -> str:
  """Tworzy konto w Keycloaku i zwraca jego id (sub).

  Wystawia KeycloakRegistrationError, gdy konto już istnieje lub hasło nie
  spełnia polityki haseł - komunikat trafia wtedy do klienta (4xx), a nie jest
  połykany po cichu.
  """
  admin = _openid_client()
  if admin.get_user_id(email):
    raise KeycloakRegistrationError('Użytkownik o tym adresie e-mail już istnieje')
  try:
    return admin.create_user({
      "email": email,
      "emailVerified": True,
      "username": email,
      "enabled": True,
      "firstName": first_name,
      "lastName": last_name,
      "credentials": [{
        "value": password,
        "type": "password",
        "temporary": False,
      }],
    }) or ''
  except KeycloakError as exc:
    # Najczęstsza przyczyna: polityka haseł Keycloaka.
    raise KeycloakRegistrationError(f'Rejestracja w Keycloaku odrzucona: {exc}') from exc


def authenticate_in_keycloak(username: str, password: str) -> dict[str, Any]:
  """Wymienia login i hasło na tokeny Keycloak (grant_type=password).

  Wymaga, żeby klient z sekcji [keycloak] miał włączone
  "Direct Access Grants"; w przeciwnym razie Keycloak zwraca
  error=unauthorized_client - komunikat trafia do klienta jako 401.
  """
  url = (f'{settings.keycloak.server_url}/realms/{settings.keycloak.realm_name}'
         f'/protocol/openid-connect/token')
  response = requests.post(
    url,
    data={
      'grant_type': 'password',
      'client_id': settings.keycloak.client_id,
      'username': username,
      'password': password,
    },
    headers={'Content-Type': 'application/x-www-form-urlencoded'},
    timeout=TOKEN_REQUEST_TIMEOUT,
  )
  if response.status_code != 200:
    logger.info('Keycloak: logowanie odrzucone dla %s (HTTP %s)', username, response.status_code)
    raise KeycloakAuthenticationError('Nieprawidłowy e-mail lub hasło')
  payload = response.json()
  if not payload.get('access_token'):
    raise KeycloakAuthenticationError('Keycloak nie zwrócił access_token')
  return payload


def get_or_create_local_user(db: Session, email: str, username: str, sub: str | None = None) -> User:
  """Znajduje lub tworzy rekord models.user.User dla konta z Keycloaka.

  Kolejność: keycloak_sub (stabilny identyfikator) -> email (zgodność
  z dotychczasowymi rekordami) -> nowy rekord.
  """
  user = None
  if sub:
    user = db.query(User).filter(User.keycloak_sub == sub).first()
  if user is None:
    user = crud_user.get_user_by_email(db, email=email)
  if user is not None:
    if sub and not user.keycloak_sub:
      user.keycloak_sub = sub
      db.commit()
      db.refresh(user)
    return user
  db_user = crud_user.create_user(db, user_data={
    'email': email,
    'name': username,
    # hasło lokalne nie jest używane - uwierzytelnianiem
    # zajmuje się Keycloak; znacznik informuje, że to konto SSO.
    'hashed_password': 'SSO_USER_NO_PASSWORD',
    **({'keycloak_sub': sub} if sub else {}),
  })
  return db_user
