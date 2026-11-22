# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

# tests/test_pm_taiga_ledger.py

# Integracja Taiga - galwork-pm - ERPNext, Etap 2: okresy, słownik
# rodzajów prac, dziennik prac liczony ze stanu (scenariusze i test właściwości
# niezmiennika), stan początkowy, usunięcia, anomalie.
from __future__ import annotations

import random
import unittest
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from connector.base import ConnectorError
from db.base import Base
import models.application  # noqa: F401  (rejestracja metadanych)
import models.user  # noqa: F401
import pm.models  # noqa: F401
import wf.models  # noqa: F401
from pm.integrations.taiga import activity, ledger, periods, service
from pm.integrations.taiga.events import TaigaChange, from_webhook
from pm.models.taiga import PersonLink, TaigaEvent, TaigaEventStatus, TaigaProjectLink
from pm.models.taiga_ledger import ActivityMap, LedgerReason, TaigaItem, WorkLedger
from tests.test_pm_taiga import webhook

D = Decimal
PID = 7
HOURS = "Czas [h]"
W47 = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)   # 2026-11-21: środa 2026-W47
W48 = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)
W49 = datetime(2026, 12, 2, 12, 0, tzinfo=UTC)    # 2026-11-21: 2026-W49: czwartek 3.12 -> grudzień


def change(object_id=100, *, kind="task", action="change", at=W47, by=6, hours=None, hours_known=None,
           assigned=6, assigned_known=True, user_story=10, epic_id=None, source="webhook", needs_fetch=False):
  return TaigaChange(
    source=source, project_id=PID, object_type=kind, action=action, object_id=object_id, occurred_at=at,
    by_user_id=by, ref=object_id, user_story_id=user_story, epic_id=epic_id,
    changed_fields=frozenset({"custom_attributes"}),
    hours=None if hours is None else D(str(hours)),
    hours_known=(hours is not None) if hours_known is None else hours_known,
    hours_changed=hours is not None, assigned_to_id=assigned, assigned_known=assigned_known,
    needs_fetch=needs_fetch)


class LedgerTestCase(unittest.TestCase):

  def setUp(self):
    self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(self.engine)
    self.db = sessionmaker(bind=self.engine)()
    self.link = TaigaProjectLink(taiga_project_id=PID, taiga_slug="galwork", erp_project="PROJ-1",
                                 hours_attribute_name=HOURS, hours_attribute_id_task=1, hours_attribute_id_us=1,
                                 billable=False, active=True, billing_from=date(2026, 11, 1),
                                 baseline_at=datetime(2026, 11, 1, tzinfo=UTC))
    self.db.add(self.link)
    self.db.add_all([PersonLink(taiga_user_id=6, erp_employee="EMP-6", source="seed", confirmed=True),
                     PersonLink(taiga_user_id=5, erp_employee="EMP-5", source="seed", confirmed=True)])
    self.db.commit()
    self.ctx = ledger.LedgerContext(self.db, now=W48)

  def tearDown(self):
    self.db.close()
    self.engine.dispose()

  def apply(self, ch, ctx=None):
    result = ledger.apply_change(ctx or self.ctx, ch)
    self.db.commit()
    return result

  def rows(self, item_taiga_id=100, kind="task"):
    item = self.ctx.item(kind, item_taiga_id)
    return list(self.db.execute(select(WorkLedger).where(WorkLedger.item_id == item.id)
                                .order_by(WorkLedger.id)).scalars())

  def summary(self):
    return {(r.week, r.erp_employee, r.erp_project, r.activity_type): r.hours
            for r in ledger.week_summary(self.db, PID)}

  def assert_invariant(self):
    for item in self.db.execute(select(TaigaItem)).scalars():
      key, hours, _ = ledger.desired(self.ctx, item)
      per_key = defaultdict(lambda: D(0))
      for weeks_key, weeks in ledger.balances(self.db, item.id).items():
        per_key[weeks_key] += sum(weeks.values(), D(0))
      for k, total in per_key.items():
        self.assertEqual(total, hours if k == key else D(0), f"{item.kind} {item.taiga_id} {k}")
      if key not in per_key:
        self.assertEqual(hours, D(0), f"{item.kind} {item.taiga_id}: brak wpisów, oczekiwano {hours}")


# --- okresy -------------------------------------------------------------------

class PeriodsTest(LedgerTestCase):

  def test_iso_week_in_warsaw(self):
    # 2026-11-21: niedziela 23:30 UTC = poniedziałek 00:30 w Warszawie
    self.assertEqual(periods.iso_week(datetime(2026, 11, 22, 23, 30, tzinfo=UTC)), "2026-W48")
    self.assertEqual(periods.iso_week(datetime(2026, 11, 22, 22, 30, tzinfo=UTC)), "2026-W47")
    self.assertEqual(periods.iso_week(date(2027, 1, 1)), "2026-W53")

  def test_week_month_by_thursday(self):
    self.assertEqual(periods.week_month("2026-W48"), "2026-11")
    self.assertEqual(periods.week_month("2026-W49"), "2026-12")   # 2026-11-21: 30.11-6.12
    self.assertEqual(periods.week_month("2026-W53"), "2026-12")   # 2026-11-21: 28.12-3.01

  def test_booking_week_skips_frozen(self):
    closed = {"2026-11"}
    self.assertEqual(periods.booking_week("2026-W47", closed), "2026-W49")
    self.assertEqual(periods.booking_week("2026-W49", closed), "2026-W49")
    self.assertEqual(periods.booking_week("2026-W45", set()), "2026-W45")

  def test_close_and_approve_idempotent(self):
    periods.close_month(self.db, "2026-11")
    periods.close_month(self.db, "2026-11")
    self.assertEqual(periods.closed_months(self.db), {"2026-11"})
    periods.approve_week(self.db, PID, "2026-W49")
    self.assertEqual(periods.week_state(self.db, PID, "2026-W49"), "approved")
    self.assertEqual(periods.week_state(self.db, PID, "2026-W48"), "frozen")
    self.assertEqual(periods.week_state(self.db, PID, "2026-W50"), "open")
    with self.assertRaises(ValueError):
      periods.close_month(self.db, "listopad")


# --- słownik ------------------------------------------------------------------

class ActivityResolutionTest(LedgerTestCase):

  def items(self):
    us = TaigaItem(kind="userstory", taiga_id=10, taiga_project_id=PID, epic_ids=[30, 20],
                   hours_total=D(0), baseline_hours=D(0), deleted=False)
    task = TaigaItem(kind="task", taiga_id=100, taiga_project_id=PID, user_story_id=10, assigned_role_id=40,
                     hours_total=D(0), baseline_hours=D(0), deleted=False)
    return task, us

  def test_resolution_order(self):
    task, us = self.items()
    index = activity.MapIndex({})
    self.assertEqual(activity.resolve(task, self.link, index, us),
                     activity.Resolution(None, "PROJ-1", "none", "project"))
    self.link.default_activity_type = "domyslny"
    self.assertEqual(activity.resolve(task, self.link, index, us).activity_type, "domyslny")
    steps = [(("role", 40), "rola", "role"), (("epic", 30), "epika30", "epic"),
             (("epic", 20), "epika20", "epic"),       # 2026-11-21: najmniejsze id epiki wygrywa
             (("userstory", 10), "historyjka", "userstory"), (("task", 100), "zadanie", "task")]
    for (scope, obj), value, source in steps:
      index = index.with_entry(scope, obj, activity.MapEntry(value, None))
      result = activity.resolve(task, self.link, index, us)
      self.assertEqual((result.activity_type, result.activity_source), (value, source))

  def test_project_override_independent_of_activity(self):
    task, us = self.items()
    index = activity.MapIndex({("task", 100): activity.MapEntry("zadanie", None),
                               ("epic", 20): activity.MapEntry(None, "PROJ-KLIENT")})
    result = activity.resolve(task, self.link, index, us)
    self.assertEqual((result.activity_type, result.erp_project, result.project_source),
                     ("zadanie", "PROJ-KLIENT", "epic"))

  def test_set_mapping_versions(self):
    t1, t2, t3 = W47, W48, W49
    first = activity.set_mapping(self.db, taiga_project_id=PID, scope="userstory", taiga_object_id=10,
                                 activity_type="programowanie", now=t1)
    same = activity.set_mapping(self.db, taiga_project_id=PID, scope="userstory", taiga_object_id=10,
                                activity_type="programowanie", now=t2)
    self.assertEqual(first.id, same.id)
    activity.set_mapping(self.db, taiga_project_id=PID, scope="userstory", taiga_object_id=10,
                         activity_type="testowanie", now=t2)
    self.assertIsNone(activity.set_mapping(self.db, taiga_project_id=PID, scope="userstory",
                                           taiga_object_id=10, activity_type=None, now=t3))
    history = activity.mapping_history(self.db, PID, "userstory", 10)
    self.assertEqual([(h.activity_type, h.valid_to is not None) for h in history],
                     [("programowanie", True), ("testowanie", True)])
    self.assertEqual(activity.MapIndex.load(self.db, PID).entries, {})

  def test_set_mapping_validation(self):
    with self.assertRaises(activity.ActivityMapError):
      activity.set_mapping(self.db, taiga_project_id=PID, scope="sprint", taiga_object_id=1,
                           activity_type="programowanie")
    with self.assertRaises(activity.ActivityMapError):
      activity.set_mapping(self.db, taiga_project_id=PID, scope="task", taiga_object_id=1,
                           activity_type="Execution", allowed_activity_types=lambda: ["programowanie"])

  def test_allowed_activity_types(self):
    class Erp:
      def get_list(self, doctype, **kw):
        assert doctype == "Activity Type" and kw["filters"] == [["disabled", "=", 0]]
        return [{"name": "programowanie"}, {"name": "Execution"}, {"name": "testowanie"}]
    self.assertEqual(activity.allowed_activity_types(Erp()), ["Execution", "programowanie", "testowanie"])
    self.assertEqual(activity.allowed_activity_types(Erp(), "testowanie, programowanie, inne"),
                     ["programowanie", "testowanie"])

  def test_erpnext_dictionaries(self):
    from wf.services.dictionary_service import ERPNEXT_DICTIONARIES
    self.assertEqual(ERPNEXT_DICTIONARIES["rodzaje-aktywnosci"].sources[0].doctype, "Activity Type")
    self.assertEqual(ERPNEXT_DICTIONARIES["projekty-erp"].sources[0].doctype, "Project")


# --- dziennik: scenariusze -------------------------------------------------------------

class LedgerScenarioTest(LedgerTestCase):

  def test_hours_changes_on_real_payloads(self):
    for name, expected in (("task_change_custom_attributes_79", D("2")),
                           ("task_change_custom_attributes_79__2", D("1.5"))):
      ch = from_webhook(webhook(name), hours_name=HOURS)
      ch.assigned_to_id, ch.assigned_known = 6, True
      self.apply(ch)
      self.assertEqual(self.rows(79)[-1].hours_delta, expected)
    self.apply(from_webhook(webhook("task_delete_79"), hours_name=HOURS))
    last = self.rows(79)[-1]
    self.assertEqual((last.hours_delta, last.reason), (D("-3.5"), LedgerReason.DELETED))
    self.assertEqual([r.work_week for r in self.rows(79)], ["2026-W47"] * 3)
    self.assert_invariant()

  def test_duplicate_and_stale_events(self):
    self.apply(change(hours=2, at=W47))
    self.apply(change(hours=5, at=W47 + timedelta(hours=1)))
    # 2026-11-21: ta sama zmiana z drugiego źródła (kilkanaście ms wcześniej) i starsze zdarzenie
    self.assertFalse(self.apply(change(hours=5, at=W47 + timedelta(hours=1, milliseconds=-14))).applied)
    self.assertFalse(self.apply(change(hours=2, at=W47)).applied)
    self.assertTrue(self.apply(change(hours=5, at=W47 + timedelta(hours=1))).applied)
    self.assertEqual([r.hours_delta for r in self.rows()], [D("2"), D("3")])

  def test_reassignment_keeps_weeks(self):
    self.apply(change(hours=4, at=W47))
    self.apply(change(hours=4, assigned=5, at=W48))
    rows = self.rows()[1:]
    self.assertEqual([(r.erp_employee, r.hours_delta, r.work_week, r.origin_week, r.reason) for r in rows],
                     [("EMP-6", D("-4"), "2026-W47", "2026-W47", LedgerReason.REASSIGNED),
                      ("EMP-5", D("4"), "2026-W47", "2026-W47", LedgerReason.REASSIGNED)])
    self.assert_invariant()

  def test_mapping_preview_and_apply(self):
    self.apply(change(hours=3, at=W47))
    plans = ledger.preview_mapping(self.ctx, taiga_project_id=PID, scope="userstory", taiga_object_id=10,
                                   activity_type="programowanie")
    self.assertEqual(len(plans), 1)
    self.assertEqual({(e.key.activity_type, e.hours_delta) for e in plans[0].entries},
                     {(None, D("-3")), ("programowanie", D("3"))})
    self.assertEqual(self.db.query(ActivityMap).count(), 0)      # 2026-11-21: podgląd niczego nie zapisuje
    self.assertEqual(len(self.rows()), 1)
    ledger.apply_mapping(self.ctx, taiga_project_id=PID, scope="userstory", taiga_object_id=10,
                         activity_type="programowanie")
    self.assertEqual(self.summary(), {("2026-W47", "EMP-6", "PROJ-1", "programowanie"): D("3")})
    self.assertEqual(self.rows()[-1].reason, LedgerReason.ACTIVITY_CHANGED)
    self.assert_invariant()

  def test_reclassification_of_frozen_week_goes_forward(self):
    self.apply(change(hours=3, at=W47))
    periods.close_month(self.db, "2026-11")
    self.ctx = ledger.LedgerContext(self.db, now=W49)
    ledger.apply_mapping(self.ctx, taiga_project_id=PID, scope="task", taiga_object_id=100,
                         activity_type="testowanie")
    moved = self.rows()[1:]
    self.assertEqual({(r.work_week, r.origin_week) for r in moved}, {("2026-W49", "2026-W47")})
    self.assertEqual(self.summary(), {("2026-W47", "EMP-6", "PROJ-1", None): D("3"),
                                      ("2026-W49", "EMP-6", "PROJ-1", None): D("-3"),
                                      ("2026-W49", "EMP-6", "PROJ-1", "testowanie"): D("3")})
    self.assertIn("negative_balance", {a.code for a in ledger.anomalies(self.ctx, PID)})
    self.assert_invariant()

  def test_unconfirmed_person_then_confirm(self):
    self.db.add(PersonLink(taiga_user_id=7, erp_employee="EMP-7", source="email", confirmed=False))
    self.db.commit()
    self.apply(change(hours=2, assigned=7, at=W47))
    self.assertEqual(self.rows()[0].erp_employee, None)
    self.assertIn("no_employee", {a.code for a in ledger.anomalies(self.ctx, PID)})
    ledger.confirm_person(self.ctx, 7, "EMP-7")
    self.assertEqual(self.summary(), {("2026-W47", "EMP-7", "PROJ-1", None): D("2")})
    self.assertEqual(self.rows()[-1].reason, LedgerReason.PERSON_MAPPED)
    self.assert_invariant()

  def test_before_billing_goes_to_baseline(self):
    self.link.billing_from = date(2026, 11, 23)
    self.db.commit()
    self.apply(change(hours=5, at=W47))
    item = self.ctx.item("task", 100)
    self.assertEqual((item.hours_total, item.baseline_hours, self.rows()), (D("5"), D("5"), []))
    self.apply(change(hours=7, at=W48))
    self.assertEqual([(r.hours_delta, r.work_week) for r in self.rows()], [(D("2"), "2026-W48")])
    self.assert_invariant()

  def test_epic_relation_reclassifies_tasks(self):
    activity.set_mapping(self.db, taiga_project_id=PID, scope="epic", taiga_object_id=20,
                         activity_type="dokumentowanie")
    self.apply(change(10, kind="userstory", hours=0, assigned=6, user_story=None, at=W47))
    self.apply(change(hours=2, at=W47))
    self.assertIsNone(self.rows()[0].activity_type)
    relation = TaigaChange(source="webhook", project_id=PID, object_type="relateduserstory", action="create",
                           object_id=500, occurred_at=W48, by_user_id=6, user_story_id=10, epic_id=20)
    self.apply(relation)
    self.assertEqual(self.summary(), {("2026-W47", "EMP-6", "PROJ-1", "dokumentowanie"): D("2")})
    self.apply(TaigaChange(source="webhook", project_id=PID, object_type="relateduserstory",
                           action="delete", object_id=500, occurred_at=W48 + timedelta(hours=1),
                           by_user_id=6, user_story_id=10, epic_id=20))
    self.assertEqual(self.summary(), {("2026-W47", "EMP-6", "PROJ-1", None): D("2")})
    self.assert_invariant()

  def test_task_without_assignee_uses_author(self):
    self.apply(change(hours=3, assigned=None, by=5, at=W47))
    self.assertEqual(self.rows()[0].erp_employee, "EMP-5")
    self.assertIn("no_assignee", {a.code for a in ledger.anomalies(self.ctx, PID)})
    self.apply(change(hours=3, assigned=6, at=W48))
    self.assertEqual(self.summary(), {("2026-W47", "EMP-6", "PROJ-1", None): D("3")})

  def test_double_counting_and_multiple_assignees(self):
    us = change(10, kind="userstory", hours=4, assigned=6, user_story=None, at=W47)
    us.assigned_users = (6, 5)
    self.apply(us)
    self.apply(change(hours=2, at=W47))
    codes = {a.code for a in ledger.anomalies(self.ctx, PID)}
    self.assertTrue({"double_counting", "multiple_assignees"} <= codes)


# --- REST: dociąganie stanu, stan początkowy, usunięcia ------------------------------------

class FakeTaigaRest:
  def __init__(self):
    self.objects = {("task", 100): {"id": 100, "ref": 5, "project": PID, "subject": "z REST", "assigned_to": 6,
                                    "user_story": 10, "is_closed": False, "tags": []},
                    ("userstory", 10): {"id": 10, "ref": 1, "project": PID, "subject": "US", "assigned_to": 6,
                                        "assigned_users": [6], "epics": [{"id": 20}], "is_closed": False}}
    self.values = {("task", 100): {"attributes_values": {"1": 6}}, ("userstory", 10): {"attributes_values": {}}}
    self.memberships = [{"user": 6, "role": 40}, {"user": 5, "role": 41}]

  def get_object(self, kind, oid):
    if (kind, oid) not in self.objects:
      raise ConnectorError(f"Taiga GET /{kind}/{oid} -> 404: {{}}")
    return self.objects[(kind, oid)]

  def get_custom_attribute_values(self, kind, oid):
    return self.values[(kind, oid)]

  def list_objects(self, kind, pid, **filters):
    return [obj for (k, _), obj in self.objects.items() if k == kind]

  def list_memberships(self, pid):
    return self.memberships


class LedgerRestTest(LedgerTestCase):

  def setUp(self):
    super().setUp()
    self.taiga = FakeTaigaRest()
    self.ctx = ledger.LedgerContext(self.db, self.taiga, now=W48)

  def test_timeline_change_of_unknown_task_fetches_structure_not_hours(self):
    # 2026-11-21: z REST struktura; godziny tylko ze zdarzenia (REST ma bieżące 6 h)
    ch = change(hours=None, hours_known=False, assigned=None, assigned_known=False, source="timeline",
                needs_fetch=True)
    self.apply(ch)
    item = self.ctx.item("task", 100)
    self.assertEqual((item.hours_total, item.assigned_to, item.assigned_role_id, item.subject),
                     (D("0"), 6, 40, "z REST"))
    us = self.ctx.item("userstory", 10)
    self.assertEqual((us.epic_ids, us.hours_total), ([20], D("0")))
    self.assertEqual(self.rows(), [])
    self.apply(change(hours=2, assigned=None, assigned_known=False, source="timeline", at=W48))
    self.assertEqual([(r.hours_delta, r.erp_employee, r.work_week) for r in self.rows()],
                     [(D("2"), "EMP-6", "2026-W48")])

  def test_role_mapping(self):
    activity.set_mapping(self.db, taiga_project_id=PID, scope="role", taiga_object_id=40,
                         activity_type="programowanie")
    self.apply(change(hours=2, at=W47))
    self.assertEqual(self.rows()[0].activity_type, "programowanie")

  def test_fetch_404_means_deleted(self):
    self.apply(change(hours=2, at=W47))
    del self.taiga.objects[("task", 100)]
    self.apply(change(hours=None, hours_known=False, source="timeline", needs_fetch=True, at=W48))
    self.assertTrue(self.ctx.item("task", 100).deleted)
    self.assertEqual(self.rows()[-1].reason, LedgerReason.DELETED)
    self.assert_invariant()

  def test_baseline_then_changes(self):
    self.link.baseline_at = None
    self.db.commit()
    self.assertEqual(ledger.baseline(self.ctx, self.link), {"created": 2, "skipped": 0})
    self.assertIsNotNone(self.link.baseline_at)
    task = self.ctx.item("task", 100)
    self.assertEqual((task.hours_total, task.baseline_hours), (D("6"), D("6")))
    self.assertEqual(self.db.query(WorkLedger).count(), 0)
    self.apply(change(hours=8, at=W48 + timedelta(hours=1)))
    self.assertEqual([r.hours_delta for r in self.rows()], [D("2")])
    self.assertEqual(ledger.baseline(self.ctx, self.link), {"created": 0, "skipped": 2})
    self.assert_invariant()

  def test_detect_deletions(self):
    self.apply(change(hours=2, at=W47))
    self.taiga.objects.pop(("task", 100))
    deleted = ledger.detect_deletions(self.ctx, self.link)
    self.assertEqual([(i.kind, i.taiga_id) for i in deleted], [("task", 100)])
    self.assertEqual([(r.hours_delta, r.work_week) for r in self.rows()][-1], (D("-2"), "2026-W48"))
    self.assert_invariant()


class LedgerHandlerTest(LedgerTestCase):

  def test_waits_for_baseline_then_processes(self):
    self.link.baseline_at = None
    self.db.commit()
    payload = webhook("task_change_custom_attributes_79")
    ch = from_webhook(payload, hours_name=HOURS)
    event, _ = service.enqueue(self.db, taiga_project_id=PID, source="webhook", dedupe_key="wh:x",
                               payload=payload, change=ch)
    handler = ledger.make_handler()
    result = service.process_pending(self.db, technical_user_id=None, handlers=[handler])
    self.assertEqual((result.waiting, result.processed), (1, 0))
    self.db.refresh(event)
    self.assertEqual(event.status, TaigaEventStatus.PENDING)
    self.link.baseline_at = datetime(2026, 11, 1, tzinfo=UTC)
    self.db.commit()
    result = service.process_pending(self.db, technical_user_id=None, handlers=[handler])
    self.assertEqual(result.processed, 1)
    self.assertEqual(self.db.query(WorkLedger).one().event_id, event.id)
    self.assertEqual(self.db.query(TaigaEvent).one().status, TaigaEventStatus.PROCESSED)


# --- test właściwości ---------------------------------------------------------------

class LedgerPropertyTest(LedgerTestCase):
  """Losowe sekwencje operacji: po każdym kroku dziennik = bieżący stan."""

  def test_invariant_random_sequences(self):
    for seed in range(12):
      # 2026-11-21: każde ziarno na świeżej bazie - koszt sprawdzenia niezmiennika nie rośnie
      self.tearDown()
      self.setUp()
      self.db.add(PersonLink(taiga_user_id=7, erp_employee="EMP-7", source="email", confirmed=False))
      self.db.commit()
      rng = random.Random(seed)
      moment = W47
      for step in range(60):
        moment += timedelta(hours=rng.choice([1, 5, 30]))
        self.ctx = ledger.LedgerContext(self.db, now=moment)
        op = rng.random()
        task_id = 1000 * seed + rng.randint(1, 4)
        if op < 0.45:
          self.apply(change(task_id, hours=rng.choice([0, 1, 2.5, 4, 8]), assigned=rng.choice([5, 6, 7, None]),
                            by=rng.choice([5, 6]), user_story=rng.choice([10, 11]), at=moment))
        elif op < 0.55:
          self.apply(change(task_id, action="delete", hours=None, at=moment))
        elif op < 0.75:
          ledger.apply_mapping(self.ctx, taiga_project_id=PID, scope=rng.choice(["task", "userstory"]),
                               taiga_object_id=rng.choice([task_id, 10, 11]),
                               activity_type=rng.choice([None, "programowanie", "testowanie"]),
                               erp_project=rng.choice([None, None, "PROJ-2"]))
        elif op < 0.80:
          ledger.confirm_person(self.ctx, 7, "EMP-7")
        elif op < 0.85:
          periods.close_month(self.db, periods.week_month(periods.iso_week(moment - timedelta(days=21))))
        else:
          # 2026-11-21: stare / zdublowane zdarzenie - nie może zepsuć niezmiennika
          self.apply(change(task_id, hours=rng.choice([1, 3]), at=moment - timedelta(days=2)))
        self.assert_invariant()


if __name__ == "__main__":
  unittest.main()
