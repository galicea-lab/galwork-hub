# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

# tests/test_pm_taiga_review.py

# Integracja Taiga - galwork-pm - ERPNext, Etap 5: proces BPMN
# `pm-przeglad-tygodnia` (spiffworkflow/projekty/przeglad-tygodnia) od startu z crona,
# przez formularz PM, zapis triażu i zatwierdzenie, po wysyłkę Timesheetów do
# ERPNext (atrapa), korektę po odrzuceniu i odłożenie; przypomnienia.
from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from unittest import mock

from pm.integrations.taiga import ledger, periods, review
from pm.integrations.taiga.events import TaigaChange
from pm.models.taiga import PersonLink, TaigaProjectLink
from pm.models.taiga_ledger import ActivityMap, TaigaItem, WeekApproval
from tests.fixture_paths import fixture_model
from tests.base import ApiTestCase
from tests.test_pm_taiga_erp import FakeErp
from wf.schemas.wf_schemas import CurrentUser

D = Decimal
MODEL_DIR = fixture_model('projekty', 'przeglad-tygodnia')
W47 = datetime(2026, 11, 18, 12, tzinfo=UTC)


class FakeConfig(dict):
  def get(self, key, default=None):
    return super().get(key, default)


class FakeTaiga:
  def __init__(self):
    self.config = FakeConfig(erp_sync="true", activity_types="programowanie,testowanie")
    self.technical_user_id = 9

  def list_memberships(self, pid):
    return [{"user": 6, "full_name": "Anna", "role": 40}]


class ReviewErp(FakeErp):
  """Atrapa Timesheetów z Etapu 3 + słowniki dla walidacji rodzajów prac."""

  def get_list(self, doctype, *, filters=None, fields=None, limit=20):
    if doctype == "Activity Type":
      return [{"name": "programowanie"}, {"name": "testowanie"}]
    if doctype == "Employee" and fields and "user_id" in fields:
      return [{"name": "EMP-6", "user_id": "anna@example.com", "company_email": None, "status": "Active"}]
    return super().get_list(doctype, filters=filters, fields=fields, limit=limit)


class ReviewProcessTest(ApiTestCase):

  def prepare(self):
    from wf.models import ProcessModelVersion
    from wf.schemas.wf_schemas import ProcessModelVersionStatus
    from wf.services.filesystem_sync import sync_folder
    self.system = self.create_user(email="cron@example.com", name="cron", keycloak_sub="sub-cron")
    self.pm_user = self.create_user(email="pm@example.com", name="pm", keycloak_sub="sub-pm")
    self.anna = self.create_user(email="anna@example.com", name="anna", keycloak_sub="sub-anna")
    result = sync_folder(self.db, MODEL_DIR)
    self.assertEqual(result.status, "draft-created", result.detail)
    for version in self.db.query(ProcessModelVersion).all():
      version.status = ProcessModelVersionStatus.PUBLISHED
    self.db.commit()
    self.taiga, self.erp = FakeTaiga(), ReviewErp()
    for target, value in (("pm.integrations.taiga.process_tasks._taiga", self.taiga),
                          ("pm.integrations.taiga.process_tasks._erp", self.erp)):
      patcher = mock.patch(target, return_value=value)
      patcher.start()
      self.addCleanup(patcher.stop)
    self.db.add_all([
      TaigaProjectLink(taiga_project_id=7, taiga_slug="galwork", erp_project="PROJ-1", hours_attribute_name="Czas [h]",
                       billable=False, active=True, triage_group="/zespoly/galwork-pm", billing_from=date(2026, 11, 1),
                       baseline_at=datetime(2026, 11, 1, tzinfo=UTC)),
      PersonLink(taiga_user_id=6, erp_employee="EMP-6", source="seed", confirmed=True),
      TaigaItem(kind="userstory", taiga_id=10, ref=1, subject="Moduł rozliczeń", taiga_project_id=7,
                hours_total=D(0), baseline_hours=D(0), deleted=False),
    ])
    self.db.commit()
    self.book(100, 3)
    self.actor = CurrentUser(id=self.system.id, sub="sub-cron", username="cron", display_name="cron")
    self.current_user = CurrentUser(id=self.pm_user.id, sub="sub-pm", username="pm", email="pm@example.com",
                                    display_name="pm", groups=frozenset({"galwork-pm"}))

  def book(self, task_id, hours, at=W47):
    ledger.apply_change(ledger.LedgerContext(self.db, now=at), TaigaChange(
      source="webhook", project_id=7, object_type="task", action="change", object_id=task_id, occurred_at=at,
      by_user_id=6, ref=task_id, subject=f"zadanie {task_id}", user_story_id=10,
      changed_fields=frozenset({"custom_attributes"}), hours=D(str(hours)), hours_known=True, hours_changed=True,
      assigned_to_id=6, assigned_known=True))
    self.db.commit()

  def start(self):
    results = review.start_reviews(self.db, self.actor, week="2026-W47")
    self.assertEqual([r.status for r in results], ["started"], results)
    return results[0]

  def my_tasks(self):
    return self.client.get("/v1/wf/tasks/my").json()["items"]

  def form(self, name):
    task = next(t for t in self.my_tasks() if t["name"] == name)
    return task, self.client.get(f"/v1/wf/tasks/{task['id']}/form").json()

  def complete(self, name, changes):
    task, form = self.form(name)
    data = {**form["form_data"], **changes}
    resp = self.client.post(f"/v1/wf/tasks/{task['id']}/complete",
                            json={"data": data, "expected_lock_version": form["process_lock_version"]})
    self.assertEqual(resp.status_code, 200, resp.text)
    return resp.json()

  def instance(self):
    from wf.models import ProcessInstance
    self.db.expire_all()
    return self.db.query(ProcessInstance).one()

  # --- scenariusze

  def test_review_assign_approve_and_submit(self):
    self.start()
    task, form = self.form("Przegląd tygodnia")
    data = form["form_data"]
    self.assertEqual((data["tygodnie_do_zatwierdzenia"], data["suma_godzin"]), (["2026-W47"], 3.0))
    self.assertEqual(data["podsumowanie"][0]["osoba"], "Anna")
    self.assertEqual([(e["zakres"], e["obiekt"], e["godziny"]) for e in data["nieprzypisane"]], [("userstory", 10, 3.0)])
    self.assertIn("chosen_path", form["json_schema"]["properties"])            # 2026-11-21: decyzja z bramki
    # 2026-11-21: zatwierdzenie bez przypisania - przegląd wraca z komunikatem
    self.complete("Przegląd tygodnia", {"chosen_path": "Flow_Zatwierdz"})
    _, form = self.form("Przegląd tygodnia")
    self.assertIn("Pozostało 1 pozycji", form["form_data"]["komunikat"])
    self.assertEqual(self.db.query(WeekApproval).count(), 0)
    # 2026-11-21: rodzaj pracy spoza dozwolonych - błąd i powrót, potem poprawne przypisanie
    entries = form["form_data"]["nieprzypisane"]
    entries[0]["rodzaj_pracy"] = "Execution"
    self.complete("Przegląd tygodnia", {"chosen_path": "Flow_Zatwierdz", "nieprzypisane": entries})
    _, form = self.form("Przegląd tygodnia")
    self.assertIn("Nie wszystkie przypisania", form["form_data"]["komunikat"])
    self.assertIn("nie jest dozwolony", form["form_data"]["triage_errors"][0])
    self.assertEqual(self.db.query(ActivityMap).count(), 0)
    entries = form["form_data"]["nieprzypisane"]
    entries[0]["rodzaj_pracy"] = "programowanie"
    self.complete("Przegląd tygodnia", {"chosen_path": "Flow_Zatwierdz", "nieprzypisane": entries})
    instance = self.instance()
    self.assertEqual(instance.status.value, "completed", instance.error_message)
    mapping = self.db.query(ActivityMap).one()
    self.assertEqual((mapping.scope, mapping.taiga_object_id, mapping.source, mapping.created_by),
                     ("userstory", 10, "triage", "pm"))
    self.assertEqual(periods.week_state(self.db, 7, "2026-W47"), "approved")
    doc = self.erp.docs["TS-0001"]
    self.assertEqual((doc["docstatus"], self.erp.lines("TS-0001")), (1, {"programowanie": 3.0}))
    # 2026-11-21: kolejne uruchomienie za ten sam tydzień nic nie robi
    self.assertEqual([r.status for r in review.start_reviews(self.db, self.actor, week="2026-W47")], ["done"])

  def test_erp_rejection_goes_to_correction_and_retry(self):
    self.start()
    _, form = self.form("Przegląd tygodnia")
    entries = form["form_data"]["nieprzypisane"]
    entries[0]["rodzaj_pracy"] = "programowanie"
    self.erp.closed_projects = {"PROJ-1"}
    self.complete("Przegląd tygodnia", {"chosen_path": "Flow_Zatwierdz", "nieprzypisane": entries})
    _, form = self.form("Korekta - odrzucone przez ERPNext")
    self.assertIn("zamknięty", form["form_data"]["erp_errors"][0])
    self.assertEqual(form["form_data"]["zatwierdzone_tygodnie"], ["2026-W47"])
    self.erp.closed_projects = set()
    self.complete("Korekta - odrzucone przez ERPNext", {"chosen_path": "Flow_Ponow"})
    self.assertEqual(self.instance().status.value, "completed")
    self.assertEqual(self.erp.docs["TS-0001"]["docstatus"], 1)

  def test_postpone_saves_assignments_only(self):
    self.start()
    _, form = self.form("Przegląd tygodnia")
    entries = form["form_data"]["nieprzypisane"]
    entries[0]["rodzaj_pracy"] = "testowanie"
    self.complete("Przegląd tygodnia", {"nieprzypisane": entries})          # 2026-11-21: domyślnie: odłóż
    self.assertEqual(self.instance().status.value, "completed")
    self.assertEqual(self.db.query(ActivityMap).one().activity_type, "testowanie")
    self.assertEqual(periods.week_state(self.db, 7, "2026-W47"), "open")
    self.assertEqual(self.erp.docs, {})

  def test_only_project_pm_group_gets_task(self):
    self.start()
    self.current_user = CurrentUser(id=self.anna.id, sub="sub-anna", username="anna", email="anna@example.com",
                                    display_name="anna", groups=frozenset({"inny-zespol"}))
    self.assertEqual(self.my_tasks(), [])

  def test_start_skips_running_and_empty(self):
    self.start()
    self.assertEqual([r.status for r in review.start_reviews(self.db, self.actor, week="2026-W48")], ["running"])
    link = self.db.query(TaigaProjectLink).one()
    link.triage_group = None
    self.db.commit()
    self.assertEqual([r.status for r in review.start_reviews(self.db, self.actor, week="2026-W48")], ["skipped"])

  def test_remind(self):
    notified = review.remind(self.db, self.erp, week="2026-W48", link_base="https://pm.example.org")
    self.assertEqual(notified, ["EMP-6"])
    self.assertEqual(review.remind(self.db, self.erp, week="2026-W48"), [])  # 2026-11-21: jedno na tydzień
    self.assertEqual(review.remind(self.db, self.erp, week="2026-W47"), [])  # 2026-11-21: w W47 są godziny
    from wf.models import Notification
    note = self.db.query(Notification).one()
    self.assertEqual((note.user_id, note.link), (self.anna.id, "https://pm.example.org/rozliczenia/moj-tydzien"))
