# models/cycle.py - ClearingCycle, NetPosition, wg ../barter.md §6.2/§8.
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import JSON, DateTime, Enum as PgEnum, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base
from bcs.models.enums import CycleStatus, NetPositionStatus


class ClearingCycle(Base):
    __tablename__ = "bcs_clearing_cycles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    network_id: Mapped[str] = mapped_column(String(36), ForeignKey("bcs_networks.id"), index=True)
    sequence_no: Mapped[int] = mapped_column(Integer)

    period_start: Mapped[datetime] = mapped_column(DateTime)
    period_end: Mapped[datetime] = mapped_column(DateTime)
    cutoff_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    dispute_window_end: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    status: Mapped[CycleStatus] = mapped_column(PgEnum(CycleStatus, native_enum=False), default=CycleStatus.OPEN)
    # Snapshot polityk sieci w chwili zamknięcia cyklu (../barter.md §6.2) -
    # zmiana polityki PO zamknięciu nie wpływa już na ten cykl.
    policy_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)

    gross_total_et: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    net_total_et: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    checksum: Mapped[str | None] = mapped_column(String(128), nullable=True)

    locked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class NetPosition(Base):
    __tablename__ = "bcs_net_positions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    cycle_id: Mapped[str] = mapped_column(String(36), ForeignKey("bcs_clearing_cycles.id"), index=True)
    member_id: Mapped[str] = mapped_column(String(36), ForeignKey("bcs_network_members.id"), index=True)

    receivables_et: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0)
    payables_et: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0)
    net_et: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0)
    fee_et: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0)
    settlement_et: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0)

    status: Mapped[NetPositionStatus] = mapped_column(
        PgEnum(NetPositionStatus, native_enum=False), default=NetPositionStatus.CALCULATED
    )
    calculation_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
