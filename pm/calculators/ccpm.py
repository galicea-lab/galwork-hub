"""Zarzadzanie Lancuchem Krytycznym (CCPM) — bufor SSQ i wykres goraczkowy.

Nowy modul (brak odpowiednika w `formularze/skrypty/`), formalizujacy rozdz. 7
`r3narzedzia.md`: wymiarowanie bufora projektu metoda pierwiastka sumy
kwadratow (SSQ) oraz klasyfikacja stref wykresu goraczkowego (Fever Chart).
"""
from __future__ import annotations

from dataclasses import dataclass
from math import sqrt


@dataclass
class ChainTask:
    code: str
    most_likely: float
    pessimistic: float


def ssq_buffer(tasks: list[ChainTask]) -> float:
    """Bufor projektu metoda SSQ (rozdz. 7.4): B = sqrt(sum((p_i - m_i)^2))."""
    return sqrt(sum((t.pessimistic - t.most_likely) ** 2 for t in tasks))


def cut_and_paste_buffer(removed_reserve_sum: float) -> float:
    """Metoda 'ciecia o polowe' — bufor projektu = 50% sumy usunietych rezerw."""
    return 0.5 * removed_reserve_sum


@dataclass
class FeverChartPoint:
    chain_complete_pct: float
    buffer_consumed_pct: float
    ratio: float
    zone: str  # "zielona" | "zolta" | "czerwona"


def fever_chart_zone(chain_complete_pct: float, buffer_consumed_pct: float) -> FeverChartPoint:
    """Klasyfikacja strefy wykresu goraczkowego CCPM (rozdz. 7.5).

    Heurystyka oparta na stosunku tempa zuzycia bufora do tempa postepu prac
    (ratio = zuzycie_bufora% / postep_lancucha%): ratio<=1 -> zielona (bufor
    zuzywany wolniej niz postep), ratio<=2 -> zolta (ostrzezenie), ratio>2 ->
    czerwona (interwencja). Zgodna z przykladami z ksiazki: 60% postepu / 20%
    bufora -> zielona; 30% postepu / 75% bufora -> czerwona.
    """
    epsilon = 1e-9
    if chain_complete_pct <= epsilon:
        zone = "czerwona" if buffer_consumed_pct > epsilon else "zielona"
        ratio = float("inf") if buffer_consumed_pct > epsilon else 0.0
        return FeverChartPoint(chain_complete_pct, buffer_consumed_pct, ratio, zone)

    ratio = buffer_consumed_pct / chain_complete_pct
    if ratio <= 1.0:
        zone = "zielona"
    elif ratio <= 2.0:
        zone = "zolta"
    else:
        zone = "czerwona"
    return FeverChartPoint(chain_complete_pct, buffer_consumed_pct, ratio, zone)
