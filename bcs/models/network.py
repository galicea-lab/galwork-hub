# models/network.py - BarterNetwork, wg ../barter.md §6.2.
import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Enum as PgEnum, String, func
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base
from bcs.models.enums import NetworkStatus


class BarterNetwork(Base):
    __tablename__ = "bcs_networks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    code: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    unit: Mapped[str] = mapped_column(String(10), default="ET")
    timezone: Mapped[str] = mapped_column(String(50), default="UTC")

    # polityki jako JSON - szczegóły do ustalenia per sieć (częstotliwość
    # cyklu, próg nettingu, opłaty), zob. ../barter.md §6.2/§9.4.
    cycle_policy: Mapped[dict] = mapped_column(JSON, default=dict)
    netting_policy: Mapped[dict] = mapped_column(JSON, default=dict)
    fee_policy: Mapped[dict] = mapped_column(JSON, default=dict)

    # Referencje do Organization (nie FK na User) - sponsor i operator są
    # podmiotami prawnymi, zob. ../barter.md §4.
    sponsor_org_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    operator_org_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    status: Mapped[NetworkStatus] = mapped_column(
        PgEnum(NetworkStatus, native_enum=False), default=NetworkStatus.DRAFT
    )

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
