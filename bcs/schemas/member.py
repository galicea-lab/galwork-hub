from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from bcs.models.enums import KybStatus, MemberRole, MemberStatus, OrganizationStatus


class OrganizationCreate(BaseModel):
    legal_name: str
    tax_id: str | None = None
    country: str | None = None
    legal_form: str | None = None


class OrganizationUpdate(BaseModel):
    """Częściowa aktualizacja - m.in. wynik weryfikacji KYB (§13.4), od
    którego zależy aktywacja członkostwa."""
    legal_name: str | None = None
    tax_id: str | None = None
    country: str | None = None
    legal_form: str | None = None
    kyb_status: KybStatus | None = None
    status: OrganizationStatus | None = None
    et_org_id: str | None = None


class OrganizationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    legal_name: str
    tax_id: str | None
    country: str | None
    legal_form: str | None = None
    kyb_status: KybStatus
    status: OrganizationStatus | None = None
    et_org_id: str | None = None


class NetworkMemberCreate(BaseModel):
    organization_id: str
    member_code: str
    role: MemberRole = MemberRole.BOTH
    credit_limit_et: Decimal = Decimal("0")


class NetworkMemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    network_id: str
    organization_id: str
    member_code: str
    role: MemberRole
    status: MemberStatus
    credit_limit_et: Decimal
    settlement_account_id: str | None = None
    onboarded_at: date | None = None


class NetworkMemberUpdate(BaseModel):
    role: MemberRole | None = None
    credit_limit_et: Decimal | None = None
    settlement_account_id: str | None = None


class MemberUserCreate(BaseModel):
    user_sub: str
    username: str | None = None


class MemberUserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    member_id: str
    user_sub: str
    username: str | None
    created_at: datetime | None = None
