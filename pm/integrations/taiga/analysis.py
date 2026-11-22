# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Analizy rozliczeń projektu Taigi z dziennika prac (Etap 6).

- wykonanie godzin wobec budżetu: budżet z WBS projektu galwork-pm
  (`WbsItem.duration_hours`, `cost`), wykonanie z dziennika wszystkich projektów
  Taigi powiązanych z tym samym projektem galwork-pm,
- koszt szacowany jak w ERPNext: stawka Activity Cost (pracownik × rodzaj pracy),
  a bez niej stawka kosztu Activity Type - może zasilić AC w EVM,
- wskaźniki dyscypliny per tydzień:
  * udział godzin bez rodzaju pracy (triaż),
  * wpisy po zatwierdzeniu tygodnia - zmiany dziennika zapisane po akceptacji PM
    (każda wymusza korektę dokumentu: cancel + amend),
  * korekty z zamkniętych miesięcy - wpisy przeniesione do przodu
    (`work_week` różny od `origin_week`).
  Spóźnienia „po fakcie” nie da się zmierzyć wprost (atrybut godzin nie ma daty
  wykonania pracy, Z2) - dwa ostatnie wskaźniki są jego mierzalnym przybliżeniem.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from connector.base import ConnectorError
from pm.integrations.taiga import periods, service
from pm.models.taiga import TaigaProjectLink
from pm.models.taiga_ledger import WeekApproval, WorkLedger

ZERO = Decimal("0")


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _f(value) -> float:
    return round(float(value or 0), 2)


def cost_rates(erp) -> tuple[dict[tuple[str, str], Decimal], dict[str, Decimal]]:
    """Stawki kosztu: (pracownik, rodzaj pracy) z Activity Cost oraz domyślne z Activity Type."""
    per_employee: dict[tuple[str, str], Decimal] = {}
    defaults: dict[str, Decimal] = {}
    for row in erp.get_list("Activity Cost", fields=["employee", "activity_type", "costing_rate"], limit=5000):
        if row.get("activity_type"):
            rate = Decimal(str(row.get("costing_rate") or 0))
            if row.get("employee"):
                per_employee[(row["employee"], row["activity_type"])] = rate
            else:
                defaults.setdefault(row["activity_type"], rate)
    for row in erp.get_list("Activity Type", fields=["name", "costing_rate"], limit=500):
        defaults.setdefault(row["name"], Decimal(str(row.get("costing_rate") or 0)))
    return per_employee, defaults


def project_analysis(db: Session, pid: int, *, erp=None) -> dict[str, Any]:
    link = service.get_link(db, pid)
    if link is None:
        raise ValueError(f"Projekt Taigi {pid} nie jest objęty integracją")
    rows = db.execute(select(WorkLedger).where(WorkLedger.taiga_project_id == pid)).scalars().all()
    approvals = {a.week: _aware(a.approved_at) for a in db.execute(select(WeekApproval).where(
        WeekApproval.taiga_project_id == pid)).scalars()}

    weeks: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: ZERO))
    by_key: dict[tuple[str | None, str | None], Decimal] = defaultdict(lambda: ZERO)
    for row in rows:
        w = weeks[row.work_week]
        w["hours"] += row.hours_delta
        if row.activity_type is None:
            w["unassigned"] += row.hours_delta
        approved_at = approvals.get(row.work_week)
        created = _aware(row.created_at)
        if approved_at is not None and created is not None and created > approved_at:
            w["after_approval"] += abs(row.hours_delta)
        if row.work_week != row.origin_week:
            w["forward"] += abs(row.hours_delta)
        by_key[(row.erp_employee, row.activity_type)] += row.hours_delta

    rates: tuple[dict, dict] | None = None
    rates_error = None
    if erp is not None:
        try:
            rates = cost_rates(erp)
        except ConnectorError as exc:
            rates_error = str(exc)

    def cost(employee: str | None, activity: str | None, hours: Decimal) -> Decimal | None:
        if rates is None or activity is None:
            return None
        per_employee, defaults = rates
        rate = per_employee.get((employee, activity), defaults.get(activity, ZERO))
        return rate * hours

    by_activity: dict[str | None, dict[str, Any]] = defaultdict(lambda: {"hours": ZERO, "cost": ZERO, "priced": True})
    for (employee, activity), hours in by_key.items():
        entry = by_activity[activity]
        entry["hours"] += hours
        value = cost(employee, activity, hours)
        if value is None:
            entry["priced"] = False
        else:
            entry["cost"] += value
    total_hours = sum((e["hours"] for e in by_activity.values()), ZERO)
    total_cost = sum((e["cost"] for e in by_activity.values()), ZERO)

    closed = periods.closed_months(db)
    cumulative = ZERO
    week_list = []
    for week in sorted(weeks):
        w = weeks[week]
        cumulative += w["hours"]
        week_list.append({
            "week": week, "state": periods.week_state(db, pid, week, closed),
            "hours": _f(w["hours"]), "cumulative": _f(cumulative),
            "unassigned_hours": _f(w["unassigned"]),
            "unassigned_share": _f(w["unassigned"] / w["hours"]) if w["hours"] > ZERO else 0.0,
            "after_approval_hours": _f(w["after_approval"]), "forward_hours": _f(w["forward"]),
        })

    return {
        "taiga_project_id": pid,
        "weeks": week_list,
        "by_activity": [{"activity_type": a, "hours": _f(e["hours"]),
                         "cost": _f(e["cost"]) if e["priced"] and rates is not None else None}
                        for a, e in sorted(by_activity.items(), key=lambda kv: (kv[0] is None, kv[0] or ""))
                        if e["hours"] != ZERO],
        "totals": {"hours": _f(total_hours), "cost": _f(total_cost) if rates is not None else None,
                   "unassigned_hours": _f(by_activity[None]["hours"]) if None in by_activity else 0.0},
        "budget": _budget(db, link, rates, cost),
        "rates_error": rates_error,
    }


def _budget(db: Session, link: TaigaProjectLink, rates, cost) -> dict[str, Any] | None:
    """Budżet z WBS projektu galwork-pm wobec wykonania wszystkich jego projektów Taigi (AC dla EVM)."""
    if not link.pm_project_id:
        return None
    from pm.models.schedule import WbsItem
    items = db.execute(select(WbsItem).where(WbsItem.project_id == link.pm_project_id)).scalars().all()
    # 2026-11-21: liście WBS - pozycje nadrzędne zwykle sumują dzieci
    parents = {i.parent_code for i in items if i.parent_code}
    leaves = [i for i in items if i.code not in parents] or items
    budget_hours = sum((i.duration_hours or 0) for i in leaves)
    budget_cost = sum((i.cost or 0) for i in leaves)
    pids = set(db.execute(select(TaigaProjectLink.taiga_project_id).where(
        TaigaProjectLink.pm_project_id == link.pm_project_id)).scalars())
    actual_hours = ZERO
    actual_cost = ZERO
    for row in db.execute(select(WorkLedger).where(WorkLedger.taiga_project_id.in_(pids))).scalars():
        actual_hours += row.hours_delta
        value = cost(row.erp_employee, row.activity_type, row.hours_delta)
        actual_cost += value or ZERO
    return {
        "pm_project_id": link.pm_project_id, "taiga_projects": sorted(pids),
        "budget_hours": round(budget_hours, 2), "budget_cost": round(budget_cost, 2),
        "actual_hours": _f(actual_hours), "actual_cost": _f(actual_cost) if rates is not None else None,
        "hours_consumed": _f(actual_hours / Decimal(str(budget_hours))) if budget_hours else None,
        "cost_consumed": _f(actual_cost / Decimal(str(budget_cost))) if budget_cost and rates is not None else None,
    }
