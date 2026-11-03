# schemas/source_system.py - rejestracja integracji ERP uczestnika (§7.1/§7.6).
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from bcs.models.enums import SourceSystemStatus, SourceSystemType


class SourceSystemCreate(BaseModel):
    type: SourceSystemType
    endpoint: str | None = None
    auth_type: str | None = None
    # referencja do sejfu, nigdy sam sekret (zob. models/source_system.py)
    credentials_ref: str | None = None
    schema_version: str | None = None


class NetworkSourceSystemCreate(SourceSystemCreate):
    """Wariant z barter.md §7.6 (POST /networks/{id}/source-systems) -
    uczestnik wskazany w body."""
    member_id: str


class SourceSystemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    member_id: str
    type: SourceSystemType
    endpoint: str | None
    auth_type: str | None
    credentials_ref: str | None
    schema_version: str | None
    status: SourceSystemStatus
    last_sync_at: datetime | None
