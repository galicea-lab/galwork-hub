# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

# tests/test_pm_taiga_erp.py

# Integracja Taiga - galwork-pm - ERPNext, Etap 3: synchronizacja dziennika
# z Timesheet ERPNext na atrapie, która odtwarza zachowania sprawdzone na instancji
# testowej (UpdateAfterSubmitError, godziny <= 0 przy Submit, LinkExistsError dla
# zafakturowanego, amend `…-1`, wyszukiwanie po galwork_ref z docstatus < 2).
from __future__ import annotations

import json
import unittest
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from connector.base import ConnectorConfig, ConnectorError
from connector.erpnext.erpnext import ErpNextConnector, ErpNextValidationError, _raise_for
from db.base import Base
import models.application  # noqa: F401  (rejestracja metadanych)
import models.user  # noqa: F401
import pm.models  # noqa: F401
import wf.models  # noqa: F401
from pm.integrations.taiga import erp_sync, periods
from pm.models.taiga import PersonLink, TaigaProjectLink
from pm.models.taiga_ledger import ErpPosting, ErpTimesheet, ErpTimesheetState, TaigaItem, WorkLedger

D = Decimal
NOW = datetime(2026, 12, 2, 12, 0, tzinfo=UTC)   # 2026-11-21: 2026-W49 (grudzień wg czwartku)


def _validation(exc_type: str, message: str) -> ErpNextValidationError:
  return ErpNextValidationError(f"417: {exc_type} {message}", status_code=417, exc_type=exc_type,
                                messages=[message])


class FakeErp:
  """Atrapa ERPNext: dokumenty Timesheet w pamięci, reguły jak na instancji testowej."""

  def __init__(self):
    self.docs: dict[str, dict] = {}
    self.counter = 0
    self.closed_projects: set[str] = set()
    self.fail_technical = False
    self.billed_hidden: set[str] = set()     # 2026-11-21: faktura widoczna dopiero przy anulowaniu
    self.calls: list[tuple] = []

  def _check(self, data):
    if self.fail_technical:
      raise ConnectorError("ERPNext -> 502: Bad Gateway")
    for log in data.get("time_logs", []):
      if log["project"] in self.closed_projects:
        raise _validation("ValidationError", f"Projekt {log['project']} jest zamknięty")

  def save_doc(self, doctype, data, name=None):
    self._check(data)
    self.calls.append(("save", name))
    if name is None:
      self.counter += 1
      name = f"TS-{self.counter:04d}"
      self.docs[name] = {"name": name, "docstatus": 0, "status": "Draft", "per_billed": 0, **data}
      return self.docs[name]
    doc = self.docs[name]
    if doc["docstatus"] != 0:
      raise _validation("UpdateAfterSubmitError", "Nie można zmienić po zatwierdzeniu")
    doc.update(data)
    return doc

  def submit_doc(self, doctype, name):
    if self.fail_technical:
      raise ConnectorError("ERPNext -> 502")
    self.calls.append(("submit", name))
    doc = self.docs[name]
    if any(log["hours"] <= 0 for log in doc["time_logs"]):
      raise _validation("ValidationError", "Wartość godzin musi być większa od zera")
    doc.update(docstatus=1, status="Submitted")
    return doc

  def cancel_doc(self, doctype, name):
    self.calls.append(("cancel", name))
    doc = self.docs[name]
    if doc["status"] == "Billed" or name in self.billed_hidden:
      raise _validation("LinkExistsError", "Arkusz jest połączony z Faktura sprzedaży")
    doc.update(docstatus=2, status="Cancelled")
    return doc

  def amend_doc(self, doctype, cancelled, data):
    self._check(data)
    self.calls.append(("amend", cancelled))
    assert self.docs[cancelled]["docstatus"] == 2
    n = 1
    while f"{cancelled.split('-')[0]}-{cancelled.split('-')[1]}-{n}" in self.docs:
      n += 1
    name = f"{cancelled.split('-')[0]}-{cancelled.split('-')[1]}-{n}"
    self.docs[name] = {"name": name, "docstatus": 0, "status": "Draft", "per_billed": 0,
                       "amended_from": cancelled, **data}
    return self.docs[name]

  def get_list(self, doctype, *, filters=None, fields=None, limit=20):
    if doctype == "Timesheet":
      ref = next(f[2] for f in filters if f[0] == "galwork_ref")
      return [d for d in self.docs.values() if d.get("galwork_ref") == ref and d["docstatus"] < 2]
    if doctype == "Employee":
      return [{"name": "EMP-6", "status": "Active", "company": "FG"}, {"name": "EMP-5", "status": "Left"}]
    if doctype == "Project":
      return [{"name": "PROJ-1", "status": "Open"}, {"name": "PROJ-Z", "status": "Completed"}]
    if doctype == "Activity Type":
      return [{"name": "programowanie", "disabled": 0}, {"name": "testowanie", "disabled": 1}]
    if doctype == "Activity Cost":
      return [{"employee": "EMP-6", "activity_type": "programowanie"}]
    raise AssertionError(doctype)

  def get_doc(self, doctype, name):
    assert doctype == "Employee"
    return {"name": name, "company": "Fundacja Galicea"}

  def bill(self, name):
    self.docs[name].update(status="Billed", per_billed=100)

  def active(self, ref):
    return [d for d in self.docs.values() if d.get("galwork_ref") == ref and d["docstatus"] < 2]

  def lines(self, name):
    return {log["activity_type"]: log["hours"] for log in self.docs[name]["time_logs"]}


class ErpSyncTestCase(unittest.TestCase):

  def setUp(self):
    self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(self.engine)
    self.db = sessionmaker(bind=self.engine)()
    self.db.add(TaigaProjectLink(taiga_project_id=7, erp_project="PROJ-1", hours_attribute_name="Czas [h]",
                                 billable=True, active=True, billing_from=date(2026, 11, 1)))
    self.db.add(PersonLink(taiga_user_id=6, erp_employee="EMP-6", source="seed", confirmed=True))
    self.item = TaigaItem(kind="task", taiga_id=100, ref=12, taiga_project_id=7, hours_total=D(0),
                          baseline_hours=D(0), deleted=False)
    self.db.add(self.item)
    self.db.commit()
    self.erp = FakeErp()

  def tearDown(self):
    self.db.close()
    self.engine.dispose()

  def book(self, hours, week="2026-W49", activity="programowanie", employee="EMP-6", project="PROJ-1"):
    self.db.add(WorkLedger(item_id=self.item.id, taiga_project_id=7, taiga_user_id=6, erp_employee=employee,
                           erp_project=project, activity_type=activity, work_week=week, origin_week=week,
                           hours_delta=D(str(hours)), reason="hours_changed"))
    self.db.commit()

  def sync(self, **kw):
    return erp_sync.ErpSync(self.db, self.erp, now=NOW).sync(**kw)

  def record(self, week="2026-W49"):
    return self.db.execute(select(ErpTimesheet).where(ErpTimesheet.week == week)).scalars().one()

  def ref(self, week="2026-W49"):
    return erp_sync.galwork_ref("EMP-6", "PROJ-1", week)


class ErpSyncTest(ErpSyncTestCase):

  def test_draft_update_submit_amend_cycle(self):
    self.book(3)
    self.book(2, activity="testowanie")
    result = self.sync()
    self.assertEqual(dict(result.actions), {"create": 1})
    doc = self.erp.docs["TS-0001"]
    self.assertEqual(self.erp.lines("TS-0001"), {"programowanie": 3.0, "testowanie": 2.0})
    log = doc["time_logs"][0]
    self.assertEqual((log["from_time"], log["is_billable"], log["billing_hours"]), ("2026-11-30 00:00:00", 1, 3.0))
    self.assertIn("#12", log["description"])
    self.assertEqual((doc["company"], doc["galwork_ref"], doc["parent_project"]),
                     ("Fundacja Galicea", self.ref(), "PROJ-1"))
    self.assertIn("nie jest to ewidencja czasu pracy", doc["note"])
    # 2026-11-21: bez zmian w dzienniku - nic do zrobienia
    self.assertEqual(dict(self.sync().actions), {})
    # 2026-11-21: zmiana godzin -> nadpisanie Draft
    self.book(1.5)
    self.assertEqual(dict(self.sync().actions), {"update": 1})
    self.assertEqual(self.erp.lines("TS-0001")["programowanie"], 4.5)
    # 2026-11-21: zatwierdzenie tygodnia przez PM -> Submit
    periods.approve_week(self.db, 7, "2026-W49")
    self.assertEqual(dict(self.sync().actions), {"submit": 1})
    self.assertEqual((self.erp.docs["TS-0001"]["docstatus"], self.record().state), (1, ErpTimesheetState.SUBMITTED))
    # 2026-11-21: korekta po zatwierdzeniu -> cancel + amend, zatwierdzenie cofnięte
    self.book(-0.5)
    self.assertEqual(dict(self.sync().actions), {"amend": 1})
    self.assertEqual(self.erp.docs["TS-0001"]["docstatus"], 2)
    self.assertEqual((self.erp.docs["TS-0001-1"]["amended_from"], self.erp.docs["TS-0001-1"]["docstatus"]),
                     ("TS-0001", 0))
    self.assertEqual(self.erp.lines("TS-0001-1")["programowanie"], 4.0)
    self.assertEqual(periods.week_state(self.db, 7, "2026-W49"), "open")
    self.assertEqual((self.record().timesheet_name, self.record().state), ("TS-0001-1", ErpTimesheetState.DRAFT))
    # 2026-11-21: ponowne zatwierdzenie -> Submit poprawki
    periods.approve_week(self.db, 7, "2026-W49")
    self.assertEqual(dict(self.sync().actions), {"submit": 1})
    self.assertEqual(len(self.erp.active(self.ref())), 1)
    postings = {(p.timesheet_name, p.activity_type): p.hours for p in self.db.execute(select(ErpPosting)).scalars()}
    self.assertEqual(postings[("TS-0001-1", "programowanie")], D("4"))

  def test_approved_week_creates_and_submits(self):
    periods.approve_week(self.db, 7, "2026-W49")
    self.book(2)
    self.assertEqual(dict(self.sync().actions), {"create+submit": 1})
    self.assertEqual(self.erp.docs["TS-0001"]["docstatus"], 1)

  def test_dry_run_writes_nothing(self):
    self.book(2)
    result = self.sync(dry_run=True)
    self.assertEqual(dict(result.actions), {"create": 1})
    self.assertEqual((self.erp.docs, self.db.query(ErpTimesheet).count()), ({}, 0))

  def test_billed_week_carries_correction_forward(self):
    self.book(3, week="2026-W49")
    self.sync()
    self.erp.bill("TS-0001")
    # 2026-11-21: przeklasyfikowanie godzin zafakturowanego tygodnia (ten sam tydzień, nie zamrożony)
    self.book(-3, week="2026-W49")
    self.book(3, week="2026-W49", activity="testowanie")
    self.book(5, week="2026-W50")
    result = erp_sync.ErpSync(self.db, self.erp, now=datetime(2026, 12, 9, tzinfo=UTC)).sync()
    self.assertEqual(self.record().state, ErpTimesheetState.BILLED)
    self.assertEqual(self.erp.lines("TS-0001"), {"programowanie": 3.0})       # 2026-11-21: bez zmian
    w50 = self.record("2026-W50").timesheet_name
    self.assertEqual(self.erp.lines(w50), {"programowanie": 2.0, "testowanie": 3.0})
    self.assertEqual(result.plan.unrealized, [])

  def test_unrealized_negative_correction(self):
    self.book(3, week="2026-W48")
    erp_sync.ErpSync(self.db, self.erp, now=datetime(2026, 11, 25, tzinfo=UTC)).sync()
    periods.close_month(self.db, "2026-11")
    # 2026-11-21: korekta po zamknięciu listopada trafia do W49 (silnik dziennika) - ujemna, bez godzin do pomniejszenia
    self.book(-3, week="2026-W49")
    result = self.sync()
    self.assertEqual(self.erp.lines("TS-0001"), {"programowanie": 3.0})
    self.assertEqual([(u.activity_type, u.hours) for u in result.plan.unrealized], [("programowanie", D("-3"))])
    self.assertNotIn(self.ref("2026-W49"), [d.get("galwork_ref") for d in self.erp.docs.values()])
    # 2026-11-21: nowe godziny w kolejnym tygodniu pochłaniają korektę
    self.book(5, week="2026-W50")
    result = erp_sync.ErpSync(self.db, self.erp, now=datetime(2026, 12, 9, tzinfo=UTC)).sync()
    self.assertEqual(self.erp.lines(self.record("2026-W50").timesheet_name), {"programowanie": 2.0})
    self.assertEqual(result.plan.unrealized, [])

  def test_late_hours_from_frozen_week_go_forward(self):
    self.book(4, week="2026-W48")     # 2026-11-21: nigdy nie wysłane przed zamknięciem miesiąca
    periods.close_month(self.db, "2026-11")
    self.sync()
    self.assertEqual(self.erp.lines(self.record("2026-W49").timesheet_name), {"programowanie": 4.0})

  def test_idempotent_create_after_lost_response(self):
    self.book(2)
    self.erp.docs["TS-0009"] = {"name": "TS-0009", "docstatus": 0, "status": "Draft", "per_billed": 0,
                                "galwork_ref": self.ref(), "time_logs": []}
    self.assertEqual(dict(self.sync().actions), {"update": 1})
    self.assertEqual(len(self.erp.active(self.ref())), 1)
    self.assertEqual(self.erp.lines("TS-0009"), {"programowanie": 2.0})

  def test_validation_error_isolated_and_retried(self):
    self.db.add(TaigaProjectLink(taiga_project_id=8, erp_project="PROJ-Z", hours_attribute_name="Czas [h]",
                                 billable=False, active=True))
    self.db.commit()
    self.book(2, project="PROJ-Z")
    self.book(1)
    self.erp.closed_projects = {"PROJ-Z"}
    result = self.sync()
    self.assertEqual(dict(result.actions), {"create": 1})
    self.assertEqual([k.erp_project for k, _ in result.errors], ["PROJ-Z"])
    failed = self.db.execute(select(ErpTimesheet).where(ErpTimesheet.erp_project == "PROJ-Z")).scalars().one()
    self.assertEqual(failed.state, ErpTimesheetState.ERROR)
    self.assertIn("zamknięty", failed.erp_error)
    self.erp.closed_projects = set()
    self.assertEqual(dict(self.sync().actions), {"create": 1})

  def test_technical_error_stops_run(self):
    self.book(2)
    self.erp.fail_technical = True
    with self.assertRaises(ConnectorError):
      self.sync()
    self.erp.fail_technical = False
    self.assertEqual(dict(self.sync().actions), {"create": 1})

  def test_cancel_of_billed_doc_detected(self):
    self.book(2)
    periods.approve_week(self.db, 7, "2026-W49")
    self.sync()
    self.erp.docs["TS-0001"]["status"] = "Billed"   # 2026-11-21: faktura po ostatnim odświeżeniu
    self.book(1)
    plan = erp_sync.build_plan(self.db, now=NOW)
    self.assertEqual([d.action for d in plan.docs], ["amend"])
    sync = erp_sync.ErpSync(self.db, self.erp, now=NOW)
    self.assertEqual(sync.execute(plan.docs[0]), "billed")
    self.db.commit()
    self.assertEqual(self.record().state, ErpTimesheetState.BILLED)

  def test_link_exists_on_cancel_marks_billed(self):
    self.book(2)
    periods.approve_week(self.db, 7, "2026-W49")
    self.sync()
    self.erp.billed_hidden = {"TS-0001"}
    self.book(1)
    self.assertEqual(dict(self.sync().actions), {"billed": 1})
    self.assertEqual((self.record().state, self.erp.docs["TS-0001"]["docstatus"]), (ErpTimesheetState.BILLED, 1))
    self.assertEqual(periods.week_state(self.db, 7, "2026-W49"), "approved")   # 2026-11-21: bez anulowania nie cofamy

  def test_manual_cancel_in_erp_recreates(self):
    self.book(2)
    self.sync()
    self.erp.docs["TS-0001"].update(docstatus=2, status="Cancelled")
    self.assertEqual(dict(self.sync().actions), {"create": 1})
    self.assertEqual(len(self.erp.active(self.ref())), 1)

  def test_submit_failure_marks_error(self):
    self.book(2)
    self.sync()
    self.erp.docs["TS-0001"]["time_logs"][0]["hours"] = 0     # 2026-11-21: ręczna zmiana w ERPNext
    periods.approve_week(self.db, 7, "2026-W49")
    result = self.sync()
    self.assertEqual(len(result.errors), 1)
    self.assertEqual(self.record().state, ErpTimesheetState.ERROR)
    self.assertEqual(dict(self.sync().actions), {"update+submit": 1})

  def test_unpostable_reported(self):
    self.db.add(WorkLedger(item_id=self.item.id, taiga_project_id=7, taiga_user_id=6, erp_employee="EMP-6",
                           erp_project="PROJ-1", activity_type=None, work_week="2026-W49", origin_week="2026-W49",
                           hours_delta=D("2"), reason="hours_changed"))
    self.db.commit()
    plan = erp_sync.build_plan(self.db, now=NOW)
    self.assertEqual((plan.docs, plan.unpostable), ([], {"brak rodzaju pracy": D("2")}))

  def test_check(self):
    self.db.add(PersonLink(taiga_user_id=5, erp_employee="EMP-5", source="seed", confirmed=True))
    self.db.add(PersonLink(taiga_user_id=4, erp_employee="EMP-404", source="seed", confirmed=True))
    self.db.add(TaigaProjectLink(taiga_project_id=8, erp_project="PROJ-Z", hours_attribute_name="Czas [h]",
                                 billable=False, active=True))
    self.db.commit()
    self.book(1)
    self.book(1, activity="testowanie")
    codes = sorted(f.code for f in erp_sync.check(self.db, self.erp))
    self.assertEqual(codes, ["activity_cost_missing", "activity_type_inactive", "employee_inactive",
                             "employee_missing", "project_closed"])


class FakeResponse:
  def __init__(self, status_code, body):
    self.status_code, self.ok = status_code, 200 <= status_code < 300
    self._body, self.text = body, json.dumps(body)

  def json(self):
    return self._body


class ErpConnectorTest(unittest.TestCase):

  def test_raise_for_classifies_errors(self):
    messages = json.dumps([json.dumps({"message": "Nie można anulować, <strong>FV/1</strong>"})])
    with self.assertRaises(ErpNextValidationError) as ctx:
      _raise_for(FakeResponse(417, {"exc_type": "LinkExistsError", "_server_messages": messages}), "cancel")
    self.assertEqual((ctx.exception.exc_type, ctx.exception.messages),
                     ("LinkExistsError", ["Nie można anulować, FV/1"]))
    for code in (401, 403, 404, 500, 502):
      with self.assertRaises(ConnectorError) as ctx:
        _raise_for(FakeResponse(code, {}), "x")
      self.assertNotIsInstance(ctx.exception, ErpNextValidationError)
    _raise_for(FakeResponse(200, {}), "x")

  def test_methods(self):
    calls = []

    class Session:
      headers = {}
      base_url = "https://erp.example.org"

      def put(self, url, json=None, timeout=None):
        calls.append(("PUT", url, json))
        return FakeResponse(200, {"data": {"name": "TS-1"}})

      def post(self, url, json=None, timeout=None):
        calls.append(("POST", url, json))
        return FakeResponse(200, {"data": {"name": "TS-1-1"}, "message": {"name": "TS-1"}})

    erp = ErpNextConnector(ConnectorConfig("erpnext", {"base_url": "https://erp.example.org"}))
    erp._client = Session()
    erp.submit_doc("Timesheet", "TS-1")
    erp.cancel_doc("Timesheet", "TS-1")
    self.assertEqual(erp.amend_doc("Timesheet", "TS-1", {"employee": "E"})["name"], "TS-1-1")
    self.assertEqual(calls, [
      ("PUT", "https://erp.example.org/api/resource/Timesheet/TS-1", {"docstatus": 1}),
      ("POST", "https://erp.example.org/api/method/frappe.client.cancel", {"doctype": "Timesheet", "name": "TS-1"}),
      ("POST", "https://erp.example.org/api/resource/Timesheet", {"employee": "E", "amended_from": "TS-1"}),
    ])


if __name__ == "__main__":
  unittest.main()
