# models/enums.py
# Enumy wg ../barter.md rozdz. 6 (model danych) i §8.3 (stan cyklu).
import enum


class NetworkStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    PILOT = "PILOT"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    CLOSED = "CLOSED"


class KybStatus(str, enum.Enum):
    NOT_STARTED = "NOT_STARTED"
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class OrganizationStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    BLOCKED = "BLOCKED"


class MemberRole(str, enum.Enum):
    BUYER = "BUYER"
    SELLER = "SELLER"
    BOTH = "BOTH"


class MemberStatus(str, enum.Enum):
    INVITED = "INVITED"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    LEFT = "LEFT"


class SourceSystemType(str, enum.Enum):
    REST_PUSH = "REST_PUSH"
    REST_PULL = "REST_PULL"
    CSV = "CSV"
    SFTP = "SFTP"
    DB_VIEW = "DB_VIEW"


class SourceSystemStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    ERROR = "ERROR"


class DocumentType(str, enum.Enum):
    INVOICE = "INVOICE"
    NOTE = "NOTE"
    ORDER = "ORDER"
    CONTRACT = "CONTRACT"


class ObligationStatus(str, enum.Enum):
    RECEIVED = "RECEIVED"
    VALIDATED = "VALIDATED"
    DISPUTED = "DISPUTED"
    ELIGIBLE = "ELIGIBLE"
    EXCLUDED = "EXCLUDED"
    SETTLED = "SETTLED"
    CANCELLED = "CANCELLED"


class CycleStatus(str, enum.Enum):
    """§8.3: OPEN -> LOCKED -> NETTING -> PENDING_SETTLEMENT -> SETTLING ->
    SETTLED (albo PARTIALLY_SETTLED / FAILED)."""
    OPEN = "OPEN"
    LOCKED = "LOCKED"
    NETTING = "NETTING"
    PENDING_SETTLEMENT = "PENDING_SETTLEMENT"
    SETTLING = "SETTLING"
    SETTLED = "SETTLED"
    PARTIALLY_SETTLED = "PARTIALLY_SETTLED"
    FAILED = "FAILED"


class NetPositionStatus(str, enum.Enum):
    CALCULATED = "CALCULATED"
    RESERVED = "RESERVED"
    SETTLED = "SETTLED"
    FAILED = "FAILED"
    CARRIED_FORWARD = "CARRIED_FORWARD"


class BatchStatus(str, enum.Enum):
    CREATED = "CREATED"
    VALIDATED = "VALIDATED"
    RESERVED = "RESERVED"
    COMMITTED = "COMMITTED"
    SETTLED = "SETTLED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class MandateStatus(str, enum.Enum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"


class DisputeStatus(str, enum.Enum):
    OPEN = "OPEN"
    UNDER_REVIEW = "UNDER_REVIEW"
    RESOLVED = "RESOLVED"
    REJECTED = "REJECTED"


class ReconciliationResult(str, enum.Enum):
    MATCHED = "MATCHED"
    DIFF = "DIFF"
