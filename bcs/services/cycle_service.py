# services/cycle_service.py
# Cykl życia cyklu clearingowego - ../barter.md §8.3/§9.
#
# TODO(bcs): zamknięcie cyklu, netting i budowa batcha są tu synchroniczne
# (wywołanie HTTP czeka na wynik). Dla większych sieci/wolumenów to powinno
# iść przez kolejkę zadań (RQ/Celery) - zob. ../galwork_bcs.md rozdz. 4
# pkt 2; nie jest to zadrutowane w tym szkicu.
from __future__ import annotations

import hashlib
import json
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from bcs.calculators.netting import ObligationLeg, net_positions, settle_transfers
from bcs.models.cycle import ClearingCycle, NetPosition
from bcs.models.enums import CycleStatus, NetPositionStatus, ObligationStatus
from bcs.models.obligation import Obligation
from bcs.schemas.cycle import CycleCreate


class CycleStateError(Exception):
    """Operacja niedozwolona w obecnym stanie cyklu (§8.3)."""


def open_cycle(db: Session, network_id: str, payload: CycleCreate) -> ClearingCycle:
    last_seq = db.execute(
        select(ClearingCycle.sequence_no)
        .where(ClearingCycle.network_id == network_id)
        .order_by(ClearingCycle.sequence_no.desc())
    ).scalars().first() or 0

    cycle = ClearingCycle(
        network_id=network_id,
        sequence_no=last_seq + 1,
        period_start=payload.period_start,
        period_end=payload.period_end,
        cutoff_at=payload.cutoff_at,
        dispute_window_end=payload.dispute_window_end,
        status=CycleStatus.OPEN,
    )
    db.add(cycle)
    db.commit()
    db.refresh(cycle)
    return cycle


def close_cycle(db: Session, cycle: ClearingCycle) -> ClearingCycle:
    """Cutoff: po tym momencie nie wolno dodawać dokumentów do cyklu
    (§8.3) - w tym szkicu to tylko zmiana statusu; wymuszenie "nie dodawaj
    ELIGIBLE po LOCKED" jest po stronie obligation_service (TODO(bcs):
    sprawdzać `cycle.status` przy imporcie, jeśli obligations mają być
    przypisywane do konkretnego cyklu od razu przy imporcie, a nie dobierane
    po zakresie dat przy nettingu jak tutaj)."""
    if cycle.status != CycleStatus.OPEN:
        raise CycleStateError(f"Cykl w stanie {cycle.status}, nie OPEN")
    cycle.status = CycleStatus.LOCKED
    from datetime import datetime
    cycle.locked_at = datetime.utcnow()
    db.commit()
    db.refresh(cycle)
    return cycle


def cycle_obligations(db: Session, cycle: ClearingCycle) -> list[Obligation]:
    """Zobowiązania wchodzące do nettingu cyklu: VALIDATED/ELIGIBLE (sporne
    i anulowane wypadają, §8.4) z datą wystawienia w okresie cyklu. Używane
    też przez statement sieci (services/statement_service.py)."""
    return db.execute(
        select(Obligation).where(
            Obligation.network_id == cycle.network_id,
            Obligation.status.in_([ObligationStatus.VALIDATED, ObligationStatus.ELIGIBLE]),
            Obligation.issue_date >= cycle.period_start.date(),
            Obligation.issue_date <= cycle.period_end.date(),
        )
    ).scalars().all()


def compute_netting(db: Session, cycle: ClearingCycle) -> list[NetPosition]:
    """Oblicza pozycje netto dla zobowiązań VALIDATED/ELIGIBLE w oknie
    czasowym cyklu (§9) i zapisuje je jako NetPosition. Idempotentne na
    poziomie wywołania - ponowne wywołanie dla tego samego cyklu nadpisuje
    wcześniejsze pozycje (TODO(bcs): blokada przed ponownym liczeniem po
    SETTLED, dziś tylko strażnik stanu LOCKED)."""
    if cycle.status != CycleStatus.LOCKED:
        raise CycleStateError(f"Cykl w stanie {cycle.status}, nie LOCKED")

    obligations = cycle_obligations(db, cycle)

    legs = [
        ObligationLeg(debtor_member_id=o.debtor_member_id, creditor_member_id=o.creditor_member_id, amount_et=o.amount_et)
        for o in obligations
    ]
    positions = net_positions(legs)

    # wyczyść poprzednie pozycje tego cyklu (ponowne liczenie)
    db.query(NetPosition).filter(NetPosition.cycle_id == cycle.id).delete()

    net_position_rows: list[NetPosition] = []
    gross_total = sum((leg.amount_et for leg in legs), Decimal("0"))
    for member_id, net in positions.items():
        receivables = sum((leg.amount_et for leg in legs if leg.creditor_member_id == member_id), Decimal("0"))
        payables = sum((leg.amount_et for leg in legs if leg.debtor_member_id == member_id), Decimal("0"))
        row = NetPosition(
            cycle_id=cycle.id,
            member_id=member_id,
            receivables_et=receivables,
            payables_et=payables,
            net_et=net,
            fee_et=Decimal("0"),
            settlement_et=net,
            status=NetPositionStatus.CALCULATED,
            calculation_hash=hashlib.sha256(
                json.dumps({"member": member_id, "net": str(net)}, sort_keys=True).encode()
            ).hexdigest(),
        )
        db.add(row)
        net_position_rows.append(row)

    cycle.status = CycleStatus.PENDING_SETTLEMENT
    cycle.gross_total_et = gross_total
    cycle.net_total_et = sum((abs(p) for p in positions.values()), Decimal("0")) / 2
    cycle.checksum = hashlib.sha256(
        json.dumps({m: str(v) for m, v in sorted(positions.items())}, sort_keys=True).encode()
    ).hexdigest()

    db.commit()
    for row in net_position_rows:
        db.refresh(row)
    return net_position_rows


def preview_transfers(positions: list[NetPosition]):
    """Pomocniczy podgląd minimalnego zestawu transferów (§9.3) - UŻYWANE
    przez routers/cycles.py do zwrócenia podglądu przed faktycznym
    złożeniem batcha (services/settlement_service.py)."""
    position_map = {p.member_id: p.net_et for p in positions}
    return settle_transfers(position_map)
