# models/settlement.py - SettlementBatch/Transfer/Mandate, wg ../barter.md
# §6.2/§10 - te tabele są rejestrem BCS; prawdziwe środki/konta/journal
# żyją w e-Talar (connector/etalar), zob. ../barter.md §5.4.
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    DateTime, Enum as PgEnum, ForeignKey, Numeric, String, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base
from bcs.models.enums import BatchStatus, MandateStatus


class SettlementBatch(Base):
    __tablename__ = "bcs_settlement_batches"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_batch_idempotency_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    network_id: Mapped[str] = mapped_column(String(36), ForeignKey("bcs_networks.id"), index=True)
    cycle_id: Mapped[str] = mapped_column(String(36), ForeignKey("bcs_clearing_cycles.id"), index=True)
    external_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(128))

    status: Mapped[BatchStatus] = mapped_column(PgEnum(BatchStatus, native_enum=False), default=BatchStatus.CREATED)
    currency: Mapped[str] = mapped_column(String(3), default="ET")
    total_amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0)
    checksum: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # id batcha po stronie e-Talar (zob. ../barter.md §12.2/§12.3) - NULL,
    # dopóki connector/etalar nie potrafi go realnie złożyć (e-Talar nie ma
    # jeszcze /v3/settlement/batches).
    provider_batch_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # lazy='select' domyślnie - wystarczające dla rozmiarów batcha w MVP
    # (../barter.md §16.5: 5-10 uczestników); przy większych wolumenach
    # (§20.11/§7.6 limit 10 000 transferów/batch) rozważyć ładowanie
    # stronicowane, nie całą kolekcję naraz.
    transfers: Mapped[list["SettlementTransfer"]] = relationship(order_by="SettlementTransfer.id")


class SettlementTransfer(Base):
    __tablename__ = "bcs_settlement_transfers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    batch_id: Mapped[str] = mapped_column(String(36), ForeignKey("bcs_settlement_batches.id"), index=True)
    from_member_id: Mapped[str] = mapped_column(String(36), ForeignKey("bcs_network_members.id"))
    to_member_id: Mapped[str] = mapped_column(String(36), ForeignKey("bcs_network_members.id"))
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[BatchStatus] = mapped_column(PgEnum(BatchStatus, native_enum=False), default=BatchStatus.CREATED)
    provider_transaction_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class SettlementMandate(Base):
    """CLEARING_DEBIT - zgoda na cykliczne obciążenie (../barter.md §10.3).
    Odpowiednik w e-Talar nie istnieje jeszcze (`charge-user` jest
    jednorazowe) - to pole/rekord czeka na API, które dziś nie ma gdzie
    wysłać."""
    __tablename__ = "bcs_settlement_mandates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    organization_id: Mapped[str] = mapped_column(String(36), ForeignKey("bcs_organizations.id"), index=True)
    network_id: Mapped[str] = mapped_column(String(36), ForeignKey("bcs_networks.id"), index=True)
    consent_type: Mapped[str] = mapped_column(String(32), default="CLEARING_DEBIT")
    max_per_cycle_et: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    max_total_et: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    valid_from: Mapped[date | None] = mapped_column(nullable=True)
    valid_to: Mapped[date | None] = mapped_column(nullable=True)
    status: Mapped[MandateStatus] = mapped_column(PgEnum(MandateStatus, native_enum=False), default=MandateStatus.PENDING)
    evidence: Mapped[str | None] = mapped_column(String(500), nullable=True)
    granted_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    granted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
