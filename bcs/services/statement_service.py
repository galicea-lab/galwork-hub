# services/statement_service.py
# Statementy - barter.md §15.1 (uczestnik) i §15.2 (sieć). Liczone na
# żądanie z pozycji netto i batcha; `hash` to SHA-256 kanonicznego JSON-a
# treści, więc ten sam stan cyklu zawsze daje ten sam hash.
# TODO(bcs): zapis niemutowalnego `Statement` (models/statement.py) po
# SETTLED, saldo otwarcia/zamknięcia z kont rozliczeniowych e-Talar (§11.8).
from __future__ import annotations

import hashlib
import json
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from bcs.models.cycle import ClearingCycle, NetPosition
from bcs.models.dispute import Dispute
from bcs.models.enums import CycleStatus
from bcs.models.member import NetworkMember
from bcs.models.network import BarterNetwork
from bcs.models.obligation import Obligation
from bcs.models.settlement import SettlementBatch
from bcs.services.cycle_service import cycle_obligations
from bcs.services.dispute_service import OPEN_STATES

NOT_NETTED = (CycleStatus.OPEN, CycleStatus.LOCKED, CycleStatus.NETTING)
ZERO = Decimal("0")


class StatementNotReady(Exception):
    """Cykl jeszcze bez nettingu - statement nie ma z czego powstać."""


def _hash(content: dict) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(content, sort_keys=True, default=str).encode()).hexdigest()


def latest_batch(db: Session, cycle_id: str) -> SettlementBatch | None:
    return db.execute(
        select(SettlementBatch).where(SettlementBatch.cycle_id == cycle_id).order_by(SettlementBatch.created_at.desc())
    ).scalars().first()


def latest_netted_cycle(db: Session, network_id: str) -> ClearingCycle | None:
    return db.execute(
        select(ClearingCycle)
        .where(ClearingCycle.network_id == network_id, ClearingCycle.status.not_in(NOT_NETTED))
        .order_by(ClearingCycle.sequence_no.desc())
    ).scalars().first()


def _open_disputes(db: Session, network_id: str, member_id: str | None = None) -> int:
    query = (
        select(Dispute)
        .join(Obligation, Obligation.id == Dispute.obligation_id)
        .where(Obligation.network_id == network_id, Dispute.status.in_(OPEN_STATES))
    )
    if member_id is not None:
        query = query.where(or_(Obligation.debtor_member_id == member_id, Obligation.creditor_member_id == member_id))
    return len(db.execute(query).scalars().all())


def member_statement(db: Session, member: NetworkMember, cycle: ClearingCycle) -> dict:
    if cycle.network_id != member.network_id:
        raise ValueError("Cykl nie należy do sieci uczestnika")
    if cycle.status in NOT_NETTED:
        raise StatementNotReady(f"Cykl #{cycle.sequence_no} w stanie {cycle.status.value} - brak nettingu")

    network = db.get(BarterNetwork, cycle.network_id)
    position = db.execute(
        select(NetPosition).where(NetPosition.cycle_id == cycle.id, NetPosition.member_id == member.id)
    ).scalars().first()
    batch = latest_batch(db, cycle.id)
    transfers = [
        t for t in (batch.transfers if batch else []) if member.id in (t.from_member_id, t.to_member_id)
    ]

    content = {
        "network_id": network.id,
        "network_code": network.code,
        "cycle_id": cycle.id,
        "cycle_sequence_no": cycle.sequence_no,
        "cycle_status": cycle.status,
        "member_id": member.id,
        "member_code": member.member_code,
        "period_start": cycle.period_start,
        "period_end": cycle.period_end,
        "receivables_et": position.receivables_et if position else ZERO,
        "payables_et": position.payables_et if position else ZERO,
        "net_et": position.net_et if position else ZERO,
        "fee_et": position.fee_et if position else ZERO,
        "settlement_et": position.settlement_et if position else ZERO,
        "transfers": transfers,
        "open_disputes": _open_disputes(db, network.id, member.id),
    }
    hashed = {
        **content,
        "cycle_status": cycle.status.value,
        "transfers": [
            {"id": t.id, "from": t.from_member_id, "to": t.to_member_id, "amount": str(t.amount)} for t in transfers
        ],
    }
    return {**content, "hash": _hash(hashed)}


def cycle_statement(db: Session, cycle: ClearingCycle) -> dict:
    if cycle.status in NOT_NETTED:
        raise StatementNotReady(f"Cykl #{cycle.sequence_no} w stanie {cycle.status.value} - brak nettingu")

    network = db.get(BarterNetwork, cycle.network_id)
    positions = db.execute(select(NetPosition).where(NetPosition.cycle_id == cycle.id)).scalars().all()
    batch = latest_batch(db, cycle.id)
    gross = cycle.gross_total_et or ZERO
    net = cycle.net_total_et or ZERO
    savings = ((gross - net) * 100 / gross).quantize(Decimal("0.01")) if gross else None

    content = {
        "network_id": network.id,
        "network_code": network.code,
        "cycle_id": cycle.id,
        "cycle_sequence_no": cycle.sequence_no,
        "cycle_status": cycle.status,
        "period_start": cycle.period_start,
        "period_end": cycle.period_end,
        "members_count": len(positions),
        "obligations_count": len(cycle_obligations(db, cycle)),
        "gross_total_et": gross,
        "net_total_et": net,
        "netting_savings_pct": savings,
        "fees_total_et": sum((p.fee_et for p in positions), ZERO),
        "transfers_count": len(batch.transfers) if batch else 0,
        "open_disputes": _open_disputes(db, network.id),
    }
    return {**content, "hash": _hash({**content, "cycle_status": cycle.status.value})}
