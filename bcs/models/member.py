# models/member.py - Organization, NetworkMember, wg ../barter.md §6.3.
#
# Organization jest tu, w BCS, bo ani galwork-hub, ani e-Talar nie mają dziś
# modelu podmiotu prawnego (ten sam brak P0 w obu - zob. ../galwork_bcs.md
# rozdz. 4 pkt 1 i ../barter.md §2.2 pkt 1). BCS staje się źródłem prawdy
# dla organizacji uczestniczących w barterze; `et_org_id` to pole do
# powiązania z odpowiednikiem w e-Talar, kiedy ten go dostanie.
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import DateTime, Enum as PgEnum, ForeignKey, Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base
from bcs.models.enums import KybStatus, MemberRole, MemberStatus, OrganizationStatus


class Organization(Base):
    __tablename__ = "bcs_organizations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    legal_name: Mapped[str] = mapped_column(String(255))
    tax_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    country: Mapped[str | None] = mapped_column(String(2), nullable=True)
    legal_form: Mapped[str | None] = mapped_column(String(100), nullable=True)
    kyb_status: Mapped[KybStatus] = mapped_column(PgEnum(KybStatus, native_enum=False), default=KybStatus.NOT_STARTED)
    status: Mapped[OrganizationStatus] = mapped_column(
        PgEnum(OrganizationStatus, native_enum=False), default=OrganizationStatus.ACTIVE
    )
    # odpowiednik w e-Talar (Organization.et_org_id po stronie e-Talar,
    # jeśli/kiedy zaimplementowane - zob. ../barter.md §6.3).
    et_org_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class NetworkMember(Base):
    __tablename__ = "bcs_network_members"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    network_id: Mapped[str] = mapped_column(String(36), ForeignKey("bcs_networks.id"), index=True)
    organization_id: Mapped[str] = mapped_column(String(36), ForeignKey("bcs_organizations.id"), index=True)
    member_code: Mapped[str] = mapped_column(String(40))
    role: Mapped[MemberRole] = mapped_column(PgEnum(MemberRole, native_enum=False), default=MemberRole.BOTH)
    status: Mapped[MemberStatus] = mapped_column(PgEnum(MemberStatus, native_enum=False), default=MemberStatus.INVITED)

    # Decimal(18,4), NIE Float - zob. ../barter.md §6.4 i ../galwork_bcs.md
    # rozdz. 4 pkt 5 (galwork-hub/models/application.py używa Float dla
    # kwot - błąd świadomie NIE powielany tutaj).
    credit_limit_et: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0)

    # Konto rozliczeniowe w e-Talar (dopóki e-Talar nie ma SettlementAccount,
    # to pole jest tylko referencją tekstową - zob. ../barter.md §10.1).
    settlement_account_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    mandate_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("bcs_settlement_mandates.id"), nullable=True)

    onboarded_at: Mapped[date | None] = mapped_column(nullable=True)

    organization: Mapped["Organization"] = relationship()


class MemberUser(Base):
    """Powiązanie konta Keycloak (`sub` z tokenu) z uczestnikiem sieci -
    kto może działać w imieniu firmy w portalu uczestnika
    (GET /api/me/memberships). BCS nie ma własnej tabeli użytkowników
    (zob. api/security.py), więc trzymamy tu tylko `sub` i - pomocniczo,
    do wyświetlania - `username` z chwili nadania dostępu."""
    __tablename__ = "bcs_member_users"
    __table_args__ = (
        UniqueConstraint("member_id", "user_sub", name="uq_member_user"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    member_id: Mapped[str] = mapped_column(String(36), ForeignKey("bcs_network_members.id"), index=True)
    user_sub: Mapped[str] = mapped_column(String(255), index=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
