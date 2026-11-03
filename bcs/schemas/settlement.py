from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from bcs.models.enums import BatchStatus


class SettlementTransferOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    from_member_id: str
    to_member_id: str
    amount: Decimal
    reference: str | None
    status: BatchStatus


class SettlementBatchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    network_id: str
    cycle_id: str
    idempotency_key: str
    status: BatchStatus
    currency: str
    total_amount: Decimal
    provider_batch_id: str | None
    created_at: datetime
    settled_at: datetime | None
    transfers: list[SettlementTransferOut] = []
