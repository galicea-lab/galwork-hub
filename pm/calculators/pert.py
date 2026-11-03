"""Kalkulator metody PERT (estymacja probabilistyczna czasow).

Port `formularze/skrypty/pert.py` (formularz 09 — Harmonogram PERT).

Wzory (rozdz. 3.6):
    t_e   = (O + 4M + P) / 6
    var   = ((P - O) / 6) ** 2
    Z     = (T_S - T_CP) / sigma_CP
    P(T<=T_S) = Phi(Z)  -> dystrybuanta rozkladu normalnego
"""
from __future__ import annotations

from dataclasses import dataclass
from math import erfc, sqrt


@dataclass
class Zadanie:
    nazwa: str
    O: float
    M: float
    P: float

    @property
    def te(self) -> float:
        return (self.O + 4 * self.M + self.P) / 6.0

    @property
    def var(self) -> float:
        return ((self.P - self.O) / 6.0) ** 2


def phi(z: float) -> float:
    """Dystrybuanta standardowego rozkladu normalnego N(0,1)."""
    return 0.5 * erfc(-z / sqrt(2.0))


def compute_pert(zadania: list[Zadanie], T_s: float) -> dict:
    t_cp = sum(z.te for z in zadania)
    sigma_cp = sqrt(sum(z.var for z in zadania))
    Z = (T_s - t_cp) / sigma_cp if sigma_cp > 0 else float("inf")
    return {
        "tasks": [
            {"nazwa": z.nazwa, "O": z.O, "M": z.M, "P": z.P, "te": z.te, "var": z.var}
            for z in zadania
        ],
        "T_CP": t_cp,
        "sigma_CP": sigma_cp,
        "T_s": T_s,
        "Z": Z,
        "P": phi(Z),
    }


def target_date_for_confidence(t_cp: float, sigma_cp: float, confidence: float) -> float:
    """Odwrotnosc: jaki T_s daje zadany poziom ufnosci (przyblizenie Z przez tablice/erfinv)."""
    from statistics import NormalDist

    z = NormalDist().inv_cdf(confidence)
    return t_cp + z * sigma_cp
