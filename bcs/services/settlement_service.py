# services/settlement_service.py
# Budowa i (przyszłe) złożenie batcha settlementowego - ../barter.md §10.4.
#
# TODO(bcs): `submit_batch_to_etalar` woła connector/etalar, które dla
# /v3/settlement/* dziś tylko podnosi ConnectorError (e-Talar nie ma tych
# endpointów - zob. connector/etalar/etalar.py). Do Phase 1 (pilot ręczny,
# ../barter.md §16.2) batch zostaje w statusie CREATED/VALIDATED, a
# rozliczenie robi człowiek poza tym serwisem (przelewy ręczne / dzisiejsze
# M2M API) - ten serwis tylko PRZYGOTOWUJE dane do tego kroku.
from __future__ import annotations

import hashlib
import json
import uuid
from decimal import Decimal

from sqlalchemy.orm import Session

from bcs.calculators.netting import settle_transfers
from connector.etalar.etalar import get_connector
from connector.base import ConnectorError
from bcs.models.cycle import ClearingCycle, NetPosition
from bcs.models.enums import BatchStatus, CycleStatus
from bcs.models.settlement import SettlementBatch, SettlementTransfer


def build_settlement_batch(db: Session, cycle: ClearingCycle, positions: list[NetPosition]) -> SettlementBatch:
    if cycle.status != CycleStatus.PENDING_SETTLEMENT:
        raise ValueError(f"Cykl w stanie {cycle.status}, nie PENDING_SETTLEMENT")

    position_map = {p.member_id: p.settlement_et for p in positions}
    transfers = settle_transfers(position_map)

    checksum = hashlib.sha256(
        json.dumps(
            [{"from": t.from_member_id, "to": t.to_member_id, "amount": str(t.amount)} for t in transfers],
            sort_keys=True,
        ).encode()
    ).hexdigest()

    batch = SettlementBatch(
        network_id=cycle.network_id,
        cycle_id=cycle.id,
        idempotency_key=str(uuid.uuid4()),
        status=BatchStatus.CREATED,
        currency="ET",
        total_amount=sum((t.amount for t in transfers), Decimal("0")),
        checksum=checksum,
    )
    db.add(batch)
    db.flush()

    for t in transfers:
        db.add(SettlementTransfer(
            batch_id=batch.id,
            from_member_id=t.from_member_id,
            to_member_id=t.to_member_id,
            amount=t.amount,
            status=BatchStatus.CREATED,
        ))

    cycle.status = CycleStatus.SETTLING
    db.commit()
    db.refresh(batch)
    return batch


def submit_batch_to_etalar(batch: SettlementBatch, transfers: list[SettlementTransfer]) -> dict:
    """Próba złożenia batcha w e-Talar (§12.2/§12.3) - dziś zawsze kończy
    się ConnectorError, dopóki e-Talar nie ma /v3/settlement/batches (zob.
    connector/etalar/etalar.py). Wołający (routers/settlement.py) łapie
    ConnectorError i zwraca 503, NIE 500 - to jest oczekiwany, znany stan
    tego szkicu, nie awaria."""
    connector = get_connector()
    try:
        return connector.create_settlement_batch(
            network_id=batch.network_id,
            cycle_id=batch.cycle_id,
            external_batch_id=batch.id,
            checksum=batch.checksum or "",
            transfers=[
                {"fromMemberId": t.from_member_id, "toMemberId": t.to_member_id, "amount": str(t.amount)}
                for t in transfers
            ],
            idempotency_key=batch.idempotency_key,
        )
    except ConnectorError:
        raise
