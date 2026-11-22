# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Kroki systemowe procesu `pm-przeglad-tygodnia` (serviceTask z `handler`).

- `pm.week_snapshot` - dane formularza przeglądu,
- `pm.apply_triage` - zapis przypisań; z właściwością `approve=true` także
  zatwierdzenie tygodni (gdy nic nie zostało nieprzypisane),
- `erpnext.timesheet_submit` - wysyłka Timesheetów projektu za zatwierdzone
  tygodnie. Odrzucenie merytoryczne -> `erp_errors` w danych (bramka kieruje do
  korekty); błąd techniczny -> wyjątek, silnik ponawia krok (K11).

Moduł jest ładowany leniwie przez wf/services/service_tasks.py.
"""
from __future__ import annotations

from wf.services import service_tasks

from pm.integrations.taiga import activity, review


def _taiga():
    from connector.taiga import get_connector
    return get_connector()


def _erp():
    from connector.erpnext.erpnext import get_connector
    return get_connector()


def _pid(ctx: service_tasks.ServiceContext) -> int:
    try:
        return int(ctx.data["taiga_project_id"])
    except (KeyError, TypeError, ValueError):
        raise service_tasks.ServiceTaskError("brak taiga_project_id w danych procesu") from None


@service_tasks.handler("pm.week_snapshot")
def week_snapshot(ctx: service_tasks.ServiceContext) -> service_tasks.ServiceResult:
    week = ctx.data.get("tydzien")
    if not week:
        raise service_tasks.ServiceTaskError("brak tygodnia (tydzien) w danych procesu")
    data = review.week_snapshot(ctx.db, _pid(ctx), week, taiga=_taiga())
    # 2026-11-21: komunikat z poprzedniego obiegu (np. pozostały nieprzypisane) zostaje widoczny
    data["komunikat"] = ctx.data.get("komunikat") or ""
    return service_tasks.done(data)


@service_tasks.handler("pm.apply_triage")
def apply_triage(ctx: service_tasks.ServiceContext) -> service_tasks.ServiceResult:
    taiga, erp = _taiga(), _erp()
    approve = str(ctx.params.get("approve", "")).lower() in ("1", "true", "tak")
    result = review.apply_triage(
        ctx.db, _pid(ctx), list(ctx.data.get("nieprzypisane") or []),
        weeks=list(ctx.data.get("tygodnie_do_zatwierdzenia") or []), approve=approve,
        created_by=ctx.actor.username,
        allowed=lambda: activity.allowed_activity_types(erp, taiga.config.get("activity_types")), taiga=taiga)
    return service_tasks.done(result)


@service_tasks.handler("erpnext.timesheet_submit")
def timesheet_submit(ctx: service_tasks.ServiceContext) -> service_tasks.ServiceResult:
    weeks = list(ctx.data.get("zatwierdzone_tygodnie") or ctx.data.get("tygodnie_do_zatwierdzenia") or [])
    return service_tasks.done(review.submit_timesheets(ctx.db, _erp(), _taiga(), _pid(ctx), weeks))
