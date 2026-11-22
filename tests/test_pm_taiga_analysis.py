# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

# tests/test_pm_taiga_analysis.py
# Integracja Taiga - galwork-pm - ERPNext, Etap 6: analizy z dziennika -
# wskaźniki tygodni, koszt ze stawek ERPNext, budżet z WBS galwork-pm.
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from connector.base import ConnectorError
from pm.integrations.taiga import analysis, ledger, periods
from pm.models.project import Project
from pm.models.schedule import WbsItem
from tests.test_pm_taiga_ledger import W47, W49, LedgerTestCase, change

D = Decimal


class RatesErp:
  def get_list(self, doctype, *, filters=None, fields=None, limit=20):
    if doctype == "Activity Cost":
      return [{"employee": "EMP-6", "activity_type": "programowanie", "costing_rate": 100}]
    if doctype == "Activity Type":
      return [{"name": "programowanie", "costing_rate": 80}, {"name": "testowanie", "costing_rate": 50}]
    raise AssertionError(doctype)


class BrokenErp:
  def get_list(self, *a, **kw):
    raise ConnectorError("ERPNext -> 502")


class AnalysisTest(LedgerTestCase):

  def setUp(self):
    super().setUp()
    project = Project(name="Galwork")
    self.db.add(project)
    self.db.flush()
    self.db.add_all([WbsItem(project_id=project.id, code="1", name="Całość", duration_hours=99, cost=9999),
                     WbsItem(project_id=project.id, code="1.1", parent_code="1", name="API", duration_hours=6, cost=500),
                     WbsItem(project_id=project.id, code="1.2", parent_code="1", name="Testy", duration_hours=4, cost=300)])
    self.link.pm_project_id = project.id
    self.db.commit()
    ledger.apply_mapping(self.ctx, taiga_project_id=7, scope="task", taiga_object_id=100, activity_type="programowanie")
    self.apply(change(100, hours=4, at=W47))
    self.apply(change(101, hours=2, at=W47, assigned=5))
    periods.approve_week(self.db, 7, "2026-W47")
    # 2026-11-21: zmiana po zatwierdzeniu tygodnia (ten sam tydzień)
    self.apply(change(100, hours=5, at=W47 + timedelta(hours=2)))

  def test_week_indicators(self):
    result = analysis.project_analysis(self.db, 7, erp=RatesErp())
    week = result["weeks"][0]
    self.assertEqual((week["week"], week["state"], week["hours"], week["unassigned_hours"]),
                     ("2026-W47", "approved", 7.0, 2.0))
    self.assertEqual((week["unassigned_share"], week["after_approval_hours"], week["forward_hours"]), (0.29, 1.0, 0.0))

  def test_forward_corrections_after_month_close(self):
    periods.close_month(self.db, "2026-11")
    self.ctx = ledger.LedgerContext(self.db, now=W49)
    ledger.apply_mapping(self.ctx, taiga_project_id=7, scope="task", taiga_object_id=101, activity_type="testowanie")
    weeks = {w["week"]: w for w in analysis.project_analysis(self.db, 7)["weeks"]}
    self.assertEqual((weeks["2026-W49"]["forward_hours"], weeks["2026-W49"]["hours"]), (4.0, 0.0))
    self.assertEqual(weeks["2026-W47"]["state"], "frozen")

  def test_costs_and_budget(self):
    result = analysis.project_analysis(self.db, 7, erp=RatesErp())
    self.assertEqual(result["by_activity"], [
      {"activity_type": "programowanie", "hours": 5.0, "cost": 500.0},     # Activity Cost EMP-6: 100
      {"activity_type": None, "hours": 2.0, "cost": None},                 # bez rodzaju pracy - bez kosztu
    ])
    self.assertEqual(result["totals"], {"hours": 7.0, "cost": 500.0, "unassigned_hours": 2.0})
    budget = result["budget"]
    # 2026-11-21: budżet z liści WBS (6 + 4 h, 500 + 300), nie z pozycji nadrzędnej
    self.assertEqual((budget["budget_hours"], budget["budget_cost"], budget["actual_hours"]), (10, 800, 7.0))
    self.assertEqual((budget["hours_consumed"], budget["actual_cost"], budget["cost_consumed"]), (0.7, 500.0, 0.62))

  def test_without_erp_or_budget(self):
    result = analysis.project_analysis(self.db, 7, erp=BrokenErp())
    self.assertIn("502", result["rates_error"])
    self.assertIsNone(result["totals"]["cost"])
    self.link.pm_project_id = None
    self.db.commit()
    self.assertIsNone(analysis.project_analysis(self.db, 7)["budget"])
