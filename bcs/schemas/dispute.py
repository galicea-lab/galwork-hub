# schemas/dispute.py - spory o dokumenty, barter.md §8.4.
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from bcs.models.enums import DisputeStatus


class DisputeCreate(BaseModel):
    reason: str = Field(min_length=10, max_length=1000)
    # strona dokumentu zgłaszająca spór; brak = spór założony przez operatora
    raised_by_member_id: str | None = None


class DisputeOutcome(str, Enum):
    """Rozstrzygnięcie: UPHELD - spór zasadny, dokument anulowany (korekta
    w kolejnym cyklu przez nową wersję dokumentu z ERP); REJECTED - spór
    oddalony, dokument wraca do nettingu."""
    UPHELD = "UPHELD"
    REJECTED = "REJECTED"


class DisputeResolve(BaseModel):
    outcome: DisputeOutcome
    decision: str = Field(min_length=3, max_length=1000)


class DisputeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    obligation_id: str
    raised_by_member_id: str | None
    reason: str
    status: DisputeStatus
    decision: str | None
    resolved_at: datetime | None
    created_at: datetime | None
