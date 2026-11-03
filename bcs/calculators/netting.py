# calculators/netting.py
# Silnik nettingu wielostronnego - algorytm z ../barter.md §9.1/§9.3,
# przeniesiony na Decimal (w dokumencie był szkicowy, na int/float).
#
# Czyste funkcje, bez SQLAlchemy/DB - łatwe do testowania (zob.
# tests/test_netting.py) i do podmiany na wariant min-cost/min-transfer
# bez dotykania services/cycle_service.py.
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

ROUNDING_QUANTUM = Decimal("0.0001")


@dataclass(frozen=True)
class ObligationLeg:
    """Widok zobowiązania potrzebny do nettingu - member_id debtor/creditor
    i kwota w ET. Serwis mapuje tu models.obligation.Obligation."""
    debtor_member_id: str
    creditor_member_id: str
    amount_et: Decimal


@dataclass(frozen=True)
class Transfer:
    from_member_id: str
    to_member_id: str
    amount: Decimal


def net_positions(obligations: list[ObligationLeg]) -> dict[str, Decimal]:
    """Pozycja netto per uczestnik: `receivables - payables` (§9.1).
    Suma wszystkich pozycji musi wynosić 0 - to inwariant sprawdzany przez
    wołającego (assert tutaj zatrzymałby cały proces na błędzie danych,
    co w cyklu produkcyjnym powinno być kontrolowanym błędem walidacji,
    nie AssertionError)."""
    positions: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for ob in obligations:
        positions[ob.debtor_member_id] -= ob.amount_et
        positions[ob.creditor_member_id] += ob.amount_et
    return dict(positions)


def positions_balance(positions: dict[str, Decimal]) -> Decimal:
    """Suma pozycji - powinna być 0; wołający decyduje, co zrobić z resztą
    (konto `rounding_residual`, zob. ../barter.md §6.4/§9.5)."""
    return sum(positions.values(), Decimal("0"))


def settle_transfers(
    positions: dict[str, Decimal],
    min_transfer: Decimal = Decimal("0.01"),
) -> list[Transfer]:
    """Minimalizuje liczbę transferów: zachłannie łączy największego
    dłużnika z największym wierzycielem (§9.3). Nie jest to optymalny
    algorytm min-transfer (problem pokrycia przepływem) - wystarczający dla
    MVP; podmiana na wariant optymalny nie zmienia kontraktu tej funkcji."""
    debtors = sorted(
        ((member, -amount) for member, amount in positions.items() if amount < 0),
        key=lambda item: item[1], reverse=True,
    )
    creditors = sorted(
        ((member, amount) for member, amount in positions.items() if amount > 0),
        key=lambda item: item[1], reverse=True,
    )

    transfers: list[Transfer] = []
    d_idx = c_idx = 0
    debtors = list(debtors)
    creditors = list(creditors)

    while d_idx < len(debtors) and c_idx < len(creditors):
        debtor, d_amount = debtors[d_idx]
        creditor, c_amount = creditors[c_idx]
        amount = min(d_amount, c_amount)

        if amount >= min_transfer:
            transfers.append(Transfer(from_member_id=debtor, to_member_id=creditor, amount=amount))

        d_amount -= amount
        c_amount -= amount
        debtors[d_idx] = (debtor, d_amount)
        creditors[c_idx] = (creditor, c_amount)

        if d_amount <= 0:
            d_idx += 1
        if c_amount <= 0:
            c_idx += 1

    return transfers


def apply_fees(
    positions: dict[str, Decimal],
    fee_rate: Decimal,
    *,
    base: str = "gross",
    gross_per_member: dict[str, Decimal] | None = None,
) -> dict[str, Decimal]:
    """settlement_i = net_i - fee_i (§9.1/§9.4). `base="gross"` liczy opłatę
    od obrotu brutto danego uczestnika (wymaga `gross_per_member`); inaczej
    liczy od wartości absolutnej pozycji netto. Zaokrąglenie do 4 miejsc,
    ROUND_HALF_UP - reszta, jeśli trzeba, trafia na `rounding_residual` poza
    tą funkcją (ona tylko liczy, nie księguje)."""
    fees: dict[str, Decimal] = {}
    for member, net in positions.items():
        base_amount = (gross_per_member or {}).get(member, abs(net)) if base == "gross" else abs(net)
        fee = (base_amount * fee_rate).quantize(ROUNDING_QUANTUM)
        fees[member] = fee
    return fees
