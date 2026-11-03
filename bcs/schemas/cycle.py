from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from bcs.models.enums import CycleStatus, NetPositionStatus


class CycleCreate(BaseModel):
    period_start: datetime
    period_end: datetime
    cutoff_at: datetime | None = None
    dispute_window_end: datetime | None = None


class CycleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    network_id: str
    sequence_no: int
    period_start: datetime
    period_end: datetime
    cutoff_at: datetime | None = None
    dispute_window_end: datetime | None = None
    status: CycleStatus
    checksum: str | None = None
    gross_total_et: Decimal | None
    net_total_et: Decimal | None
    locked_at: datetime | None
    settled_at: datetime | None


class NetPositionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    cycle_id: str
    member_id: str
    receivables_et: Decimal
    payables_et: Decimal
    net_et: Decimal
    fee_et: Decimal
    settlement_et: Decimal
    status: NetPositionStatus
