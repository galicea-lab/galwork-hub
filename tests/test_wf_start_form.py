# tests/test_wf_start_form.py
# 2026-11-13: formularz na startEvent zamiast pustego zadania "Start"
# (start jednym kliknięciem) - filtr x-permissions dla kroku "Start",
# walidacja danych startowych, dane startowe w danych zadań.
from __future__ import annotations

import json
import unittest

from tests.base import ApiTestCase
from wf.models import HumanTask
from wf.schemas.wf_schemas import CurrentUser

BPMN = '''<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"
  xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
  xmlns:spiffworkflow="http://spiffworkflow.org/bpmn/schema/1.0/core" id="d" targetNamespace="x">
  <bpmn:process id="Process_Wniosek" isExecutable="true">
    <bpmn:startEvent id="StartEvent_main" name="Start">
      <bpmn:extensionElements><spiffworkflow:properties>
        <spiffworkflow:property name="formJsonSchemaFilename" value="wniosek-schema.json" />
        <spiffworkflow:property name="formUiSchemaFilename" value="wniosek-uischema.json" />
      </spiffworkflow:properties></bpmn:extensionElements>
    </bpmn:startEvent>
    <bpmn:sequenceFlow id="f1" sourceRef="StartEvent_main" targetRef="Task_02" />
    <bpmn:userTask id="Task_02" name="Weryfikacja">
      <bpmn:extensionElements><spiffworkflow:properties>
        <spiffworkflow:property name="formJsonSchemaFilename" value="wniosek-schema.json" />
        <spiffworkflow:property name="formUiSchemaFilename" value="wniosek-uischema.json" />
      </spiffworkflow:properties></bpmn:extensionElements>
    </bpmn:userTask>
    <bpmn:sequenceFlow id="f2" sourceRef="Task_02" targetRef="GW" />
    <bpmn:exclusiveGateway id="GW" default="f_b" />
    <bpmn:sequenceFlow id="f_a" sourceRef="GW" targetRef="Task_A">
      <bpmn:conditionExpression xsi:type="bpmn:tFormalExpression">rodzaj == 'A'</bpmn:conditionExpression>
    </bpmn:sequenceFlow>
    <bpmn:sequenceFlow id="f_b" sourceRef="GW" targetRef="Task_B" />
    <bpmn:userTask id="Task_A" name="Ścieżka A" />
    <bpmn:userTask id="Task_B" name="Ścieżka B" />
    <bpmn:sequenceFlow id="f3" sourceRef="Task_A" targetRef="e" />
    <bpmn:sequenceFlow id="f4" sourceRef="Task_B" targetRef="e" />
    <bpmn:endEvent id="e" />
  </bpmn:process>
</bpmn:definitions>'''

SCHEMA = {'type': 'object', 'properties': {
    'rodzaj': {'type': 'string', 'title': 'Rodzaj',
               'x-permissions': {'Start': 'editable', 'Weryfikacja': 'readonly'},
               'x-required-in-steps': ['Start']},
    'opis': {'type': 'string', 'title': 'Opis', 'x-permissions': {'Start': 'editable', 'Weryfikacja': 'readonly'}},
    'ocena': {'type': 'string', 'title': 'Ocena', 'x-permissions': {'Weryfikacja': 'editable'}},
}}


class StartFormTest(ApiTestCase):

    def prepare(self) -> None:
        user = self.create_user()
        self.current_user = CurrentUser(
            id=user.id, sub=user.keycloak_sub, username=user.name, email=user.email,
            display_name=user.name, groups=frozenset({'wf-admin'}))
        model = self.client.post('/v1/wf/process-models', json={'key': 'wniosek', 'name': 'Wniosek'}).json()
        version = self.client.post(f"/v1/wf/process-models/{model['id']}/versions", json={
            'bpmn_process_id': 'Process_Wniosek', 'files': [
                {'file_name': 'wniosek.bpmn', 'file_type': 'bpmn', 'content': BPMN},
                {'file_name': 'wniosek-schema.json', 'file_type': 'json_schema', 'content': json.dumps(SCHEMA)},
                {'file_name': 'wniosek-uischema.json', 'file_type': 'ui_schema',
                 'content': json.dumps({'ui:order': ['rodzaj', 'opis', 'ocena']})}]}).json()
        self.client.post(f"/v1/wf/process-models/{model['id']}/versions/{version['id']}/publish")

    def _start(self, data):
        return self.client.post('/v1/wf/process-instances', json={
            'process_model_key': 'wniosek', 'title': 'W', 'data': data})

    def _open(self, instance_id) -> HumanTask:
        self.db.expire_all()
        return [t for t in self.db.query(HumanTask).all()
                if str(t.process_instance_id) == instance_id and t.status.value == 'open'][0]

    def test_start_form_is_filtered_for_start_step(self):
        form = self.client.get('/v1/wf/process-models/wniosek/start-form')
        self.assertEqual(form.status_code, 200, form.text)
        schema = form.json()['json_schema']
        self.assertEqual(set(schema['properties']), {'rodzaj', 'opis'})  # bez "ocena"
        self.assertEqual(schema['required'], ['rodzaj'])
        self.assertNotIn('x-permissions', json.dumps(schema))
        self.assertEqual(form.json()['ui_schema']['ui:order'], ['rodzaj', 'opis'])

    def test_start_data_is_validated(self):
        response = self._start({'opis': 'bez rodzaju'})
        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn('rodzaj', response.json()['detail'])

    def test_start_data_reaches_tasks_and_gateway(self):
        response = self._start({'rodzaj': 'A', 'opis': 'pilne'})
        self.assertEqual(response.status_code, 201, response.text)
        instance_id = response.json()['id']
        task = self._open(instance_id)
        self.assertEqual(task.name, 'Weryfikacja')  # bez zadania "Start"
        form = self.client.get(f'/v1/wf/tasks/{task.id}/form').json()
        self.assertEqual(form['form_data']['opis'], 'pilne')
        self.assertTrue(form['json_schema']['properties']['rodzaj']['readOnly'])
        lock = self.client.get(f'/v1/wf/process-instances/{instance_id}').json()['lock_version']
        done = self.client.post(f'/v1/wf/tasks/{task.id}/complete',
                                json={'data': {'ocena': 'ok'}, 'expected_lock_version': lock})
        self.assertEqual(done.status_code, 200, done.text)
        # warunek bramki widzi dane startowe (rodzaj == 'A')
        self.assertEqual(self._open(instance_id).name, 'Ścieżka A')


if __name__ == '__main__':
    unittest.main()
