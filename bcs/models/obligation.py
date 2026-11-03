# models/obligation.py - Obligation, wg ../barter.md §6.2.
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    DateTime, Enum as PgEnum, ForeignKey, Integer, Numeric, String,
    UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base
from bcs.models.enums import DocumentType, ObligationStatus


class Obligation(Base):
    __tablename__ = "bcs_obligations"
    __table_args__ = (
        # external_id + source_system jest unikalne (../barter.md §6.2
        # "Zasady"); korekta tworzy nową WERSJĘ tego samego external_id,
        # nie nowy wiersz - obsłuży to services/obligation_service.py.
        UniqueConstraint("source_system_id", "external_id", name="uq_obligation_source_external"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    network_id: Mapped[str] = mapped_column(String(36), ForeignKey("bcs_networks.id"), index=True)
    debtor_member_id: Mapped[str] = mapped_column(String(36), ForeignKey("bcs_network_members.id"), index=True)
    creditor_member_id: Mapped[str] = mapped_column(String(36), ForeignKey("bcs_network_members.id"), index=True)
    source_system_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("bcs_source_systems.id"), nullable=True)

    external_id: Mapped[str] = mapped_column(String(128))
    document_type: Mapped[DocumentType] = mapped_column(PgEnum(DocumentType, native_enum=False))
    document_number: Mapped[str] = mapped_column(String(128))
    issue_date: Mapped[date] = mapped_column()
    due_date: Mapped[date | None] = mapped_column(nullable=True)

    # Kwota w walucie źródłowej + przeliczenie do ET - zob. ../barter.md
    # §6.4: Numeric(18,4) wszędzie, nigdy Float.
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    currency: Mapped[str] = mapped_column(String(3), default="PLN")
    fx_rate: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    amount_et: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    tax_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)

    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[ObligationStatus] = mapped_column(
        PgEnum(ObligationStatus, native_enum=False), default=ObligationStatus.RECEIVED
    )
    version: Mapped[int] = mapped_column(Integer, default=1)
    document_hash: Mapped[str] = mapped_column(String(128))

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
