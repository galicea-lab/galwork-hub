# tests/test_netting.py
# Przykład z ../barter.md §9.2 jako test regresyjny silnika nettingu.
from decimal import Decimal

from bcs.calculators.netting import ObligationLeg, net_positions, positions_balance, settle_transfers


def _leg(debtor: str, creditor: str, amount: str) -> ObligationLeg:
    return ObligationLeg(debtor_member_id=debtor, creditor_member_id=creditor, amount_et=Decimal(amount))


def test_net_positions_example_from_barter_md():
    obligations = [
        _leg("A", "B", "100"),
        _leg("B", "C", "80"),
        _leg("C", "A", "40"),
        _leg("D", "A", "30"),
        _leg("A", "D", "10"),
    ]

    positions = net_positions(obligations)

    assert positions["A"] == Decimal("-40")
    assert positions["B"] == Decimal("20")
    assert positions["C"] == Decimal("40")
    assert positions["D"] == Decimal("-20")
    assert positions_balance(positions) == Decimal("0")


def test_settle_transfers_minimizes_to_two_transfers():
    positions = {
        "A": Decimal("-40"),
        "B": Decimal("20"),
        "C": Decimal("40"),
        "D": Decimal("-20"),
    }

    transfers = settle_transfers(positions)

    total = sum((t.amount for t in transfers), Decimal("0"))
    assert total == Decimal("60")  # gross był 260 - redukcja 77% (§9.2)
    assert len(transfers) == 2

    by_pair = {(t.from_member_id, t.to_member_id): t.amount for t in transfers}
    assert by_pair[("A", "C")] == Decimal("40")
    assert by_pair[("D", "B")] == Decimal("20")


def test_settle_transfers_ignores_dust_below_min_transfer():
    positions = {"A": Decimal("-0.005"), "B": Decimal("0.005")}
    transfers = settle_transfers(positions, min_transfer=Decimal("0.01"))
    assert transfers == []
