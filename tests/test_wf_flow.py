# tests/test_wf_flow.py
# test pełnego przebiegu procesu w module wf - od autoryzacji
# (token Keycloak -> users.id) przez uruchomienie silnika BPMN, aż po wykonanie
# zadania i zamknięcie instancji.
from __future__ import annotations

from tests.base import BPMN_PROCESS, ApiTestCase

from wf.schemas.wf_schemas import CurrentUser, HumanTaskStatus, ProcessInstanceStatus


class ProcessFlowTest(ApiTestCase):

  def prepare(self) -> None:
    self.user = self.create_user()
    self.current_user = CurrentUser(
      id=self.user.id,
      sub=self.user.keycloak_sub,
      username=self.user.name,
      email=self.user.email,
      display_name=self.user.name,
      groups=frozenset({'recenzenci'}),
    )

  def start_instance(self) -> dict:
    self.create_process_model()
    response = self.client.post('/v1/wf/process-instances', json={
      'process_model_key': 'test-proces',
      'title': 'Sprawa testowa',
      'business_key': 'TEST/1',
      'data': {'kwota': 100},
    })
    self.assertEqual(response.status_code, 201, response.text)
    return response.json()

  def test_start_creates_state_and_first_task(self):
    instance = self.start_instance()

    self.assertEqual(instance['status'], ProcessInstanceStatus.RUNNING)
    self.assertEqual(instance['started_by']['id'], self.user.id)

    tasks = self.client.get('/v1/wf/tasks/my').json()
    self.assertEqual(tasks['total'], 1)
    self.assertEqual(tasks['items'][0]['name'], 'Recenzja')

    from wf.models import ProcessInstanceState
    state = self.db.query(ProcessInstanceState).one()
    self.assertEqual(state.serializer_version,
                     'spiff-bpmn-3.2-default-registry')
    self.assertIn('data', state.state)

  def test_complete_task_advances_and_validates_form(self):
    instance = self.start_instance()
    task = self.client.get('/v1/wf/tasks/my').json()['items'][0]
    lock_version = self.client.get(
      f"/v1/wf/process-instances/{instance['id']}").json()['lock_version']

    # JSON Schema z ProcessModelVersion.spec_json wymaga pola `ocena`
    bad = self.client.post(f"/v1/wf/tasks/{task['id']}/complete", json={
      'data': {'komentarz': 'bez oceny'}, 'expected_lock_version': lock_version})
    self.assertEqual(bad.status_code, 422, bad.text)

    done = self.client.post(f"/v1/wf/tasks/{task['id']}/complete", json={
      'data': {'ocena': 5}, 'expected_lock_version': lock_version})
    self.assertEqual(done.status_code, 200, done.text)
    self.assertEqual(done.json()['status'], HumanTaskStatus.COMPLETED)

    # Otworzyło się kolejne zadanie (Podpis - przypisane do jureka)
    names = [item['name'] for item in self.client.get('/v1/wf/tasks/my').json()['items']]
    self.assertEqual(names, ['Podpis'])

    # lock_version pobieramy tuż przed każdą mutacją (tak jak
    # robi klient); `version_id_col` zwiększa się o 1 na flush, więc
    # arytmetyczne `+= 1` w teście byłoby kruche.
    second = self.client.get('/v1/wf/tasks/my').json()['items'][0]
    lock_version = self.client.get(
      f"/v1/wf/process-instances/{instance['id']}").json()['lock_version']
    finished = self.client.post(f"/v1/wf/tasks/{second['id']}/complete", json={
      'data': {}, 'expected_lock_version': lock_version})
    self.assertEqual(finished.status_code, 200, finished.text)

    instance_after = self.client.get(f"/v1/wf/process-instances/{instance['id']}").json()
    self.assertEqual(instance_after['status'], ProcessInstanceStatus.COMPLETED)

  def test_task_outside_groups_is_forbidden(self):
    instance = self.start_instance()
    task = self.client.get('/v1/wf/tasks/my').json()['items'][0]

    # Użytkownik bez grupy "recenzenci"
    self.current_user = self.current_user.model_copy(update={'groups': frozenset()})
    lock_version = self.client.get(
      f"/v1/wf/process-instances/{instance['id']}").json()['lock_version']
    response = self.client.post(f"/v1/wf/tasks/{task['id']}/complete", json={
      'data': {'ocena': 3}, 'expected_lock_version': lock_version})
    self.assertEqual(response.status_code, 403, response.text)

  def test_missing_published_version_is_conflict(self):
    response = self.client.post('/v1/wf/process-instances', json={
      'process_model_key': 'brak-procesu', 'title': 'X'})
    self.assertEqual(response.status_code, 409, response.text)

  def test_endpoints_require_token(self):
    """Bez zalogowania (bez podmiany zależności) endpoint musi zwrócić 401."""
    for sub_app in self.sub_apps:
      sub_app.dependency_overrides.clear()
    response = self.client.post('/v1/wf/process-instances', json={
      'process_model_key': 'test-proces', 'title': 'X'})
    self.assertEqual(response.status_code, 401, response.text)

  def test_process_model_write_requires_admin_group(self):
    """2026-10-05: odczyt definicji - dla zalogowanego, zapis - tylko dla
    grupy z keycloak.admin_groups (wf/dependencies.py: require_process_admin)."""
    payload = {'key': 'inny-proces', 'name': 'Inny proces'}

    forbidden = self.client.post('/v1/wf/process-models', json=payload)
    self.assertEqual(forbidden.status_code, 403, forbidden.text)

    self.current_user = self.current_user.model_copy(
      update={'groups': frozenset({'recenzenci', 'wf-admin'})})
    created = self.client.post('/v1/wf/process-models', json=payload)
    self.assertEqual(created.status_code, 201, created.text)
    self.assertEqual(created.json()['key'], 'inny-proces')

    listed = self.client.get('/v1/wf/process-models')
    self.assertEqual(listed.status_code, 200, listed.text)
    self.assertEqual({item['key'] for item in listed.json()}, {'inny-proces'})

  def test_republishing_retires_previous_version(self):
    """2026-10-15: regresja - `uq_pmv_one_published` (częściowy indeks
    unikalny, zob. wf/models/wf.py) jest sprawdzany przez bazę przy KAŻDYM
    UPDATE, nie dopiero przy commicie. Publikowanie drugiej wersji tego
    samego procesu bez flusha między "cofnij poprzednią" i "opublikuj nową"
    (wf/routers/process_model_router.py::publish_process_model_version)
    dawało IntegrityError, bo SQLAlchemy mogło wysłać te dwa UPDATE-y w
    złej kolejności. Nieszczęśliwie: ten test (SQLite w pamięci,
    tests/base.py) przechodzi RÓWNIEŻ bez flusha - SQLite nie odtwarza
    timingu, na którym wywalał się prawdziwy Postgres. Trzymamy ten test
    jako sprawdzenie stanu końcowego republikacji (poprzednia wersja
    retired, nowa published), a nie jako pełną gwarancję przed regresją tego
    konkretnego błędu - tamten został potwierdzony i poprawiony wyłącznie
    na żywej bazie Postgres."""
    model_id = self.create_process_model()  # wersja 1, już published
    self.current_user = self.current_user.model_copy(
      update={'groups': frozenset({'recenzenci', 'wf-admin'})})

    version2 = self.client.post(
      f"/v1/wf/process-models/{model_id}/versions", json={
        'bpmn_process_id': 'proc_test',
        'files': [{'file_name': 'proc_test.bpmn', 'file_type': 'bpmn',
                   'content': BPMN_PROCESS}],
      })
    self.assertEqual(version2.status_code, 201, version2.text)

    published = self.client.post(
      f"/v1/wf/process-models/{model_id}/versions/{version2.json()['id']}/publish")
    self.assertEqual(published.status_code, 200, published.text)
    self.assertEqual(published.json()['status'], 'published')

    # Nie przez GET .../versions - response_model waliduje `content_hash`
    # wzorcem sha256, a `create_process_model()` (tests/base.py) wpisuje
    # fikcyjny hash dla wersji 1; sprawdzamy stan wprost w bazie.
    from wf.models import ProcessModelVersion
    rows = self.db.query(ProcessModelVersion).filter_by(process_model_id=model_id).all()
    by_version = {row.version: row.status for row in rows}
    self.assertEqual(by_version, {1: 'retired', 2: 'published'})

  def test_process_model_endpoints_require_token(self):
    """2026-10-05: definicje procesów nie mogą być anonimowe (TODO.md pkt 4)."""
    for sub_app in self.sub_apps:
      sub_app.dependency_overrides.clear()
    for method, url in (
      ('get', '/v1/wf/process-models'),
      ('post', '/v1/wf/process-models'),
      ('get', '/v1/wf/tasks/my'),
      ('get', '/v1/wf/'),
    ):
      with self.subTest(url=url):
        response = getattr(self.client, method)(url)
        self.assertEqual(response.status_code, 401, f'{url}: {response.text}')

  def test_suspend_and_resume(self):
    instance = self.start_instance()
    lock_version = instance['lock_version']
    suspended = self.client.post(
      f"/v1/wf/process-instances/{instance['id']}/suspend",
      json={'expected_lock_version': lock_version, 'reason': 'urlop'})
    self.assertEqual(suspended.status_code, 200, suspended.text)
    self.assertEqual(suspended.json()['status'], ProcessInstanceStatus.SUSPENDED)

    resumed = self.client.post(
      f"/v1/wf/process-instances/{instance['id']}/resume",
      json={'expected_lock_version': suspended.json()['lock_version']})
    self.assertEqual(resumed.status_code, 200, resumed.text)
    self.assertEqual(resumed.json()['status'], ProcessInstanceStatus.RUNNING)

  def test_cancelled_instance_has_no_active_tasks(self):
    instance = self.start_instance()
    cancelled = self.client.post(
      f"/v1/wf/process-instances/{instance['id']}/cancel",
      json={'expected_lock_version': instance['lock_version'], 'reason': 'rezygnacja'})
    self.assertEqual(cancelled.status_code, 200, cancelled.text)
    self.assertEqual(cancelled.json()['status'], ProcessInstanceStatus.CANCELLED)

    from wf.models import HumanTask
    statuses = {task.name: task.status for task in self.db.query(HumanTask).all()}
    self.assertEqual(statuses, {'Recenzja': HumanTaskStatus.CANCELLED})


# BEGIN 2026-11-12
BPMN_LANES = """<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"
  id="def_lanes" targetNamespace="urn:galwork:test">
  <bpmn:process id="proc_lanes" isExecutable="true">
    <bpmn:laneSet id="ls_1">
      <bpmn:lane id="lane_dziekanat" name="Dziekanat">
        <bpmn:flowNodeRef>task_lane</bpmn:flowNodeRef>
      </bpmn:lane>
    </bpmn:laneSet>
    <bpmn:startEvent id="start_1"/>
    <bpmn:sequenceFlow id="f1" sourceRef="start_1" targetRef="task_lane"/>
    <bpmn:userTask id="task_lane" name="W torze"/>
    <bpmn:sequenceFlow id="f2" sourceRef="task_lane" targetRef="end_1"/>
    <bpmn:endEvent id="end_1"/>
  </bpmn:process>
</bpmn:definitions>
"""


class LaneTest(ApiTestCase):
  """Zadanie w `bpmn:lane` - SpiffWorkflow 3.x podaje tor jako str."""

  def prepare(self) -> None:
    self.user = self.create_user()
    self.current_user = CurrentUser(
      id=self.user.id, sub=self.user.keycloak_sub, username=self.user.name,
      email=self.user.email, display_name=self.user.name, groups=frozenset())

  def test_task_in_lane_is_created_with_lane_name(self):
    from wf.models import HumanTask
    self.create_process_model(key='tory', bpmn=BPMN_LANES, bpmn_process_id='proc_lanes')
    response = self.client.post('/v1/wf/process-instances', json={
      'process_model_key': 'tory', 'title': 'Tor'})
    self.assertEqual(response.status_code, 201, response.text)
    task = self.db.query(HumanTask).one()
    self.assertEqual(task.lane, 'Dziekanat')
# -END
