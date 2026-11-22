# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

# tests/test_pm_taiga_api.py

# Integracja Taiga - galwork-pm - ERPNext: API ekranów galwork-pm
# (/v1/pm/api/taiga/...) - zakres wglądu (administrator / PM projektu / pracownik)
# i działanie endpointów na atrapach Taigi i ERPNext.
from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from unittest import mock

from pm.integrations.taiga import ledger, periods
from pm.models.taiga import PersonLink, TaigaProjectLink
from pm.models.taiga_ledger import ActivityMap, TaigaItem, WeekApproval, WorkLedger
from tests.base import ApiTestCase
from wf.schemas.wf_schemas import CurrentUser

D = Decimal
BASE = "/v1/pm/api/taiga"


class FakeConfig(dict):
  def get(self, key, default=None):
    return super().get(key, default)


class FakeTaiga:
  def __init__(self):
    self.config = FakeConfig(activity_types="programowanie,testowanie")
    self.technical_user_id = 9

  def list_memberships(self, pid):
    return [{"user": 6, "full_name": "Anna", "role_name": "Back", "user_email": "anna@example.org"},
            {"user": 5, "full_name": "Bartek", "role_name": "Front", "user_email": "bartek@example.org"},
            {"user": 9, "full_name": "Konto techniczne", "role_name": "PO", "user_email": "hub@example.org"}]

  def list_epics(self, pid):
    return [{"id": 20, "ref": 3, "subject": "Moduł rozliczeń"}]

  def get_project(self, pid):
    return {"roles": [{"id": 40, "name": "Back"}, {"id": 39, "name": "Front"}]}

  def webhook_secret(self, pid):
    return None

  def list_custom_attributes(self, kind, pid):
    return [{"id": 1, "name": "Czas [h]"}]

  def list_webhooks(self, pid):
    return []

  def request(self, method, path, params=None, **kw):
    return []


class FakeErp:
  def get_list(self, doctype, *, filters=None, fields=None, limit=20):
    if doctype == "Employee":
      employees = [{"name": "EMP-6", "employee_name": "Anna Nowak", "user_id": "anna@example.org",
                    "company_email": None, "status": "Active"},
                   {"name": "EMP-5", "employee_name": "Bartek", "user_id": None,
                    "company_email": "bartek@example.org", "status": "Active"}]
      if filters:
        field, _, value = filters[0]
        return [e for e in employees if e.get(field) == value]
      return employees
    if doctype == "Activity Type":
      return [{"name": "programowanie", "disabled": 0, "costing_rate": 100},
              {"name": "testowanie", "disabled": 0, "costing_rate": 80},
              {"name": "Execution", "disabled": 0, "costing_rate": 0}]
    if doctype == "Project":
      return [{"name": "PROJ-1", "status": "Open"}]
    if doctype == "Activity Cost":
      return []
    raise AssertionError(doctype)


def user(name, *, groups=(), email=None, uid=1):
  return CurrentUser(id=uid, sub=f"sub-{name}", username=name, email=email, display_name=name,
                     groups=frozenset(groups))


class TaigaApiTest(ApiTestCase):

  def prepare(self):
    from pm.routers.taiga import get_erp, get_taiga
    from router import app_pm
    self.taiga, self.erp = FakeTaiga(), FakeErp()
    app_pm.dependency_overrides[get_taiga] = lambda: self.taiga
    app_pm.dependency_overrides[get_erp] = lambda: self.erp
    admin_groups = mock.patch("wf.dependencies.ADMIN_GROUPS", frozenset({"galwork-admin"}))
    admin_groups.start()
    self.addCleanup(admin_groups.stop)
    self.db.add_all([
      TaigaProjectLink(taiga_project_id=7, taiga_slug="galwork", erp_project="PROJ-1", hours_attribute_name="Czas [h]",
                       billable=False, active=True, triage_group="/pm/galwork-pm", billing_from=date(2026, 11, 1),
                       baseline_at=datetime(2026, 11, 1, tzinfo=UTC)),
      TaigaProjectLink(taiga_project_id=8, taiga_slug="inny", erp_project="PROJ-2", hours_attribute_name="Czas [h]",
                       billable=False, active=True, triage_group="inny-pm"),
      PersonLink(taiga_user_id=6, erp_employee="EMP-6", source="seed", confirmed=True),
      PersonLink(taiga_user_id=5, erp_employee="EMP-5", source="email", confirmed=False),
    ])
    self.db.add_all([
      TaigaItem(kind="userstory", taiga_id=10, ref=1, subject="US rozliczenia", taiga_project_id=7, epic_ids=[20],
                hours_total=D(0), baseline_hours=D(0), deleted=False),
      TaigaItem(kind="task", taiga_id=100, ref=12, subject="API", taiga_project_id=7, user_story_id=10,
                assigned_to=6, hours_total=D(0), baseline_hours=D(0), deleted=False),
    ])
    self.db.commit()
    self.admin = user("admin", groups={"galwork-admin"})
    self.pm = user("pm", groups={"galwork-pm"}, uid=2)
    self.anna = user("anna", email="anna@example.org", uid=3)
    self.current_user = self.pm
    ctx = ledger.LedgerContext(self.db, now=datetime(2026, 11, 18, tzinfo=UTC))
    from pm.integrations.taiga.events import TaigaChange
    ledger.apply_change(ctx, TaigaChange(
      source="webhook", project_id=7, object_type="task", action="change", object_id=100,
      occurred_at=datetime(2026, 11, 18, tzinfo=UTC), by_user_id=6, user_story_id=10,
      changed_fields=frozenset({"custom_attributes"}), hours=D("3"), hours_known=True, hours_changed=True,
      assigned_to_id=6, assigned_known=True))
    self.db.commit()

  def as_(self, who):
    self.current_user = who

  # --- zakres wglądu

  def test_me_and_project_visibility(self):
    self.assertEqual(self.client.get(f"{BASE}/me").json()["projects"], [7])
    self.assertEqual([p["taiga_project_id"] for p in self.client.get(f"{BASE}/projects").json()], [7])
    self.as_(self.admin)
    body = self.client.get(f"{BASE}/me").json()
    self.assertEqual((body["is_admin"], body["projects"]), (True, [7, 8]))
    self.as_(self.anna)
    body = self.client.get(f"{BASE}/me").json()
    self.assertEqual((body["is_admin"], body["projects"], body["erp_employee"]), (False, [], "EMP-6"))

  def test_employee_cannot_see_project_data(self):
    self.as_(self.anna)
    for path in ("/projects/7/weeks", "/projects/7/tree", "/projects/7/unassigned", "/erp/plan", "/alerts"):
      self.assertEqual(self.client.get(BASE + path).status_code, 403, path)

  def test_pm_limited_to_own_project_and_no_admin_actions(self):
    self.assertEqual(self.client.get(f"{BASE}/projects/8/weeks").status_code, 403)
    self.assertEqual(self.client.patch(f"{BASE}/projects/7", json={"billable": True}).status_code, 403)
    self.assertEqual(self.client.post(f"{BASE}/persons/confirm",
                                      json={"taiga_user_id": 5, "erp_employee": "EMP-5"}).status_code, 403)
    self.assertEqual(self.client.get(f"{BASE}/projects/99/weeks").status_code, 404)

  def test_emails_only_for_admin(self):
    persons = self.client.get(f"{BASE}/projects/7/persons").json()
    self.assertEqual({p["taiga_user_id"] for p in persons}, {5, 6})          # 2026-11-21: bez konta technicznego
    self.assertTrue(all(p["email"] is None for p in persons))
    self.assertEqual(next(p for p in persons if p["taiga_user_id"] == 6)["links"][0]["employee_name"], "Anna Nowak")
    self.as_(self.admin)
    persons = self.client.get(f"{BASE}/projects/7/persons").json()
    self.assertEqual(next(p for p in persons if p["taiga_user_id"] == 6)["email"], "anna@example.org")

  # --- dane

  def test_weeks_and_approve(self):
    body = self.client.get(f"{BASE}/projects/7/weeks").json()
    self.assertEqual(body["weeks"], [{"week": "2026-W47", "state": "open", "month": "2026-11"}])
    self.assertEqual(body["rows"], [{"week": "2026-W47", "erp_employee": "EMP-6", "taiga_user_id": 6,
                                     "person": "Anna", "erp_project": "PROJ-1", "activity_type": None,
                                     "hours": 3.0}])
    resp = self.client.post(f"{BASE}/projects/7/weeks/2026-W47/approve")
    self.assertEqual((resp.status_code, resp.json()["approved_by"]), (200, "pm"))
    self.assertEqual(self.db.query(WeekApproval).one().week, "2026-W47")
    self.assertEqual(self.client.post(f"{BASE}/projects/7/weeks/zly/approve").status_code, 422)
    periods.close_month(self.db, "2026-11")
    self.assertEqual(self.client.post(f"{BASE}/projects/7/weeks/2026-W46/approve").status_code, 409)

  def test_tree_unassigned_and_mapping_flow(self):
    tree = self.client.get(f"{BASE}/projects/7/tree").json()
    epic = tree["epics"][0]
    self.assertEqual((epic["taiga_id"], epic["subject"]), (20, "Moduł rozliczeń"))
    task = epic["userstories"][0]["tasks"][0]
    self.assertEqual((task["ref"], task["hours"], task["activity_type"], task["activity_source"]), (12, 3.0, None, "none"))
    self.assertEqual([r["name"] for r in tree["roles"]], ["Back", "Front"])
    unassigned = self.client.get(f"{BASE}/projects/7/unassigned").json()
    self.assertEqual((unassigned[0]["user_story_id"], unassigned[0]["hours"]), (10, 3.0))
    change = {"scope": "epic", "taiga_object_id": 20, "activity_type": "programowanie"}
    preview = self.client.post(f"{BASE}/projects/7/map/preview", json=change).json()
    self.assertEqual(preview["hours_by_week"], {"2026-W47": 3.0})
    self.assertEqual(self.db.query(ActivityMap).count(), 0)                  # 2026-11-21: podgląd bez zapisu
    resp = self.client.post(f"{BASE}/projects/7/map", json={**change, "activity_type": "Execution"})
    self.assertEqual(resp.status_code, 422)                                   # 2026-11-21: spoza activity_types
    applied = self.client.post(f"{BASE}/projects/7/map", json=change).json()
    self.assertEqual(len(applied["items"]), 1)
    self.assertEqual(self.client.get(f"{BASE}/projects/7/unassigned").json(), [])
    mapping = self.client.get(f"{BASE}/projects/7/map").json()
    self.assertEqual((mapping[0]["scope"], mapping[0]["activity_type"], mapping[0]["created_by"]),
                     ("epic", "programowanie", "pm"))
    self.assertEqual(self.client.get(f"{BASE}/activity-types").json(), ["programowanie", "testowanie"])

  def test_my_weeks_only_own(self):
    self.as_(self.anna)
    body = self.client.get(f"{BASE}/me/weeks").json()
    self.assertEqual((body["erp_employee"], body["rows"]), ("EMP-6", [
      {"week": "2026-W47", "taiga_project_id": 7, "erp_project": "PROJ-1", "activity_type": None, "hours": 3.0}]))
    self.as_(user("obcy", email="obcy@example.org", uid=9))
    body = self.client.get(f"{BASE}/me/weeks").json()
    self.assertEqual((body["erp_employee"], body["rows"]), (None, []))

  def test_erp_plan_and_alerts(self):
    ledger.apply_mapping(ledger.LedgerContext(self.db), taiga_project_id=7, scope="task", taiga_object_id=100,
                         activity_type="programowanie")
    plan = self.client.get(f"{BASE}/erp/plan").json()
    self.assertEqual([(d["action"], d["erp_employee"], d["lines"]) for d in plan["docs"]],
                     [("create", "EMP-6", {"programowanie": 3.0})])
    self.assertEqual(plan["unpostable"], {})                                  # 2026-11-21: tylko administrator
    # 2026-11-21: PM: tylko anomalie dziennika jego projektów - pozycja jest kompletna, więc brak alertów
    self.assertEqual(self.client.get(f"{BASE}/alerts").json(), [])
    self.as_(self.admin)
    alerts = self.client.get(f"{BASE}/alerts").json()
    self.assertEqual({a["source"] for a in alerts}, {"taiga", "erpnext"})
    self.assertIn("project_missing", {a["code"] for a in alerts})            # 2026-11-21: PROJ-2 projektu 8

  def test_analysis_access(self):
    body = self.client.get(f"{BASE}/projects/7/analysis").json()
    self.assertEqual((body["weeks"][0]["week"], body["weeks"][0]["unassigned_hours"]), ("2026-W47", 3.0))
    self.as_(self.anna)
    self.assertEqual(self.client.get(f"{BASE}/projects/7/analysis").status_code, 403)

  def test_admin_project_update_and_person_confirm(self):
    self.as_(self.admin)
    resp = self.client.patch(f"{BASE}/projects/7", json={"default_activity_type": "testowanie", "billable": True})
    self.assertEqual((resp.json()["default_activity_type"], resp.json()["billable"]), ("testowanie", True))
    rows = self.db.query(WorkLedger).order_by(WorkLedger.id).all()
    self.assertEqual(rows[-1].activity_type, "testowanie")                    # 2026-11-21: przeliczenie po zmianie domyślnego
    self.assertEqual(self.client.patch(f"{BASE}/projects/7", json={"pm_project_id": 999}).status_code, 422)
    from pm.models.project import Project
    project = Project(name="Galwork")
    self.db.add(project)
    self.db.commit()
    resp = self.client.patch(f"{BASE}/projects/7", json={"pm_project_id": project.id})
    self.assertEqual(resp.json()["pm_project_id"], project.id)
    resp = self.client.post(f"{BASE}/persons/confirm", json={"taiga_user_id": 5, "erp_employee": "EMP-5"})
    self.assertEqual(resp.status_code, 200)
    self.assertTrue(self.db.query(PersonLink).filter_by(taiga_user_id=5).one().confirmed)
