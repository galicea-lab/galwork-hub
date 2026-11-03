# models/audit.py - AuditEvent, wg ../barter.md §11.6/§14.2.
#
# Szkielet append-only: hash_n = SHA256(hash_{n-1} || entry). Łańcuch jest
# per encja (entity_type+entity_id), nie globalny - wystarczające dla audytu
# pojedynczego zobowiązania/cyklu/batcha; TODO(bcs): anchor okresowy
# (np. publikacja hasha najnowszego eventu poza bazę) jeśli ma to chronić
# też przed modyfikacją samej bazy, nie tylko przed błędem aplikacji.
import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


class AuditEvent(Base):
    __tablename__ = "bcs_audit_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    entity_type: Mapped[str] = mapped_column(String(64), index=True)
    entity_id: Mapped[str] = mapped_column(String(36), index=True)
    action: Mapped[str] = mapped_column(String(64))
    # `sub`/`preferred_username` z tokenu Keycloak (api/security.py), NIE FK
    # - BCS nie ma lokalnej tabeli użytkowników.
    actor: Mapped[str] = mapped_column(String(255))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    prev_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    hash: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
