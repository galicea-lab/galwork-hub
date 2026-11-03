# services/dispute_service.py
# Spory o dokumenty - barter.md §8.4: spór nie blokuje cyklu, dokument jest
# wyłączany z nettingu (status DISPUTED nie jest brany przez
# cycle_service.compute_netting), po rozstrzygnięciu wraca albo jest anulowany.
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from bcs.models.dispute import Dispute
from bcs.models.enums import DisputeStatus, ObligationStatus
from bcs.models.obligation import Obligation
from bcs.schemas.dispute import DisputeCreate, DisputeOutcome, DisputeResolve

DISPUTABLE = (ObligationStatus.RECEIVED, ObligationStatus.VALIDATED, ObligationStatus.ELIGIBLE)
OPEN_STATES = (DisputeStatus.OPEN, DisputeStatus.UNDER_REVIEW)


class DisputeStateError(Exception):
    """Spór niedozwolony w obecnym stanie dokumentu/sporu."""


def open_dispute(db: Session, obligation: Obligation, payload: DisputeCreate) -> Dispute:
    if obligation.status not in DISPUTABLE:
        raise DisputeStateError(f"Dokument w stanie {obligation.status.value} nie może być przedmiotem sporu")
    if payload.raised_by_member_id is not None and payload.raised_by_member_id not in (
        obligation.debtor_member_id,
        obligation.creditor_member_id,
    ):
        raise DisputeStateError("Spór może zgłosić tylko strona dokumentu (dłużnik albo wierzyciel)")
    existing = db.execute(
        select(Dispute).where(Dispute.obligation_id == obligation.id, Dispute.status.in_(OPEN_STATES))
    ).scalars().first()
    if existing is not None:
        raise DisputeStateError("Dla tego dokumentu jest już otwarty spór")

    dispute = Dispute(
        obligation_id=obligation.id,
        raised_by_member_id=payload.raised_by_member_id,
        reason=payload.reason,
        status=DisputeStatus.OPEN,
    )
    obligation.status = ObligationStatus.DISPUTED
    db.add(dispute)
    db.commit()
    db.refresh(dispute)
    return dispute


def start_review(db: Session, dispute: Dispute) -> Dispute:
    if dispute.status != DisputeStatus.OPEN:
        raise DisputeStateError(f"Spór w stanie {dispute.status.value}, nie OPEN")
    dispute.status = DisputeStatus.UNDER_REVIEW
    db.commit()
    db.refresh(dispute)
    return dispute


def resolve_dispute(db: Session, dispute: Dispute, payload: DisputeResolve) -> Dispute:
    """UPHELD -> dokument CANCELLED (korekta = nowa wersja z ERP w kolejnym
    cyklu), REJECTED -> dokument wraca do VALIDATED i do nettingu.
    TODO(bcs): natychmiastowy Adjustment (§8.4) i four-eyes (§14.2)."""
    if dispute.status not in OPEN_STATES:
        raise DisputeStateError(f"Spór w stanie {dispute.status.value} jest już rozstrzygnięty")
    obligation = db.get(Obligation, dispute.obligation_id)
    if payload.outcome == DisputeOutcome.UPHELD:
        dispute.status = DisputeStatus.RESOLVED
        obligation.status = ObligationStatus.CANCELLED
    else:
        dispute.status = DisputeStatus.REJECTED
        obligation.status = ObligationStatus.VALIDATED
    dispute.decision = payload.decision
    dispute.resolved_at = datetime.utcnow()
    db.commit()
    db.refresh(dispute)
    return dispute
