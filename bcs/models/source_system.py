# models/source_system.py - SourceSystem, wg ../barter.md §6.2/§7.1.
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum as PgEnum, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base
from bcs.models.enums import SourceSystemStatus, SourceSystemType


class SourceSystem(Base):
    __tablename__ = "bcs_source_systems"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    member_id: Mapped[str] = mapped_column(String(36), ForeignKey("bcs_network_members.id"), index=True)
    type: Mapped[SourceSystemType] = mapped_column(PgEnum(SourceSystemType, native_enum=False))
    endpoint: Mapped[str | None] = mapped_column(String(500), nullable=True)
    auth_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Referencja do sejfu (Vault/secret manager), NIGDY sekret w bazie wprost
    # - zob. ../barter.md §7.1 i wzorzec `secret/` z galwork-hub.
    credentials_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    schema_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    cursor: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[SourceSystemStatus] = mapped_column(
        PgEnum(SourceSystemStatus, native_enum=False), default=SourceSystemStatus.ACTIVE
    )
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
