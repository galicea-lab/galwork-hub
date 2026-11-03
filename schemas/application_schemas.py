# schemas/application_schemas.py
import datetime
from typing import List, Optional

from pydantic import Field

from models.application import ApplicationStatus
from schemas.camel_model import CamelModel


class ApplicationCreate(CamelModel):
    name: str = Field(min_length=1, max_length=150)
    description: Optional[str] = None
    redirect_uris: List[str] = Field(default_factory=list)
    issue_limit: float = Field(ge=0)


class ApplicationUpdate(CamelModel):
    description: Optional[str] = None
    redirect_uris: Optional[List[str]] = None
    webhook_url: Optional[str] = None


class Application(CamelModel):
    id: int
    owner_id: int
    name: str
    description: Optional[str] = None
    keycloak_client_id: str
    redirect_uris: List[str]
    webhook_url: Optional[str] = None
    issue_limit: float
    issued_total: float
    status: ApplicationStatus
    created_at: datetime.datetime


class ApplicationWithSecret(Application):
    """Zwracane wyłącznie raz - bezpośrednio po rejestracji lub rotacji sekretu."""
    client_secret: str
