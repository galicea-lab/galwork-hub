# schemas/obligation.py - kontrakt importu zgodny z ../barter.md §7.2/§7.3.
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from bcs.models.enums import DocumentType, ObligationStatus


class ObligationIn(BaseModel):
    """Jedna pozycja z `POST /obligations/batch` - pola 1:1 z §18.2."""
    external_id: str
    debtor_member_code: str
    creditor_member_code: str
    document_type: DocumentType
    document_number: str
    issue_date: date
    due_date: date | None = None
    amount: Decimal
    currency: str = "ET"
    fx_rate: Decimal | None = None
    tax_amount: Decimal | None = None
    description: str | None = None
    version: int = 1
    document_hash: str


class ObligationBatchIn(BaseModel):
    source_system_id: str
    batch_id: str
    obligations: list[ObligationIn]


class ObligationBatchResult(BaseModel):
    """Odpowiedź w kształcie §7.3."""
    batch_id: str
    received: int
    accepted: int
    rejected: int
    obligation_ids: list[str]
    rejections: list[dict] = []
    status: str = "RECEIVED"


class ObligationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    network_id: str
    debtor_member_id: str
    creditor_member_id: str
    external_id: str
    document_type: DocumentType
    document_number: str
    amount: Decimal
    currency: str
    amount_et: Decimal
    status: ObligationStatus
    version: int
    source_system_id: str | None = None
    issue_date: date | None = None
    due_date: date | None = None
    description: str | None = None
