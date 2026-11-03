# services/obligation_service.py
# Import zobowiązań - kontrakt i walidacje z ../barter.md §7.3/§7.4.
# TODO(bcs): tylko podzbiór walidacji z §7.4 (1,2,3,4,6); brakuje m.in.
# sprawdzenia cutoffu (5), zgodności z ERP (7), limitu/koncentracji (8),
# sanctions/AML (9) - to świadomie poza szkicem (wymagają modeli
# cyklu/limitów/AML jeszcze niedopracowanych).
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from bcs.models.member import NetworkMember
from bcs.models.obligation import Obligation
from bcs.models.enums import ObligationStatus
from bcs.schemas.obligation import ObligationBatchIn, ObligationBatchResult, ObligationIn


class ObligationRejected(Exception):
    def __init__(self, external_id: str, code: str, detail: str):
        self.external_id = external_id
        self.code = code
        self.detail = detail
        super().__init__(detail)


def _member_by_code(db: Session, network_id: str, code: str) -> NetworkMember | None:
    return db.execute(
        select(NetworkMember).where(
            NetworkMember.network_id == network_id,
            NetworkMember.member_code == code,
        )
    ).scalars().first()


def _validate_and_build(db: Session, network_id: str, source_system_id: str, item: ObligationIn) -> Obligation:
    # 2. członkostwo w sieci
    debtor = _member_by_code(db, network_id, item.debtor_member_code)
    creditor = _member_by_code(db, network_id, item.creditor_member_code)
    if debtor is None:
        raise ObligationRejected(item.external_id, "UNKNOWN_DEBTOR", f"Nieznany dłużnik: {item.debtor_member_code}")
    if creditor is None:
        raise ObligationRejected(item.external_id, "UNKNOWN_CREDITOR", f"Nieznany odbiorca: {item.creditor_member_code}")

    # 4. kwota > 0
    if item.amount <= 0:
        raise ObligationRejected(item.external_id, "INVALID_AMOUNT", "Kwota musi być > 0")

    # 6. duplikat external_id w ramach source_system (constraint w DB też to
    # łapie, ale tu dajemy czytelny komunikat zamiast IntegrityError)
    existing = db.execute(
        select(Obligation).where(
            Obligation.source_system_id == source_system_id,
            Obligation.external_id == item.external_id,
        )
    ).scalars().first()
    if existing is not None:
        raise ObligationRejected(item.external_id, "DUPLICATE", "external_id już istnieje dla tego source_system")

    # Przeliczenie do ET - jeśli waluta already ET, fx_rate=1; inaczej
    # wymagany kurs (§6.4: brak kursu = dokument wykluczony z cyklu - tu
    # odrzucamy już na imporcie, jawnie, zamiast cichego wykluczenia później).
    if item.currency == "ET":
        amount_et = item.amount
    elif item.fx_rate is not None:
        amount_et = (item.amount * item.fx_rate).quantize(Decimal("0.0001"))
    else:
        raise ObligationRejected(item.external_id, "MISSING_FX_RATE", "Brak kursu do ET dla waluty innej niż ET")

    return Obligation(
        network_id=network_id,
        debtor_member_id=debtor.id,
        creditor_member_id=creditor.id,
        source_system_id=source_system_id,
        external_id=item.external_id,
        document_type=item.document_type,
        document_number=item.document_number,
        issue_date=item.issue_date,
        due_date=item.due_date,
        amount=item.amount,
        currency=item.currency,
        fx_rate=item.fx_rate,
        amount_et=amount_et,
        tax_amount=item.tax_amount,
        description=item.description,
        status=ObligationStatus.VALIDATED,
        version=item.version,
        document_hash=item.document_hash,
    )


def import_obligations(db: Session, network_id: str, payload: ObligationBatchIn) -> ObligationBatchResult:
    """Odpowiedź w kształcie §7.3 - `accepted`/`rejected` per pozycja,
    import jest best-effort (jedna błędna pozycja nie blokuje reszty
    batcha), inaczej niż SettlementBatch, który jest all-or-nothing."""
    obligation_ids: list[str] = []
    rejections: list[dict] = []

    for item in payload.obligations:
        try:
            obligation = _validate_and_build(db, network_id, payload.source_system_id, item)
        except ObligationRejected as exc:
            rejections.append({"external_id": exc.external_id, "code": exc.code, "detail": exc.detail})
            continue
        db.add(obligation)
        db.flush()
        obligation_ids.append(obligation.id)

    db.commit()

    return ObligationBatchResult(
        batch_id=payload.batch_id,
        received=len(payload.obligations),
        accepted=len(obligation_ids),
        rejected=len(rejections),
        obligation_ids=obligation_ids,
        rejections=rejections,
        status="RECEIVED",
    )
