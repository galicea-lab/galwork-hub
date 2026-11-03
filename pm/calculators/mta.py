"""Analiza Trendu Kamieni Milowych (MTA — Milestone Trend Analysis).

Nowy modul (brak odpowiednika w `formularze/skrypty/`), formalizujacy
rozdz. 5.3 `r3narzedzia.md`: seria kolejnych prognoz terminu danego kamienia
milowego pozwala wykryc narastajacy poslizg na dlugo przed terminem koncowym.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass
class ForecastPoint:
    report_date: date
    forecast_date: date


@dataclass
class MilestoneTrend:
    baseline_date: date | None
    points: list[ForecastPoint]
    drift_days: list[int]
    trend: str  # "stabilny" | "narastajace_opoznienie" | "przyspieszenie"


def analyze_milestone_trend(
    baseline_date: date | None, points: list[ForecastPoint]
) -> MilestoneTrend:
    points_sorted = sorted(points, key=lambda p: p.report_date)
    reference = baseline_date or (
        points_sorted[0].forecast_date if points_sorted else None
    )
    drift_days = (
        [(p.forecast_date - reference).days for p in points_sorted] if reference else []
    )

    if len(drift_days) < 2:
        trend = "stabilny"
    else:
        delta = drift_days[-1] - drift_days[0]
        if delta > 0:
            trend = "narastajace_opoznienie"
        elif delta < 0:
            trend = "przyspieszenie"
        else:
            trend = "stabilny"

    return MilestoneTrend(
        baseline_date=baseline_date, points=points_sorted, drift_days=drift_days, trend=trend
    )
