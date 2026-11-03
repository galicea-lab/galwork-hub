from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ProjectCreate(BaseModel):
    name: str
    code: str | None = None
    phase: str | None = None
    methodology: str | None = None


class ProjectUpdate(BaseModel):
    name: str | None = None
    code: str | None = None
    phase: str | None = None
    methodology: str | None = None


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    code: str | None
    phase: str | None
    methodology: str | None
    created_at: datetime
    updated_at: datetime
