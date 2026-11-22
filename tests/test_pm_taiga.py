# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

# tests/test_pm_taiga.py

# Integracja Taiga - galwork-pm - ERPNext, Etap 1: normalizacja
# zdarzeń (zanonimizowane payloady z Taigi 6 w tests/fixtures/taiga), konektor
# na atrapie sesji, endpoint webhooka z podpisem HMAC, kolejka, rekoncyliacja,
# audyt, rejestracja projektu, mapowanie osób, seed.
from __future__ import annotations

import hashlib
import hmac
import json
import pathlib
import unittest
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from connector.base import ConnectorConfig, ConnectorError
from connector.taiga.taiga import TaigaConnector
from pm.integrations.taiga import service
from pm.integrations.taiga.events import TaigaPayloadError, from_timeline, from_webhook
from pm.models.taiga import PersonLink, TaigaEvent, TaigaEventStatus, TaigaProjectLink
from tests.base import ApiTestCase

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "taiga"
HOURS = "Czas [h]"
SECRET = "sekret-testowy"


def webhook(name: str) -> dict:
  return json.loads((FIXTURES / "webhook" / f"{name}.json").read_text(encoding="utf-8"))


def timeline(name: str) -> dict:
  return json.loads((FIXTURES / "timeline" / f"{name}.json").read_text(encoding="utf-8"))


# --- normalizacja -------------------------------------------------------------

class WebhookNormalizationTest(unittest.TestCase):

  def test_all_fixtures_normalize(self):
    for path in sorted((FIXTURES / "webhook").glob("*.json")):
      with self.subTest(path.name):
        change = from_webhook(json.loads(path.read_text(encoding="utf-8")), hours_name=HOURS)
        self.assertEqual(change.project_id, 7)
        self.assertIsNotNone(change.occurred_at)
        self.assertEqual(change.by_user_id in (5, 6), True)
    for path in sorted((FIXTURES / "timeline").glob("*.json")):
      with self.subTest(path.name):
        change = from_timeline(json.loads(path.read_text(encoding="utf-8")), hours_name=HOURS)
        self.assertEqual(change.project_id, 7)

  def test_first_hours_entry(self):
    change = from_webhook(webhook("task_change_custom_attributes_79"), hours_name=HOURS)
    self.assertEqual((change.object_type, change.action, change.object_id), ("task", "change", 79))
    self.assertEqual(change.hours, Decimal("2"))
    self.assertTrue(change.hours_known and change.hours_changed)
    self.assertEqual(change.changed_fields, frozenset({"custom_attributes"}))
    # 2026-11-21: zadanie niesie pełną US - w tym jej godziny (wykrycie podwójnego liczenia)
    self.assertEqual(change.user_story_id, 64)
    self.assertEqual(change.user_story_hours, Decimal("3.5"))

  def test_hours_changed(self):
    change = from_webhook(webhook("task_change_custom_attributes_79__2"), hours_name=HOURS)
    self.assertEqual(change.hours, Decimal("3.5"))
    self.assertTrue(change.hours_changed)

  def test_hours_cleared_is_changed_to_none(self):
    change = from_webhook(webhook("task_change_custom_attributes_81"), hours_name=HOURS)
    self.assertIsNone(change.hours)
    self.assertTrue(change.hours_known and change.hours_changed)

  def test_delete_has_no_hours(self):
    change = from_webhook(webhook("task_delete_79"), hours_name=HOURS)
    self.assertEqual(change.action, "delete")
    self.assertFalse(change.hours_known)
    self.assertEqual(change.assigned_to_id, 6)

  def test_assignee_from_data_not_from_diff(self):
    # 2026-11-21: diff.assigned_to niesie full_name; id wykonawcy jest tylko w data.assigned_to
    change = from_webhook(webhook("task_change_assigned_to_81"), hours_name=HOURS)
    self.assertEqual((change.by_user_id, change.assigned_to_id), (5, 6))
    self.assertTrue(change.assigned_known)
    self.assertTrue(change.is_closed)
    self.assertEqual(change.changed_fields, frozenset({"assigned_to"}))
    self.assertFalse(change.hours_changed)

  def test_tags(self):
    change = from_webhook(webhook("task_change_tags_81"), hours_name=HOURS)
    self.assertEqual(change.tags, ())
    self.assertEqual(change.changed_fields, frozenset({"tags"}))

  def test_relateduserstory(self):
    change = from_webhook(webhook("relateduserstory_delete_57"), hours_name=HOURS)
    self.assertEqual((change.object_type, change.action, change.object_id), ("relateduserstory", "delete", 57))
    self.assertEqual((change.epic_id, change.user_story_id), (27, 65))

  def test_userstory_hours_and_assigned_users(self):
    change = from_webhook(webhook("userstory_change_custom_attributes_64"), hours_name=HOURS)
    self.assertEqual(change.hours, Decimal("3.5"))
    change = from_webhook(webhook("userstory_change_assigned_users_64"), hours_name=HOURS)
    self.assertEqual(change.assigned_users, (6,))

  def test_noise_only_diff(self):
    payload = webhook("task_change_tags_79")
    payload["change"]["diff"] = {"taskboard_order": {"from": 1, "to": 2}}
    change = from_webhook(payload, hours_name=HOURS)
    self.assertEqual(change.changed_fields, frozenset())

  def test_invalid_hours_value(self):
    payload = webhook("task_change_custom_attributes_79")
    payload["data"]["custom_attributes_values"][HOURS] = "dwie"
    change = from_webhook(payload, hours_name=HOURS)
    self.assertFalse(change.hours_known)
    self.assertIn("niepoprawna", change.hours_error)

  def test_other_attribute_name(self):
    change = from_webhook(webhook("task_change_custom_attributes_79"), hours_name="Godziny")
    self.assertIsNone(change.hours)
    self.assertFalse(change.hours_changed)

  def test_malformed(self):
    with self.assertRaises(TaigaPayloadError):
      from_webhook({"data": {}}, hours_name=HOURS)
    with self.assertRaises(TaigaPayloadError):
      from_timeline({"event_type": "zly", "data": {}}, hours_name=HOURS)


class TimelineNormalizationTest(unittest.TestCase):

  def test_hours_by_other_user(self):
    change = from_timeline(timeline("tasks_task_change_1160"), hours_name=HOURS)
    self.assertEqual((change.object_type, change.action, change.object_id), ("task", "change", 68))
    self.assertEqual((change.by_user_id, change.hours), (5, Decimal("12")))
    self.assertTrue(change.hours_changed)
    self.assertFalse(change.needs_fetch)
    self.assertEqual(change.occurred_at, datetime(2026, 11, 20, 21, 43, 1, 139000, tzinfo=UTC))
    self.assertEqual(change.extra["timeline_id"], 1160)

  def test_hours_cleared(self):
    change = from_timeline(timeline("tasks_task_change_1150"), hours_name=HOURS)
    self.assertIsNone(change.hours)
    self.assertTrue(change.hours_changed and change.hours_known)
    self.assertEqual(change.user_story_id, 66)

  def test_assignee_needs_fetch(self):
    change = from_timeline(timeline("tasks_task_change_1158"), hours_name=HOURS)
    self.assertTrue(change.needs_fetch)
    self.assertFalse(change.assigned_known)

  def test_create_needs_fetch(self):
    self.assertTrue(from_timeline(timeline("tasks_task_create_1142"), hours_name=HOURS).needs_fetch)

  def test_status_only(self):
    change = from_timeline(timeline("tasks_task_change_1152"), hours_name=HOURS)
    self.assertEqual(change.changed_fields, frozenset({"status"}))
    self.assertFalse(change.hours_changed or change.needs_fetch)

  def test_relateduserstory(self):
    change = from_timeline(timeline("epics_relateduserstory_create_1138"), hours_name=HOURS)
    self.assertEqual((change.object_type, change.object_id), ("relateduserstory", 58))
    self.assertEqual((change.epic_id, change.user_story_id), (27, 66))


# --- konektor -----------------------------------------------------------------

class FakeResponse:
  def __init__(self, status_code=200, json_data=None):
    self.status_code = status_code
    self.ok = 200 <= status_code < 300
    self._json = json_data
    self.text = "" if json_data is None else json.dumps(json_data)

  def json(self):
    return self._json


class FakeSession:
  def __init__(self):
    self.headers = {}
    self.calls = []
    self.queue: dict[tuple[str, str], list[FakeResponse]] = {}

  def program(self, method, path, *responses):
    self.queue.setdefault((method, path), []).extend(responses)

  def request(self, method, url, params=None, json=None, headers=None, timeout=None):
    path = url.split("/api/v1", 1)[1]
    self.calls.append({"method": method, "path": path, "params": params, "json": json,
                       "headers": headers or {}})
    responses = self.queue.get((method, path))
    if not responses:
      return FakeResponse(404, {"_error_message": "not found"})
    return responses.pop(0) if len(responses) > 1 else responses[0]


class TaigaConnectorTest(unittest.TestCase):

  def setUp(self):
    self.session = FakeSession()
    self.session.base_url = "https://taiga.example.org"
    self.taiga = TaigaConnector(ConnectorConfig("taiga", {
      "base_url": "https://taiga.example.org", "username": "hub", "password": "x",
      "technical_user_id": "12", "webhook_secret_7": SECRET}))
    self.taiga._client = self.session
    self.session.program("POST", "/auth", FakeResponse(200, {"auth_token": "T1"}))

  def test_bearer_token_and_no_pagination(self):
    self.session.program("GET", "/tasks", FakeResponse(200, [{"id": 1}]))
    self.assertEqual(self.taiga.list_objects("task", 7, modified_date__gte="2026-11-20"), [{"id": 1}])
    call = self.session.calls[-1]
    self.assertEqual(call["headers"]["Authorization"], "Bearer T1")
    self.assertEqual(call["headers"]["x-disable-pagination"], "True")
    self.assertEqual(call["params"], {"project": 7, "modified_date__gte": "2026-11-20"})
    self.assertEqual(self.session.calls[0]["json"]["username"], "hub")

  def test_relogin_after_401(self):
    self.session.program("GET", "/users/me", FakeResponse(401, {}), FakeResponse(200, {"id": 12}))
    self.session.queue[("POST", "/auth")] = [FakeResponse(200, {"auth_token": "T1"}),
                                              FakeResponse(200, {"auth_token": "T2"})]
    self.assertTrue(self.taiga.test_connection())
    self.assertEqual(self.session.calls[-1]["headers"]["Authorization"], "Bearer T2")

  def test_error_raises(self):
    self.session.program("GET", "/history/task/79", FakeResponse(404, {"_error_message": ""}))
    with self.assertRaises(ConnectorError):
      self.taiga.get_history("task", 79)

  def test_config_values(self):
    self.assertEqual(self.taiga.technical_user_id, 12)
    self.assertEqual(self.taiga.webhook_secret(7), SECRET)
    self.assertIsNone(self.taiga.webhook_secret(8))

  def test_timeline_stops_at_cursor_and_last_page(self):
    self.session.program("GET", "/timeline/project/7",
                         FakeResponse(200, [{"id": 30}, {"id": 28}]),
                         FakeResponse(200, [{"id": 26}, {"id": 24}]),
                         FakeResponse(404, {}))
    self.assertEqual([e["id"] for e in self.taiga.iter_timeline(7, after_id=25)], [30, 28, 26])
    pages = [c["params"]["page"] for c in self.session.calls if c["path"] == "/timeline/project/7"]
    self.assertEqual(pages, [1, 2])
    self.assertNotIn("x-disable-pagination", self.session.calls[-1]["headers"])

  def test_timeline_until_404(self):
    self.session.program("GET", "/timeline/project/7",
                         FakeResponse(200, [{"id": 2}]), FakeResponse(404, {}))
    self.assertEqual([e["id"] for e in self.taiga.iter_timeline(7)], [2])

  def test_unknown_kind(self):
    with self.assertRaises(ValueError):
      self.taiga.list_objects("issue", 7)


# --- atrapa Taigi dla serwisu i endpointu ------------------------------------------

class FakeTaiga:
  def __init__(self):
    self.secrets = {7: SECRET}
    self.technical_user_id = 12
    self.timeline: list[dict] = []
    self.webhooks: list[dict] = []
    self.webhook_logs: dict[int, list[dict]] = {}
    self.attributes = {"task": [], "userstory": []}
    self.memberships: list[dict] = []
    self.projects = [{"id": 7, "slug": "galwork"}]
    self.created: list[tuple] = []

  def webhook_secret(self, pid):
    return self.secrets.get(pid)

  def iter_timeline(self, pid, *, after_id=None, max_pages=50):
    for entry in sorted(self.timeline, key=lambda e: -e["id"]):
      if after_id is not None and entry["id"] <= after_id:
        return
      yield entry

  def list_webhooks(self, pid):
    return self.webhooks

  def list_webhook_logs(self, wid):
    return self.webhook_logs.get(wid, [])

  def list_custom_attributes(self, kind, pid):
    return self.attributes[kind]

  def create_custom_attribute(self, kind, pid, *, name, attr_type="number", description="", order=None):
    attr = {"id": 100 + len(self.created), "name": name, "type": attr_type}
    self.attributes[kind].append(attr)
    self.created.append(("attribute", kind))
    return attr

  def create_webhook(self, pid, *, name, url, key):
    hook = {"id": 2, "name": name, "url": url}
    self.webhooks.append(hook)
    self.created.append(("webhook", url, key))
    return hook

  def get_project_by_slug(self, slug):
    return {"id": 7, "slug": slug}

  def list_memberships(self, pid):
    return self.memberships

  def request(self, method, path, params=None, **kw):
    if path == "/projects":
      return self.projects
    raise AssertionError(path)


class FakeErp:
  def __init__(self, employees):
    self.employees = employees

  def get_list(self, doctype, *, filters=None, fields=None, limit=20):
    assert doctype == "Employee"
    return self.employees


def sign(body: bytes, secret: str = SECRET) -> str:
  return hmac.new(secret.encode(), body, hashlib.sha1).hexdigest()


# --- endpoint webhooka -------------------------------------------------------------

class TaigaWebhookApiTest(ApiTestCase):
  URL = "/v1/integrations/taiga/webhook/7"

  def prepare(self):
    from pm.integrations.taiga.router import _taiga
    from router import app_integrations
    self.taiga = FakeTaiga()
    app_integrations.dependency_overrides[_taiga] = lambda: self.taiga
    self.db.add(TaigaProjectLink(taiga_project_id=7, taiga_slug="galwork", hours_attribute_name=HOURS,
                                 billable=False, active=True))
    self.db.commit()

  def post(self, body: bytes, signature: str | None = "auto", url: str | None = None):
    headers = {"Content-Type": "application/json"}
    if signature == "auto":
      signature = sign(body)
    if signature is not None:
      headers["X-TAIGA-WEBHOOK-SIGNATURE"] = signature
    return self.client.post(url or self.URL, content=body, headers=headers)

  def body(self, name="task_change_custom_attributes_79") -> bytes:
    return json.dumps(webhook(name), ensure_ascii=False).encode("utf-8")

  def test_valid_signature_queues_event(self):
    resp = self.post(self.body())
    self.assertEqual(resp.status_code, 202, resp.text)
    self.assertEqual(resp.json()["status"], "queued")
    event = self.db.query(TaigaEvent).one()
    self.assertEqual((event.source, event.object_type, event.action, event.object_id), ("webhook", "task", "change", 79))
    self.assertEqual((event.by_taiga_user_id, event.status), (6, TaigaEventStatus.PENDING))
    self.db.expire_all()
    self.assertIsNotNone(self.db.query(TaigaProjectLink).one().last_webhook_at)

  def test_signature_over_raw_body(self):
    # 2026-11-21: inny zapis tego samego JSON (spacje) - podpis musi dotyczyć surowej treści
    body = self.body()
    reformatted = json.dumps(json.loads(body), indent=2).encode()
    self.assertEqual(self.post(reformatted, signature=sign(body)).status_code, 403)

  def test_duplicate_delivery(self):
    body = self.body()
    self.assertEqual(self.post(body).json()["status"], "queued")
    self.assertEqual(self.post(body).json()["status"], "duplicate")
    self.assertEqual(self.db.query(TaigaEvent).count(), 1)

  def test_bad_or_missing_signature(self):
    self.assertEqual(self.post(self.body(), signature="00" * 20).status_code, 403)
    self.assertEqual(self.post(self.body(), signature=None).status_code, 403)
    self.assertEqual(self.db.query(TaigaEvent).count(), 0)

  def test_unknown_or_inactive_project(self):
    self.assertEqual(self.post(self.body(), url="/v1/integrations/taiga/webhook/99").status_code, 404)
    link = self.db.query(TaigaProjectLink).one()
    link.active = False
    self.db.commit()
    self.assertEqual(self.post(self.body()).status_code, 404)

  def test_missing_secret(self):
    self.taiga.secrets = {}
    self.assertEqual(self.post(self.body()).status_code, 503)

  def test_project_mismatch(self):
    self.db.add(TaigaProjectLink(taiga_project_id=8, hours_attribute_name=HOURS, billable=False, active=True))
    self.db.commit()
    self.taiga.secrets[8] = SECRET
    resp = self.post(self.body(), url="/v1/integrations/taiga/webhook/8")
    self.assertEqual(resp.status_code, 400)

  def test_ping(self):
    body = json.dumps({"action": "test", "type": "test", "by": {"id": 6}, "date": "2026-11-21T10:00:00Z",
                       "data": {"test": "test"}}).encode()
    self.assertEqual(self.post(body).json(), {"status": "test"})
    self.assertEqual(self.db.query(TaigaEvent).count(), 0)

  def test_invalid_json(self):
    self.assertEqual(self.post(b"{nie-json").status_code, 400)


# --- serwis -------------------------------------------------------------------

class TaigaServiceTest(ApiTestCase):

  def prepare(self):
    service.clear_handlers()
    self.addCleanup(service.clear_handlers)
    self.taiga = FakeTaiga()
    self.link = TaigaProjectLink(taiga_project_id=7, taiga_slug="galwork", hours_attribute_name=HOURS,
                                 hours_attribute_id_task=1, hours_attribute_id_us=1,
                                 billable=False, active=True)
    self.db.add(self.link)
    self.db.commit()

  def enqueue_webhook(self, name, **overrides):
    payload = webhook(name)
    payload.update(overrides)
    change = from_webhook(payload, hours_name=HOURS)
    raw = json.dumps(payload).encode()
    event, _ = service.enqueue(self.db, taiga_project_id=7, source="webhook",
                               dedupe_key=service.webhook_dedupe_key(raw), payload=payload, change=change)
    return event

  # --- kolejka

  def test_process_without_handlers_keeps_relevant_pending(self):
    relevant = self.enqueue_webhook("task_change_custom_attributes_79")
    technical = self.enqueue_webhook("task_change_tags_79", by={"id": 12})
    irrelevant = self.enqueue_webhook("task_change_tags_81", type="issue")
    result = service.process_pending(self.db, technical_user_id=12)
    self.assertEqual((result.waiting, result.ignored, result.processed), (1, 2, 0))
    self.db.refresh(relevant)
    self.db.refresh(technical)
    self.db.refresh(irrelevant)
    self.assertEqual(relevant.status, TaigaEventStatus.PENDING)
    self.assertEqual((technical.status, technical.error), (TaigaEventStatus.IGNORED, "zmiana konta technicznego"))
    self.assertEqual(irrelevant.status, TaigaEventStatus.IGNORED)

  def test_process_with_handlers_in_order_and_error_isolated(self):
    seen = []

    def handler(db, event, change):
      seen.append(change.object_id)
      if change.object_id == 81:
        raise RuntimeError("awaria konsumenta")

    first = self.enqueue_webhook("task_change_custom_attributes_79")
    failing = self.enqueue_webhook("task_change_custom_attributes_81__2")
    later = self.enqueue_webhook("task_change_custom_attributes_68")
    result = service.process_pending(self.db, technical_user_id=12, handlers=[handler])
    self.assertEqual(seen, [79, 81, 68])
    self.assertEqual((result.processed, result.errors), (2, 1))
    for event, status in ((first, TaigaEventStatus.PROCESSED), (failing, TaigaEventStatus.ERROR),
                          (later, TaigaEventStatus.PROCESSED)):
      self.db.refresh(event)
      self.assertEqual(event.status, status)
    self.assertIn("awaria konsumenta", failing.error)
    self.assertEqual(failing.attempts, 1)

  def test_unlinked_project_ignored(self):
    self.link.active = False
    self.db.commit()
    event = self.enqueue_webhook("task_change_custom_attributes_79")
    service.process_pending(self.db, technical_user_id=None)
    self.db.refresh(event)
    self.assertEqual(event.status, TaigaEventStatus.IGNORED)

  def test_purge(self):
    event = self.enqueue_webhook("task_change_tags_79", by={"id": 12})
    service.process_pending(self.db, technical_user_id=12)
    event.received_at = datetime.now(UTC) - timedelta(days=100)
    self.db.commit()
    pending = self.enqueue_webhook("task_change_custom_attributes_79")
    pending.received_at = datetime.now(UTC) - timedelta(days=100)
    self.db.commit()
    self.assertEqual(service.purge_events(self.db, older_than_days=90), 1)
    self.assertEqual(self.db.query(TaigaEvent).count(), 1)

  # --- rekoncyliacja

  def test_reconcile_timeline_cursor_and_dedupe(self):
    self.taiga.timeline = [timeline("tasks_task_change_1144"), timeline("tasks_task_change_1150"),
                           timeline("tasks_task_change_1160")]
    self.assertEqual(service.reconcile_timeline(self.db, self.taiga, self.link), 3)
    self.assertEqual(self.link.timeline_cursor, 1160)
    events = self.db.query(TaigaEvent).order_by(TaigaEvent.id).all()
    self.assertEqual([e.dedupe_key for e in events], ["tl:7:1144", "tl:7:1150", "tl:7:1160"])
    self.assertEqual(events[2].by_taiga_user_id, 5)
    # 2026-11-21: nowy wpis po kursorze
    self.taiga.timeline.append(timeline("tasks_task_change_1158") | {"id": 1170})
    self.assertEqual(service.reconcile_timeline(self.db, self.taiga, self.link), 1)
    self.assertEqual(self.link.timeline_cursor, 1170)
    self.assertEqual(service.reconcile_timeline(self.db, self.taiga, self.link), 0)

  def test_reconcile_first_run_respects_billing_from(self):
    self.link.billing_from = date(2026, 11, 21)
    self.db.commit()
    old = timeline("tasks_task_change_1160")
    new = timeline("tasks_task_change_1150") | {"id": 1200, "created": "2026-11-23T08:00:00Z"}
    self.taiga.timeline = [old, new]
    self.assertEqual(service.reconcile_timeline(self.db, self.taiga, self.link), 1)
    self.assertEqual(self.link.timeline_cursor, 1200)

  # --- audyt

  def test_audit_findings(self):
    base = "https://hub.example.org/v1/integrations/taiga/webhook"
    self.taiga.memberships = [{"user": 12, "is_admin": True}]
    self.taiga.webhooks = [{"id": 2, "url": f"{base}/7"}]
    self.taiga.webhook_logs = {2: [{"status": 404, "created": "2026-11-20T21:41:15Z"},
                                   {"status": 200, "created": "2026-11-20T21:16:09Z"}]}
    self.taiga.attributes = {"task": [{"id": 1, "name": HOURS}], "userstory": [{"id": 1, "name": "Godziny"}]}
    self.taiga.projects = [{"id": 7, "slug": "galwork"}, {"id": 9, "slug": "inny"}]
    self.link.last_reconcile_at = datetime.now(UTC)
    self.db.commit()
    codes = {f.code for f in service.audit(self.db, self.taiga, webhook_url=base)}
    self.assertEqual(codes, {"webhook_failed", "hours_attribute_renamed", "webhook_silent",
                             "project_not_linked"})
    self.taiga.secrets = {}
    self.taiga.webhooks = [{"id": 3, "url": "https://n8n.example.org/webhook/x"}]
    codes = {f.code for f in service.audit(self.db, self.taiga, webhook_url=base)}
    self.assertTrue({"webhook_secret_missing", "webhook_missing"} <= codes)

  def test_audit_without_webhook(self):
    # 2026-11-21: webhook opcjonalny: bez webhook_url żadnych kontroli webhooka ani sekretu
    self.taiga.secrets = {}
    self.taiga.memberships = [{"user": 12, "is_admin": False}]
    self.taiga.attributes = {"task": [{"id": 1, "name": HOURS}], "userstory": [{"id": 1, "name": HOURS}]}
    findings = service.audit(self.db, self.taiga, webhook_url=None)
    self.assertEqual({f.code for f in findings},
                     {"webhook_disabled", "technical_not_admin", "reconcile_stale"})
    self.taiga.memberships = []
    self.link.last_reconcile_at = datetime.now(UTC) - timedelta(hours=1)
    self.db.commit()
    self.assertEqual({f.code for f in service.audit(self.db, self.taiga, webhook_url="")},
                     {"webhook_disabled", "technical_not_member"})

  # --- rejestracja

  def test_register_dry_run_changes_nothing(self):
    self.db.delete(self.link)
    self.db.commit()
    result = service.register_project(self.db, self.taiga, slug="galwork",
                                      webhook_url="https://hub.example.org/wh", apply=False)
    self.assertEqual(self.taiga.created, [])
    self.assertEqual(len(result.actions), 4)
    self.assertEqual(self.db.query(TaigaProjectLink).count(), 0)

  def test_register_apply_idempotent(self):
    self.taiga.attributes["task"] = [{"id": 1, "name": HOURS, "type": "number"}]
    result = service.register_project(self.db, self.taiga, slug="galwork",
                                      webhook_url="https://hub.example.org/wh/", apply=True)
    self.assertEqual(result.created_attributes, ["userstory"])
    self.assertEqual(self.taiga.webhooks[0]["url"], "https://hub.example.org/wh/7")
    self.assertEqual(len(result.webhook_secret), 64)
    self.assertEqual(self.taiga.created[-1][2], result.webhook_secret)
    self.db.refresh(self.link)
    self.assertEqual((self.link.hours_attribute_id_task, self.link.webhook_id), (1, 2))
    self.assertEqual(self.link.hours_attribute_id_us, result.attributes["userstory"])
    again = service.register_project(self.db, self.taiga, slug="galwork",
                                     webhook_url="https://hub.example.org/wh", apply=True)
    self.assertEqual((again.actions, again.webhook_secret), ([], None))
    self.assertEqual(len(self.taiga.created), 2)

  # --- osoby

  def test_propose_person_links(self):
    self.taiga.memberships = [
      {"user": 6, "full_name": "User 6", "user_email": "Admin@Example.org"},
      {"user": 5, "full_name": "User 5", "user_email": "ktos@example.org"},
      {"user": 7, "full_name": "User 7", "user_email": None},
      {"user": 8, "full_name": "User 8", "user_email": "biuro@example.org"},
      {"user": 12, "full_name": "Konto techniczne", "user_email": "admin@example.org"},
    ]
    self.db.add(PersonLink(taiga_user_id=8, erp_employee="HR-EMP-00002", source="seed", confirmed=True))
    self.db.commit()
    erp = FakeErp([{"name": "HR-EMP-00001", "user_id": "admin@example.org", "company_email": None},
                   {"name": "HR-EMP-00002", "user_id": None, "company_email": "biuro@example.org"}])
    proposals = service.propose_person_links(self.db, self.taiga, erp, taiga_project_id=7, apply=True)
    self.assertEqual([(p.taiga_user_id, p.erp_employee, p.status) for p in proposals],
                     [(6, "HR-EMP-00001", "new"), (5, None, "no_match"), (7, None, "no_email"),
                      (8, "HR-EMP-00002", "exists")])
    created = self.db.query(PersonLink).filter_by(taiga_user_id=6).one()
    self.assertEqual((created.source, created.confirmed), ("email", False))
    again = service.propose_person_links(self.db, self.taiga, erp, taiga_project_id=7, apply=True)
    self.assertEqual(again[0].status, "exists")
    self.assertEqual(self.db.query(PersonLink).count(), 2)

  # --- seed

  def test_load_seed_idempotent(self):
    data = {
      "_opis": "komentarz",
      "pm_taiga_project_link": [{"taiga_project_id": 7, "taiga_slug": "galwork", "erp_project": "PROJ-0003",
                                 "hours_attribute_id_task": 1, "hours_attribute_id_us": 1,
                                 "billing_from": "2026-11-23", "billable": False, "active": True,
                                 "_erp_project_name": "komentarz"}],
      "pm_person_link": [{"taiga_user_id": 6, "erp_employee": "HR-EMP-00001", "source": "email",
                          "_uwagi": "zgodny e-mail"},
                         {"taiga_user_id": 5, "erp_employee": "HR-EMP-00002", "source": "manual-test"}],
    }
    self.assertEqual(service.load_seed(self.db, data),
                     {"projects_created": 0, "projects_updated": 1, "persons_created": 2, "persons_updated": 0})
    self.assertEqual(service.load_seed(self.db, data)["persons_updated"], 2)
    self.db.refresh(self.link)
    self.assertEqual((self.link.erp_project, self.link.billing_from), ("PROJ-0003", date(2026, 11, 23)))
    people = self.db.query(PersonLink).order_by(PersonLink.taiga_user_id).all()
    self.assertEqual([(p.taiga_user_id, p.confirmed, p.source) for p in people],
                     [(5, True, "seed"), (6, True, "seed")])
    self.assertIn("zgodny e-mail", people[1].note)


if __name__ == "__main__":
  unittest.main()
