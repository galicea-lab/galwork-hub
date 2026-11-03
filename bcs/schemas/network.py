from datetime import datetime

from pydantic import BaseModel, ConfigDict

from bcs.models.enums import NetworkStatus


class NetworkCreate(BaseModel):
    code: str
    name: str
    unit: str = "ET"
    timezone: str = "UTC"
    cycle_policy: dict = {}
    netting_policy: dict = {}
    fee_policy: dict = {}
    sponsor_org_id: str | None = None
    operator_org_id: str | None = None


class NetworkUpdate(BaseModel):
    """Częściowa aktualizacja konfiguracji/statusu sieci. Zmiana polityk
    nie działa wstecz na zamknięte cykle (snapshot w ClearingCycle)."""
    name: str | None = None
    timezone: str | None = None
    status: NetworkStatus | None = None
    cycle_policy: dict | None = None
    netting_policy: dict | None = None
    fee_policy: dict | None = None
    sponsor_org_id: str | None = None
    operator_org_id: str | None = None


class NetworkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    code: str
    name: str
    unit: str
    timezone: str
    status: NetworkStatus
    sponsor_org_id: str | None
    operator_org_id: str | None
    cycle_policy: dict = {}
    netting_policy: dict = {}
    fee_policy: dict = {}
    created_at: datetime
