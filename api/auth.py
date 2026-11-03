# api/auth.py
# 2026-10-05: rejestracja i logowanie zostały przepięte na Keycloak -
# hub nie wystawia już własnych tokenów JWT (HS256). Token zwracany przez
# /auth/login jest tokenem Keycloaka i jest weryfikowany tak samo jak w
# api/security.validate_token. Zob. TODO.md pkt 4 i 2026-10-05.md.
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from crud.crud_user import crud_user
from schemas.user_schemas import UserSchema
from schemas.auth_schemas import AccessTokenResponse, LoginRequest, RegisterRequest
from services.auth_service import (
  KeycloakAuthenticationError,
  KeycloakRegistrationError,
  authenticate_in_keycloak,
  get_or_create_local_user,
  register_in_keycloak,
)
from .security import get_current_user
from models.user import User
from api.dependencies import get_db

router = APIRouter(prefix="/auth", tags=["auth"])


def _user_schema(user: User) -> UserSchema:
  # `id_from_keycloak` w schemacie to `sub` z Keycloaka
  # przechowywany w models.user.User.keycloak_sub.
  return UserSchema(email=user.email, id=user.id, id_from_keycloak=user.keycloak_sub)


@router.post("/register", response_model=AccessTokenResponse, status_code=201)
def register(data: RegisterRequest, db: Session = Depends(get_db)):
  """Zakłada konto w Keycloaku i od razu zwraca token (grant_type=password)."""
  if crud_user.get_user_by_email(db, email=data.email):
    raise HTTPException(status_code=400, detail="Użytkownik o tym adresie e-mail już istnieje")

  name = (data.name or '').strip()
  first_name, _, last_name = name.partition(' ')
  try:
    keycloak_sub = register_in_keycloak(data.email, data.password, first_name, last_name)
  except KeycloakRegistrationError as exc:
    raise HTTPException(status_code=400, detail=str(exc))

  user = get_or_create_local_user(db, email=data.email, username=name or data.email, sub=keycloak_sub)
  try:
    tokens = authenticate_in_keycloak(data.email, data.password)
  except KeycloakAuthenticationError as exc:
    # Konto istnieje, ale nie dało się zalogować (np. polityka haseł wymaga
    # zmiany hasła przy pierwszym wejściu) - nie zostawiamy konta bez wyjaśnienia.
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc))

  return AccessTokenResponse(
    access_token=tokens['access_token'],
    expires_in=tokens.get('expires_in'),
    refresh_token=tokens.get('refresh_token'),
    user=_user_schema(user),
  )


@router.post("/login", response_model=AccessTokenResponse)
def login(data: LoginRequest, db: Session = Depends(get_db)):
  """Loguje w Keycloaku (wymaga włączonych Direct Access Grants na kliencie)."""
  try:
    tokens = authenticate_in_keycloak(data.email, data.password)
  except KeycloakAuthenticationError as exc:
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc))

  user = crud_user.get_user_by_email(db, email=data.email)
  return AccessTokenResponse(
    access_token=tokens['access_token'],
    expires_in=tokens.get('expires_in'),
    refresh_token=tokens.get('refresh_token'),
    user=_user_schema(user) if user else UserSchema(email=data.email, id=0),
  )


@router.get("/me", response_model=UserSchema)
def me(current_user: User = Depends(get_current_user)):
  """Dane zalogowanego użytkownika (token Keycloak -> models.user.User)."""
  return _user_schema(current_user)
