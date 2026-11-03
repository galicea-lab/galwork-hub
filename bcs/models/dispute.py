# models/dispute.py - Dispute, Adjustment, wg ../barter.md §6.2/§8.4.
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Enum as PgEnum, ForeignKey, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base
from bcs.models.enums import DisputeStatus


class Dispute(Base):
    __tablename__ = "bcs_disputes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    obligation_id: Mapped[str] = mapped_column(String(36), ForeignKey("bcs_obligations.id"), index=True)
    # NULL = spór założony przez operatora (np. na zgłoszenie spoza
    # systemu); wypełnione = strona dokumentu (dłużnik albo wierzyciel).
    raised_by_member_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("bcs_network_members.id"), nullable=True
    )
    reason: Mapped[str] = mapped_column(String(1000))
    status: Mapped[DisputeStatus] = mapped_column(PgEnum(DisputeStatus, native_enum=False), default=DisputeStatus.OPEN)
    decision: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Adjustment(Base):
    """Korekta NASTĘPNEGO cyklu - nigdy edycja zamkniętego cyklu
    (../barter.md §8.4/§3 zasada 5)."""
    __tablename__ = "bcs_adjustments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    cycle_id: Mapped[str] = mapped_column(String(36), ForeignKey("bcs_clearing_cycles.id"), index=True)
    obligation_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("bcs_obligations.id"), nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    reason: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
