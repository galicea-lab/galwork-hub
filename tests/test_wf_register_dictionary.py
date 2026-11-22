# 2026-11-22: słownik zasilany z rejestru (`galwork.dictionary` w process_model.json):
# opcje = aktywne wpisy rejestru, value = id wpisu, label = kolumna metadanych.
from __future__ import annotations

import json
import unittest

from tests.base import ApiTestCase
from tests.test_wf_instance_metadata import NS, _props
from wf.models import HumanTask
from wf.schemas.wf_schemas import CurrentUser

BPMN = f'''<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions {NS} xmlns:flowable="http://flowable.org/bpmn" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" id="d" targetNamespace="x">
  <bpmn:process id="Process_Rej" isExecutable="true">
    <bpmn:startEvent id="s" name="Nowy wpis">{_props('rej')}</bpmn:startEvent>
    <bpmn:sequenceFlow id="f1" sourceRef="s" targetRef="t1" />
    <bpmn:userTask id="t1" name="Przegląd wpisu" flowable:candidateGroups="it">{_props('rej')}</bpmn:userTask>
    <bpmn:sequenceFlow id="f2" sourceRef="t1" targetRef="t2" />
    <bpmn:userTask id="t2" name="Wpis w rejestrze" flowable:candidateGroups="it">{_props('rej')}</bpmn:userTask>
    <bpmn:sequenceFlow id="f3" sourceRef="t2" targetRef="gw" />
    <bpmn:exclusiveGateway id="gw" default="Flow_Zapisz" />
    <bpmn:sequenceFlow id="Flow_Zapisz" name="Zapisz zmiany" sourceRef="gw" targetRef="t2" />
    <bpmn:sequenceFlow id="Flow_Wycofaj" name="Wycofaj z rejestru" sourceRef="gw" targetRef="e">
      <bpmn:conditionExpression xsi:type="bpmn:tFormalExpression">chosen_path == 'Flow_Wycofaj'</bpmn:conditionExpression>
    </bpmn:sequenceFlow>
    <bpmn:endEvent id="e" />
  </bpmn:process>
</bpmn:definitions>'''
SCHEMA = {'type': 'object', 'properties': {'nazwa': {'type': 'string', 'title': 'Nazwa'},
                                           'hosting': {'type': 'string', 'title': 'Hosting'}}}
PATHS = [{'key': 'nazwa', 'path': 'nazwa'}, {'key': 'hosting', 'path': 'hosting'}]
DICTIONARY = {'slug': 'systemy-test', 'title': 'Systemy', 'label': 'nazwa', 'detail': ['hosting'],
              'active_step': 'Wpis w rejestrze'}


class RegisterDictionaryTest(ApiTestCase):

    def prepare(self) -> None:
        self.admin = self.create_user(email='a@example.com', name='admin', keycloak_sub='sub-a')
        self.other = self.create_user(email='o@example.com', name='obcy', keycloak_sub='sub-o')
        self.act(self.admin, 'wf-admin', 'it')
        response = self.publish('rej', galwork={'dictionary': DICTIONARY})
        self.assertEqual(response.status_code, 201, response.text)

    def act(self, user, *groups):
        self.current_user = CurrentUser(id=user.id, sub=user.keycloak_sub, username=user.name, email=user.email,
                                        display_name=user.name, groups=frozenset(groups))

    def publish(self, key, *, galwork=None, paths=PATHS):
        meta = {'metadata_extraction_paths': paths, **({'galwork': galwork} if galwork else {})}
        files = [{'file_name': f'{key}.bpmn', 'file_type': 'bpmn', 'content': BPMN},
                 {'file_name': 'process_model.json', 'file_type': 'other', 'content': json.dumps(meta)},
                 {'file_name': 'rej-schema.json', 'file_type': 'json_schema', 'content': json.dumps(SCHEMA)},
                 {'file_name': 'rej-uischema.json', 'file_type': 'ui_schema', 'content': '{}'}]
        model = self.client.post('/v1/wf/process-models', json={'key': key, 'name': key}).json()
        version = self.client.post(f"/v1/wf/process-models/{model['id']}/versions",
                                   json={'bpmn_process_id': 'Process_Rej', 'files': files})
        if version.status_code == 201:
            self.client.post(f"/v1/wf/process-models/{model['id']}/versions/{version.json()['id']}/publish")
        return version

    def entry(self, name: str, hosting: str, *, reviewed: bool = True, key: str = 'rej') -> str:
        response = self.client.post('/v1/wf/process-instances', json={
            'process_model_key': key, 'title': f'Wpis {name}', 'data': {'nazwa': name, 'hosting': hosting}})
        self.assertEqual(response.status_code, 201, response.text)
        instance_id = response.json()['id']
        if reviewed:
            self.complete(instance_id, {'nazwa': name, 'hosting': hosting})
        return instance_id

    def complete(self, instance_id: str, data: dict) -> None:
        self.db.expire_all()
        task = next(t for t in self.db.query(HumanTask).all()
                    if str(t.process_instance_id) == instance_id and t.status.value == 'open')
        lock = self.client.get(f'/v1/wf/process-instances/{instance_id}').json()['lock_version']
        response = self.client.post(f'/v1/wf/tasks/{task.id}/complete',
                                    json={'data': data, 'expected_lock_version': lock})
        self.assertEqual(response.status_code, 200, response.text)

    def options(self, slug: str = 'systemy-test', status: int = 200, **params):
        response = self.client.get(f'/v1/wf/dictionaries/{slug}', params=params)
        self.assertEqual(response.status_code, status, response.text)
        return response.json()

    def test_options_are_active_register_entries(self):
        taiga = self.entry('Taiga', 'SaaS')
        erp = self.entry('ERPNext', 'hosting')
        self.entry('Szkic', 'SaaS', reviewed=False)                 # przed przeglądem - nieaktywny
        withdrawn = self.entry('Stary', 'serwer')
        self.complete(withdrawn, {'chosen_path': 'Flow_Wycofaj'})    # wycofany z rejestru
        self.assertEqual(self.options(), [{'value': erp, 'label': 'ERPNext (hosting)'},
                                          {'value': taiga, 'label': 'Taiga (SaaS)'}])
        self.assertEqual(self.options(q='TAIG'), [{'value': taiga, 'label': 'Taiga (SaaS)'}])
        self.assertEqual(self.options(value=erp), [{'value': erp, 'label': 'ERPNext (hosting)'}])
        self.assertEqual(self.options(value='nie-uuid'), [])

    def test_rename_keeps_value_and_labels_are_shared(self):
        erp = self.entry('ERPNext', 'hosting')
        self.complete(erp, {'nazwa': 'ERPNext produkcja', 'hosting': 'hosting'})   # „Zapisz zmiany”
        self.act(self.other)                                     # nie uczestniczy w sprawie
        self.assertEqual(self.options(), [{'value': erp, 'label': 'ERPNext produkcja (hosting)'}])
        self.assertEqual(self.client.get(f'/v1/wf/process-instances/{erp}').status_code, 403)

    def test_invalid_declarations(self):
        for n, (galwork, paths) in enumerate((
                ({'dictionary': {**DICTIONARY, 'slug': 'dzialy'}}, PATHS),          # słownik huba
                ({'dictionary': {**DICTIONARY, 'slug': 'Zly Slug'}}, PATHS),
                ({'dictionary': {**DICTIONARY, 'label': 'brak'}}, PATHS),          # brak kolumny
                ({'dictionary': {**DICTIONARY, 'detail': 'hosting'}}, PATHS))):
            response = self.publish(f'zly-{n}', galwork=galwork, paths=paths)
            self.assertEqual(response.status_code, 422, f'{galwork}: {response.text}')
            self.assertIn('galwork.dictionary', response.text)
        self.options('nieznany', status=404)

    def test_duplicate_declaration_is_reported(self):
        self.assertEqual(self.publish('rej-kopia', galwork={'dictionary': DICTIONARY}).status_code, 201)
        detail = self.options(status=502)['detail']
        self.assertIn('rej', detail)
        self.assertIn('rej-kopia', detail)


if __name__ == '__main__':
    unittest.main()
