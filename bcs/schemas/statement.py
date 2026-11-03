# schemas/statement.py - statementy uczestnika i sieci, barter.md §15.1/§15.2.
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel

from bcs.models.enums import CycleStatus
from bcs.schemas.settlement import SettlementTransferOut


class MemberStatementOut(BaseModel):
    network_id: str
    network_code: str
    cycle_id: str
    cycle_sequence_no: int
    cycle_status: CycleStatus
    member_id: str
    member_code: str
    period_start: datetime
    period_end: datetime
    receivables_et: Decimal
    payables_et: Decimal
    net_et: Decimal
    fee_et: Decimal
    settlement_et: Decimal
    transfers: list[SettlementTransferOut]
    open_disputes: int
    # saldo konta rozliczeniowego - dostępne dopiero z e-Talar (§11.8)
    opening_balance: Decimal | None = None
    closing_balance: Decimal | None = None
    hash: str


class CycleStatementOut(BaseModel):
    network_id: str
    network_code: str
    cycle_id: str
    cycle_sequence_no: int
    cycle_status: CycleStatus
    period_start: datetime
    period_end: datetime
    members_count: int
    obligations_count: int
    gross_total_et: Decimal
    net_total_et: Decimal
    netting_savings_pct: Decimal | None
    fees_total_et: Decimal
    transfers_count: int
    open_disputes: int
    hash: str
