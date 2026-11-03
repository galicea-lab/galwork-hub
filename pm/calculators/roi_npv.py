"""Kalkulator wskaznikow ekonomicznych projektu: NPV, ROI, payback.

Port `formularze/skrypty/roi_npv.py` (formularz 03 — Business Case).
Wzory por. Rozdz. 4 — Ekonomia projektu.
"""
from __future__ import annotations


def npv(flows: list[float], rate: float) -> float:
    """NPV przy stalej stopie dyskonta rate (np. 0.08 = 8%)."""
    return sum(cf / (1.0 + rate) ** t for t, cf in enumerate(flows))


def roi(flows: list[float]) -> float:
    """ROI (prosty, bez dyskonta) w procentach."""
    if not flows:
        return 0.0
    inv = abs(min(flows[0], 0.0))
    if inv == 0.0:
        return 0.0
    gains = sum(cf for cf in flows[1:]) + max(flows[0], 0.0)
    return (gains / inv) * 100.0


def payback_simple(flows: list[float]) -> float | None:
    """Prosty okres zwrotu w latach (moze byc ulamkowy lub None — brak zwrotu)."""
    cum = 0.0
    for t, cf in enumerate(flows):
        cum += cf
        if cum >= 0 and t > 0:
            prev = cum - cf
            if cf == 0:
                return float(t)
            return t - prev / cf
    return None


def payback_discounted(flows: list[float], rate: float) -> float | None:
    """Zdyskontowany okres zwrotu w latach."""
    cum = 0.0
    for t, cf in enumerate(flows):
        dcf = cf / (1.0 + rate) ** t
        prev = cum
        cum += dcf
        if cum >= 0 and t > 0:
            if dcf == 0:
                return float(t)
            return t - prev / dcf
    return None


def compute_variant(flows: list[float], rate: float) -> dict:
    return {
        "npv": npv(flows, rate),
        "roi_pct": roi(flows),
        "payback_simple_years": payback_simple(flows),
        "payback_discounted_years": payback_discounted(flows, rate),
    }
