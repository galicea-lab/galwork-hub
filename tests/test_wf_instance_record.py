# 2026-11-22 (H4): karta obiektu - GET /process-instances/{id}/record.
from __future__ import annotations

import json
import unittest

from tests.base import ApiTestCase
from tests.test_wf_instance_metadata import NS, _props
from wf.models import HumanTask
from wf.schemas.wf_schemas import CurrentUser

BPMN = f'''<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions {NS} xmlns:flowable="http://flowable.org/bpmn" id="d" targetNamespace="x">
  <bpmn:process id="Process_Karta" isExecutable="true">
    <bpmn:startEvent id="s" name="Start">{_props('karta')}</bpmn:startEvent>
    <bpmn:sequenceFlow id="f1" sourceRef="s" targetRef="t" />
    <bpmn:userTask id="t" name="Rejestr" flowable:candidateGroups="rej">{_props('karta')}</bpmn:userTask>
    <bpmn:sequenceFlow id="f2" sourceRef="t" targetRef="e" />
    <bpmn:endEvent id="e" />
  </bpmn:process>
</bpmn:definitions>'''
SCHEMA = {'type': 'object', 'required': ['Nazwa'], 'properties': {
    'Nazwa': {'type': 'string', 'title': 'Nazwa', 'x-permissions': {'Start': 'editable', 'Rejestr': 'editable'}},
    'Szczegóły': {'type': 'object', 'title': 'Szczegóły',
                  'x-permissions': {'Start': 'editable', 'Rejestr': 'editable'},
                  'properties': {'Opis': {'type': 'string', 'title': 'Opis'}}},
    'Uwagi': {'type': 'string', 'title': 'Uwagi', 'x-permissions': {'Start': 'none', 'Rejestr': 'editable'}},
    'Tajne': {'type': 'string', 'title': 'Tajne', 'x-permissions': {'Start': 'editable', 'Rejestr': 'none'}}}}
UI = {'ui:order': ['Nazwa', 'Szczegóły', 'Uwagi', 'Tajne'], 'Uwagi': {'ui:widget': 'textarea'}}
NO_FORM = BPMN.replace('Process_Karta', 'Process_Bez').replace(_props('karta'), '')


class InstanceRecordTest(ApiTestCase):

    def prepare(self) -> None:
        self.users = {n: self.create_user(email=f'{n}@example.com', name=n, keycloak_sub=f'sub-{n}')
                      for n in ('admin', 'autor', 'rej1', 'obcy')}
        self.act('admin', 'wf-admin')
        galwork = {'record_step': 'Rejestr', 'edit_step': 'Rejestr'}
        self.publish('karta', 'Process_Karta', BPMN, galwork=galwork, forms=True)
        self.publish('karta-start', 'Process_Karta', BPMN, forms=True)
        self.publish('bez', 'Process_Bez', NO_FORM)

    def act(self, name: str, *groups: str) -> None:
        user = self.users[name]
        self.current_user = CurrentUser(id=user.id, sub=user.keycloak_sub, username=name, email=user.email,
                                        display_name=name, groups=frozenset(groups))

    def publish(self, key, pid, bpmn, *, galwork=None, forms=False):
        files = [{'file_name': f'{key}.bpmn', 'file_type': 'bpmn', 'content': bpmn}]
        if galwork:
            files.append({'file_name': 'process_model.json', 'file_type': 'other',
                          'content': json.dumps({'galwork': galwork})})
        if forms:
            files += [{'file_name': 'karta-schema.json', 'file_type': 'json_schema', 'content': json.dumps(SCHEMA)},
                      {'file_name': 'karta-uischema.json', 'file_type': 'ui_schema', 'content': json.dumps(UI)}]
        model = self.client.post('/v1/wf/process-models', json={'key': key, 'name': key}).json()
        version = self.client.post(f"/v1/wf/process-models/{model['id']}/versions",
                                   json={'bpmn_process_id': pid, 'files': files})
        if version.status_code == 201:
            self.client.post(f"/v1/wf/process-models/{model['id']}/versions/{version.json()['id']}/publish")
        return version

    def start(self, key: str, data: dict | None = None) -> str:
        response = self.client.post('/v1/wf/process-instances', json={
            'process_model_key': key, 'title': 'Wpis', 'data': data or {}})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()['id']

    def record(self, instance_id: str, status: int = 200) -> dict:
        response = self.client.get(f'/v1/wf/process-instances/{instance_id}/record')
        self.assertEqual(response.status_code, status, response.text)
        return response.json()

    def test_record_step_filters_fields_and_data(self):
        self.act('autor')
        instance = self.start('karta', {'Nazwa': 'ERPNext', 'Szczegóły': {'Opis': 'księgi', 'Obce': 'x'},
                                         'Tajne': 'hasło', 'Poza schematem': 1})
        record = self.record(instance)
        self.assertEqual(record['record_step'], 'Rejestr')
        self.assertTrue(record['has_form'])
        self.assertEqual(list(record['json_schema']['properties']), ['Nazwa', 'Szczegóły', 'Uwagi'])
        self.assertTrue(record['json_schema']['readOnly'])
        self.assertNotIn('required', record['json_schema'])
        self.assertTrue(record['ui_schema']['ui:readonly'])
        self.assertEqual(record['ui_schema']['ui:submitButtonOptions'], {'norender': True})
        self.assertEqual(record['ui_schema']['Uwagi'], {'ui:widget': 'textarea'})
        # pole `none` w kroku karty i dane spoza schematu nie wychodzą do przeglądarki
        self.assertEqual(record['form_data'], {'Nazwa': 'ERPNext', 'Szczegóły': {'Opis': 'księgi'}})
        self.assertIsNone(record['edit_task_id'])      # autor nie jest kandydatem zadania
        self.assertEqual(record['prints'][0]['is_default'], True)

    def test_edit_task_for_candidate_and_current_data(self):
        self.act('autor')
        instance = self.start('karta', {'Nazwa': 'ERPNext'})
        self.act('rej1', 'rej')
        record = self.record(instance)
        self.db.expire_all()
        task = next(t for t in self.db.query(HumanTask).all() if str(t.process_instance_id) == instance)
        self.assertEqual((record['edit_task_id'], record['edit_task_name']), (str(task.id), 'Rejestr'))
        lock = self.client.get(f'/v1/wf/process-instances/{instance}').json()['lock_version']
        done = self.client.post(f'/v1/wf/tasks/{task.id}/complete',
                                json={'data': {'Nazwa': 'ERPNext', 'Uwagi': 'po przeglądzie'},
                                      'expected_lock_version': lock})
        self.assertEqual(done.status_code, 200, done.text)
        record = self.record(instance)
        self.assertEqual(record['status'], 'completed')
        self.assertEqual(record['form_data'], {'Nazwa': 'ERPNext', 'Uwagi': 'po przeglądzie'})
        self.assertIsNone(record['edit_task_id'])      # zadanie wykonane

    def test_default_step_is_start_and_no_form(self):
        self.act('autor')
        record = self.record(self.start('karta-start', {'Nazwa': 'Taiga', 'Tajne': 'widoczne w starcie'}))
        self.assertEqual(record['record_step'], 'Start')
        self.assertEqual(list(record['json_schema']['properties']), ['Nazwa', 'Szczegóły', 'Tajne'])
        self.assertEqual(record['form_data'], {'Nazwa': 'Taiga', 'Tajne': 'widoczne w starcie'})
        bare = self.record(self.start('bez'))
        self.assertEqual((bare['has_form'], bare['json_schema'], bare['form_data']), (False, {}, {}))

    def test_access_and_invalid_meta(self):
        self.act('autor')
        instance = self.start('karta', {'Nazwa': 'ERPNext'})
        self.act('obcy')
        self.record(instance, 403)
        self.act('admin', 'wf-admin')
        bad = self.publish('zla-karta', 'Process_Karta', BPMN, galwork={'record_step': ''}, forms=True)
        self.assertEqual(bad.status_code, 422, bad.text)
        self.assertIn('galwork.record_step', bad.text)


if __name__ == '__main__':
    unittest.main()
