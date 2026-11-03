# schemas/user_schemas.py
from pydantic import BaseModel, EmailStr, ConfigDict
from .camel_model import CamelModel

class UserBase(CamelModel):
    email: EmailStr

class UserCreate(UserBase):
    pass # No password directly here if Keycloak handles it

class UserSchema(UserBase):
    id: int
    id_from_keycloak: str | None = None

