# tests/test_wf_forms.py
# testy formularzy Spiff-Arena (JSON Schema + uiSchema dla
# react-jsonschema-form): endpoint formularza zadania, formularz startowy,
# indeks `spec_json` budowany z plików wersji oraz renderowanie szablonów Jinja
# danymi zadania.
from __future__ import annotations

import unittest

from tests.base import ApiTestCase, BPMN_PROCESS

from wf.schemas.wf_schemas import CurrentUser, HumanTaskStatus, ProcessFileType

# Jinja w schemacie: `kwota` pochodzi z danych instancji (startowych).
OCENA_SCHEMA = ('{"type":"object","required":["ocena"],"properties":'
                '{"ocena":{"type":"integer","title":"Ocena (kwota {{ kwota }})"}}}')
OCENA_UI = '{"ui:order":["ocena"],"ocena":{"ui:widget":"radio"}}'
START_SCHEMA = ('{"type":"object","properties":'
                '{"uwagi":{"type":"string","title":"Uwagi do startu"}}}')
START_UI = '{"uwagi":{"ui:autofocus":true}}'
# Poprawny JSON, ale niedomknięty blok Jinja - render musi dać HTTP 422.
BROKEN_SCHEMA = '{"type":"object","properties":{"x":{"title":"{% if kwota %}"}}}'

BPMN_FORMS = """<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"
  xmlns:flowable="http://flowable.org/bpmn"
  xmlns:spiffworkflow="https://spiffworkflow.org/schema/bpmn"
  id="def_1" targetNamespace="urn:galwork:test">
  <bpmn:process id="proc_test" isExecutable="true">
    <bpmn:startEvent id="start_1">
      <bpmn:extensionElements>
        <spiffworkflow:properties>
          <spiffworkflow:property name="formJsonSchemaFilename" value="start.json"/>
          <spiffworkflow:property name="formUiSchemaFilename" value="start.uischema.json"/>
        </spiffworkflow:properties>
      </bpmn:extensionElements>
    </bpmn:startEvent>
    <bpmn:sequenceFlow id="flow_1" sourceRef="start_1" targetRef="task_recenzja"/>
    <bpmn:userTask id="task_recenzja" name="Recenzja" flowable:candidateGroups="recenzenci">
      <bpmn:extensionElements>
        <spiffworkflow:properties>
          <spiffworkflow:property name="formJsonSchemaFilename" value="ocena.json"/>
          <spiffworkflow:property name="formUiSchemaFilename" value="ocena.uischema.json"/>
        </spiffworkflow:properties>
      </bpmn:extensionElements>
    </bpmn:userTask>
    <bpmn:sequenceFlow id="flow_2" sourceRef="task_recenzja" targetRef="task_podpis"/>
    <bpmn:userTask id="task_podpis" name="Podpis" flowable:assignee="jurek"/>
    <bpmn:sequenceFlow id="flow_3" sourceRef="task_podpis" targetRef="end_1"/>
    <bpmn:endEvent id="end_1"/>
  </bpmn:process>
</bpmn:definitions>
"""


class FormSpecUnitTest(unittest.TestCase):
  """Czyste funkcje: klucz z nazwy pliku i indeks formularzy z plików."""

  def test_form_key_strips_spiff_arena_suffixes(self):
    from wf.services.process_service import form_key

    for name, key in (('ocena.json', 'ocena'),
                      ('ocena.uischema.json', 'ocena'),
                      ('ocena.ui.json', 'ocena'),
                      ('ocena_ui.json', 'ocena'),
                      ('ocena.uischema', 'ocena'),
                      ('start.json', 'start'),
                      ('bez_rozszerzenia', 'bez_rozszerzenia')):
      with self.subTest(name=name):
        self.assertEqual(form_key(name), key)

  def test_build_spec_json_indexes_form_files(self):
    from wf.services.process_service import build_spec_json

    spec = build_spec_json([
      ('proc_test.bpmn', ProcessFileType.BPMN, BPMN_FORMS),
      ('ocena.json', ProcessFileType.JSON_SCHEMA, OCENA_SCHEMA),
      ('ocena.uischema.json', ProcessFileType.UI_SCHEMA, OCENA_UI),
      ('diagram.dmn', ProcessFileType.DMN, '<definitions/>'),
    ])

    self.assertEqual(spec['forms'], {'ocena': OCENA_SCHEMA})
    self.assertEqual(spec['uischemas'], {'ocena': OCENA_UI})
    self.assertEqual(spec['form_files'], {
      'start_1': {'schema': 'start.json', 'ui': 'start.uischema.json'},
      'task_recenzja': {'schema': 'ocena.json', 'ui': 'ocena.uischema.json'},
    })
    self.assertEqual(spec['start_form'], {'task_id': 'start_1'})

  def test_build_spec_json_without_forms_has_no_start_form(self):
    from wf.services.process_service import build_spec_json

    spec = build_spec_json([('proc_test.bpmn', ProcessFileType.BPMN, BPMN_PROCESS)])
    self.assertEqual(spec, {'forms': {}, 'uischemas': {}})


class TaskFormTest(ApiTestCase):
  """GET /v1/wf/tasks/{id}/form dla formularza ze `spec_json` (bez plików)."""

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
    self.create_process_model()
    instance = self.client.post('/v1/wf/process-instances', json={
      'process_model_key': 'test-proces',
      'title': 'Sprawa testowa',
      'data': {'kwota': 100},
    })
    self.assertEqual(instance.status_code, 201, instance.text)
    self.instance = instance.json()
    tasks = self.client.get('/v1/wf/tasks/my').json()
    self.task = tasks['items'][0]

  def test_form_returns_schema_data_and_lock_version(self):
    response = self.client.get(f"/v1/wf/tasks/{self.task['id']}/form")
    self.assertEqual(response.status_code, 200, response.text)
    body = response.json()

    self.assertEqual(body['task_name'], 'Recenzja')
    self.assertEqual(body['json_schema']['required'], ['ocena'])
    self.assertEqual(body['json_schema']['properties']['ocena']['type'], 'integer')
    # Bez plików UI nie ma uiSchema - klient używa własnych domyślnych widgetów.
    self.assertEqual(body['ui_schema'], {})
    self.assertEqual(body['form_data'], {'kwota': 100})
    self.assertEqual(body['documents'], [])
    # `process_lock_version` potrzebne do /complete (optimistic locking).
    self.assertEqual(body['process_lock_version'], self.instance['lock_version'])

  def test_form_for_task_without_schema_is_empty(self):
    lock_version = self.instance['lock_version']
    done = self.client.post(f"/v1/wf/tasks/{self.task['id']}/complete", json={
      'data': {'ocena': 5}, 'expected_lock_version': lock_version})
    self.assertEqual(done.status_code, 200, done.text)

    second = self.client.get('/v1/wf/tasks/my').json()['items'][0]
    response = self.client.get(f"/v1/wf/tasks/{second['id']}/form")
    self.assertEqual(response.status_code, 200, response.text)
    body = response.json()
    self.assertEqual(body['json_schema'], {})
    self.assertEqual(body['task_name'], 'Podpis')

  def test_form_is_forbidden_without_candidate_group(self):
    self.current_user = self.current_user.model_copy(update={'groups': frozenset()})
    response = self.client.get(f"/v1/wf/tasks/{self.task['id']}/form")
    self.assertEqual(response.status_code, 403, response.text)

  def test_form_unknown_task_is_404(self):
    response = self.client.get('/v1/wf/tasks/00000000-0000-0000-0000-000000000000/form')
    self.assertEqual(response.status_code, 404, response.text)


class StartFormTest(ApiTestCase):
  """GET /v1/wf/process-models/{key}/start-form bez plików formularzy."""

  def prepare(self) -> None:
    self.user = self.create_user()
    self.current_user = CurrentUser(
      id=self.user.id,
      sub=self.user.keycloak_sub,
      username=self.user.name,
      email=self.user.email,
      display_name=self.user.name,
      groups=frozenset({'recenzenci', 'wf-admin'}),
    )
    self.create_process_model()

  def test_start_form_without_form_files_is_empty(self):
    response = self.client.get('/v1/wf/process-models/test-proces/start-form')
    self.assertEqual(response.status_code, 200, response.text)
    body = response.json()
    self.assertEqual(body['process_model_key'], 'test-proces')
    self.assertEqual(body['json_schema'], {})
    self.assertEqual(body['ui_schema'], {})
    self.assertIsNone(body['bpmn_task_id'])

  def test_start_form_requires_published_version(self):
    response = self.client.get('/v1/wf/process-models/brak-procesu/start-form')
    self.assertEqual(response.status_code, 409, response.text)

  def test_start_form_requires_token(self):
    for sub_app in self.sub_apps:
      sub_app.dependency_overrides.clear()
    response = self.client.get('/v1/wf/process-models/test-proces/start-form')
    self.assertEqual(response.status_code, 401, response.text)


class FormFilesTest(ApiTestCase):
  """Formularze z plików wersji: rozszerzenia BPMN + Jinja + walidacja."""

  def prepare(self) -> None:
    self.user = self.create_user()
    self.current_user = CurrentUser(
      id=self.user.id,
      sub=self.user.keycloak_sub,
      username=self.user.name,
      email=self.user.email,
      display_name=self.user.name,
      groups=frozenset({'recenzenci', 'wf-admin'}),
    )

  def create_form_model(self, schema: str = OCENA_SCHEMA) -> dict:
    """Model procesu przez API: BPMN z rozszerzeniami + pliki formularzy."""
    created = self.client.post('/v1/wf/process-models', json={
      'key': 'form-proces', 'name': 'Proces z formularzem'})
    self.assertEqual(created.status_code, 201, created.text)

    version = self.client.post(
      f"/v1/wf/process-models/{created.json()['id']}/versions", json={
        'bpmn_process_id': 'proc_test',
        'files': [
          {'file_name': 'proc_test.bpmn', 'file_type': 'bpmn', 'content': BPMN_FORMS},
          {'file_name': 'ocena.json', 'file_type': 'json_schema', 'content': schema},
          {'file_name': 'ocena.uischema.json', 'file_type': 'ui_schema',
           'content': OCENA_UI},
          {'file_name': 'start.json', 'file_type': 'json_schema',
           'content': START_SCHEMA},
          {'file_name': 'start.uischema.json', 'file_type': 'ui_schema',
           'content': START_UI},
        ]})
    self.assertEqual(version.status_code, 201, version.text)

    published = self.client.post(
      f"/v1/wf/process-models/{created.json()['id']}/versions/"
      f"{version.json()['id']}/publish")
    self.assertEqual(published.status_code, 200, published.text)

    instance = self.client.post('/v1/wf/process-instances', json={
      'process_model_key': 'form-proces',
      'title': 'Sprawa z formularzem',
      'data': {'kwota': 100},
    })
    self.assertEqual(instance.status_code, 201, instance.text)
    return instance.json()

  def first_task(self) -> dict:
    tasks = self.client.get('/v1/wf/tasks/my').json()
    self.assertEqual(tasks['total'], 1, tasks)
    return tasks['items'][0]

  def test_version_stores_form_index_in_spec_json(self):
    self.create_form_model()
    from wf.models import ProcessModelVersion
    version = self.db.query(ProcessModelVersion).one()
    self.assertEqual(version.spec_json['forms']['ocena'], OCENA_SCHEMA)
    self.assertEqual(version.spec_json['uischemas']['ocena'], OCENA_UI)
    self.assertEqual(version.spec_json['form_files']['task_recenzja'],
                     {'schema': 'ocena.json', 'ui': 'ocena.uischema.json'})
    self.assertEqual(version.spec_json['start_form'], {'task_id': 'start_1'})

  def test_get_file_content_returns_raw_content(self):
    """2026-10-17: GET .../versions/{v}/files/{f} - `content` nie jest
    w `ProcessModelFileRead` (listy), tylko w `ProcessModelFileContent`
    (ten endpoint); potrzebne do podglądu formularza bez startowania
    procesu (galwork-dashboard: Narzędzia twórcy -> Testuj formularz)."""
    self.create_form_model()
    from wf.models import ProcessModelVersion
    version = self.db.query(ProcessModelVersion).one()
    schema_file = next(f for f in version.files if f.file_name == 'ocena.json')

    response = self.client.get(
      f"/v1/wf/process-models/{version.process_model_id}/versions/"
      f"{version.id}/files/{schema_file.id}")
    self.assertEqual(response.status_code, 200, response.text)
    body = response.json()
    self.assertEqual(body['file_name'], 'ocena.json')
    self.assertEqual(body['content'], OCENA_SCHEMA)

  def test_get_file_content_unknown_file_is_404(self):
    self.create_form_model()
    from wf.models import ProcessModelVersion
    version = self.db.query(ProcessModelVersion).one()

    response = self.client.get(
      f"/v1/wf/process-models/{version.process_model_id}/versions/"
      f"{version.id}/files/00000000-0000-0000-0000-000000000000")
    self.assertEqual(response.status_code, 404, response.text)

  def test_task_form_is_rendered_with_task_data(self):
    self.create_form_model()
    task = self.first_task()

    response = self.client.get(f"/v1/wf/tasks/{task['id']}/form")
    self.assertEqual(response.status_code, 200, response.text)
    body = response.json()

    # Szablon Jinja w schemacie renderowany danymi zadania (kwota=100).
    self.assertEqual(body['json_schema']['properties']['ocena']['title'],
                     'Ocena (kwota 100)')
    self.assertEqual(body['json_schema']['required'], ['ocena'])
    self.assertEqual(body['ui_schema']['ui:order'], ['ocena'])
    self.assertEqual(body['ui_schema']['ocena'], {'ui:widget': 'radio'})
    self.assertEqual(body['form_data'], {'kwota': 100})

  def test_complete_uses_the_same_rendered_schema(self):
    self.create_form_model()
    task = self.first_task()
    lock_version = self.client.get(
      f"/v1/wf/process-instances/{task['process_instance_id']}").json()['lock_version']

    bad = self.client.post(f"/v1/wf/tasks/{task['id']}/complete", json={
      'data': {'komentarz': 'bez oceny'}, 'expected_lock_version': lock_version})
    self.assertEqual(bad.status_code, 422, bad.text)

    done = self.client.post(f"/v1/wf/tasks/{task['id']}/complete", json={
      'data': {'ocena': 5}, 'expected_lock_version': lock_version})
    self.assertEqual(done.status_code, 200, done.text)
    self.assertEqual(done.json()['status'], HumanTaskStatus.COMPLETED)

  def test_start_form_is_read_from_start_event(self):
    self.create_form_model()
    response = self.client.get('/v1/wf/process-models/form-proces/start-form')
    self.assertEqual(response.status_code, 200, response.text)
    body = response.json()
    self.assertEqual(body['bpmn_task_id'], 'start_1')
    self.assertEqual(body['json_schema']['properties']['uwagi']['title'],
                     'Uwagi do startu')
    self.assertEqual(body['ui_schema'], {'uwagi': {'ui:autofocus': True}})
    self.assertEqual(body['form_data'], {})

  def test_broken_jinja_template_is_422(self):
    self.create_form_model(schema=BROKEN_SCHEMA)
    task = self.first_task()

    response = self.client.get(f"/v1/wf/tasks/{task['id']}/form")
    self.assertEqual(response.status_code, 422, response.text)

    # Ten sam błąd przy wykonaniu zadania nie może być 500.
    lock_version = self.client.get(
      f"/v1/wf/process-instances/{task['process_instance_id']}").json()['lock_version']
    done = self.client.post(f"/v1/wf/tasks/{task['id']}/complete", json={
      'data': {'ocena': 5}, 'expected_lock_version': lock_version})
    self.assertEqual(done.status_code, 422, done.text)
