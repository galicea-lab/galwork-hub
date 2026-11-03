# models/statement.py - Statement, ReconciliationRun, wg ../barter.md
# §6.2/§15.
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import JSON, DateTime, Enum as PgEnum, ForeignKey, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base
from bcs.models.enums import ReconciliationResult


class Statement(Base):
    """Niemutowalny dokument (hash) - §15.1/§15.2. `member_id` NULL =
    statement sieci (zbiorczy); wypełniony = statement uczestnika."""
    __tablename__ = "bcs_statements"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    cycle_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("bcs_clearing_cycles.id"), nullable=True)
    member_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("bcs_network_members.id"), nullable=True)
    period_start: Mapped[datetime] = mapped_column(DateTime)
    period_end: Mapped[datetime] = mapped_column(DateTime)
    opening_balance: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    closing_balance: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    total_debit: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    total_credit: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    hash: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ReconciliationRun(Base):
    """Uzgodnienie trzystronne ERP <-> BCS <-> e-Talar, §7.5."""
    __tablename__ = "bcs_reconciliation_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    cycle_id: Mapped[str] = mapped_column(String(36), ForeignKey("bcs_clearing_cycles.id"), index=True)
    result: Mapped[ReconciliationResult] = mapped_column(PgEnum(ReconciliationResult, native_enum=False))
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
