# schemas/auth_schemas.py
from typing import List

from schemas.camel_model import CamelModel
from schemas.user_schemas import UserSchema

class Token(CamelModel):
  username: str
  email : str
  message : str
  scope : str
  roles: List[str]


class RegisterRequest(CamelModel):
    email: str
    password: str
    name: str | None = None


class LoginRequest(CamelModel):
    email: str
    password: str


class AccessTokenResponse(CamelModel):
    access_token: str
    token_type: str = "bearer"
    # 2026-10-05: pola przekazywane przez Keycloak przy grant_type=password
    # (wcześniejsza wersja wystawiała własny JWT i nic takiego nie zwracała).
    expires_in: int | None = None
    refresh_token: str | None = None
    user: UserSchema

