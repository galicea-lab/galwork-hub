# 2026-11-22 (H1, H2): metadane instancji z `metadata_extraction_paths`
# i lista spraw z wyszukiwaniem, filtrem po metadanych i sortowaniem.
from __future__ import annotations

import json
import unittest

from tests.base import ApiTestCase
from wf.models import HumanTask
from wf.schemas.wf_schemas import CurrentUser

NS = ('xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" '
      'xmlns:spiffworkflow="http://spiffworkflow.org/bpmn/schema/1.0/core"')


def _props(prefix: str) -> str:
    return (f'<bpmn:extensionElements><spiffworkflow:properties>'
            f'<spiffworkflow:property name="formJsonSchemaFilename" value="{prefix}-schema.json" />'
            f'<spiffworkflow:property name="formUiSchemaFilename" value="{prefix}-uischema.json" />'
            f'</spiffworkflow:properties></bpmn:extensionElements>')


BPMN = f'''<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions {NS} id="d" targetNamespace="x">
  <bpmn:process id="Process_Rej" isExecutable="true">
    <bpmn:startEvent id="s" name="Start">{_props('rej-start')}</bpmn:startEvent>
    <bpmn:sequenceFlow id="f1" sourceRef="s" targetRef="t" />
    <bpmn:userTask id="t" name="Uzupełnienie">{_props('rej-uzup')}</bpmn:userTask>
    <bpmn:sequenceFlow id="f2" sourceRef="t" targetRef="e" />
    <bpmn:endEvent id="e" />
  </bpmn:process>
</bpmn:definitions>'''
START = {'type': 'object', 'properties': {
    'Nazwa': {'type': 'string', 'title': 'Nazwa systemu'},
    'Szczegóły': {'type': 'object', 'properties': {
        'Kategoria': {'type': 'string', 'title': 'Kategoria', 'enum': ['krytyczny', 'zwykły']}}}}}
UZUP = {'type': 'object', 'properties': {
    'Szczegóły': START['properties']['Szczegóły'],
    'Przegląd': {'type': 'string', 'format': 'date', 'title': 'Data przeglądu'}}}
META = {'key': 'rej', 'metadata_extraction_paths': [
    {'key': 'nazwa', 'path': 'Nazwa'}, {'key': 'kategoria', 'path': 'Szczegóły.Kategoria'},
    {'key': 'przeglad', 'path': 'Przegląd'}]}


class InstanceMetadataTest(ApiTestCase):

    def prepare(self) -> None:
        user = self.create_user()
        self.current_user = CurrentUser(id=user.id, sub=user.keycloak_sub, username=user.name,
                                        email=user.email, display_name=user.name,
                                        groups=frozenset({'wf-admin'}))
        response = self.publish(META)
        self.assertEqual(response.status_code, 201, response.text)

    def publish(self, meta: dict, key: str = 'rej'):
        files = [{'file_name': 'rej.bpmn', 'file_type': 'bpmn', 'content': BPMN},
                 {'file_name': 'process_model.json', 'file_type': 'other', 'content': json.dumps(meta)}]
        for prefix, schema in (('rej-start', START), ('rej-uzup', UZUP)):
            files += [{'file_name': f'{prefix}-schema.json', 'file_type': 'json_schema', 'content': json.dumps(schema)},
                      {'file_name': f'{prefix}-uischema.json', 'file_type': 'ui_schema', 'content': '{}'}]
        model = self.client.post('/v1/wf/process-models', json={'key': key, 'name': key}).json()
        version = self.client.post(f"/v1/wf/process-models/{model['id']}/versions",
                                   json={'bpmn_process_id': 'Process_Rej', 'files': files})
        if version.status_code == 201:
            self.client.post(f"/v1/wf/process-models/{model['id']}/versions/{version.json()['id']}/publish")
        return version

    def start(self, name: str, category: str) -> dict:
        response = self.client.post('/v1/wf/process-instances', json={
            'process_model_key': 'rej', 'title': f'System {name}',
            'data': {'Nazwa': name, 'Szczegóły': {'Kategoria': category}}})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def complete_open_task(self, instance_id: str, data: dict) -> None:
        self.db.expire_all()
        task = next(t for t in self.db.query(HumanTask).all()
                    if str(t.process_instance_id) == instance_id and t.status.value == 'open')
        lock = self.client.get(f'/v1/wf/process-instances/{instance_id}').json()['lock_version']
        response = self.client.post(f'/v1/wf/tasks/{task.id}/complete',
                                    json={'data': data, 'expected_lock_version': lock})
        self.assertEqual(response.status_code, 200, response.text)

    def listing(self, **params) -> dict:
        response = self.client.get('/v1/wf/process-instances', params={'process_model_key': 'rej', **params})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_metadata_follow_current_data(self):
        instance = self.start('ERPNext', 'krytyczny')
        self.assertEqual(instance['metadata'], {'nazwa': 'ERPNext', 'kategoria': 'krytyczny'})
        self.complete_open_task(instance['id'], {'Szczegóły': {'Kategoria': 'zwykły'}, 'Przegląd': '2026-11-22'})
        detail = self.client.get(f"/v1/wf/process-instances/{instance['id']}").json()
        self.assertEqual(detail['metadata'],
                         {'nazwa': 'ERPNext', 'kategoria': 'zwykły', 'przeglad': '2026-11-22'})
        self.assertEqual(detail['summary']['Szczegóły'], {'Kategoria': 'krytyczny'})  # dane startowe

    def test_search_filter_and_sort(self):
        erp, taiga, forgejo = (self.start('ERPNext', 'krytyczny'), self.start('Taiga', 'zwykły'),
                               self.start('Forgejo', 'krytyczny'))
        ids = lambda page: [item['id'] for item in page['items']]  # noqa: E731

        self.assertEqual(ids(self.listing(q='taig')), [taiga['id']])            # metadane / tytuł
        self.assertEqual(ids(self.listing(q='SYSTEM erp')), [erp['id']])       # tytuł, bez wielkości liter
        self.assertEqual(ids(self.listing(q='zwykł')), [taiga['id']])          # polskie znaki w metadanych
        page = self.listing(**{'m.kategoria': 'krytyczny', 'sort': 'm.nazwa'})
        self.assertEqual((ids(page), page['total']), ([erp['id'], forgejo['id']], 2))
        self.assertEqual(ids(self.listing(sort='-m.nazwa')), [taiga['id'], forgejo['id'], erp['id']])
        page = self.listing(sort='m.nazwa', limit=1, offset=1)
        self.assertEqual((ids(page), page['total']), ([forgejo['id']], 3))
        bad = self.client.get('/v1/wf/process-instances', params={'sort': 'haslo'})
        self.assertEqual(bad.status_code, 422)

    def test_metadata_fields_describe_columns(self):
        fields = self.client.get('/v1/wf/process-models/rej/metadata-fields').json()
        self.assertEqual(fields, [
            {'key': 'nazwa', 'path': 'Nazwa', 'title': 'Nazwa systemu', 'type': 'string'},
            {'key': 'kategoria', 'path': 'Szczegóły.Kategoria', 'title': 'Kategoria', 'type': 'string',
             'enum': ['krytyczny', 'zwykły']},
            {'key': 'przeglad', 'path': 'Przegląd', 'title': 'Data przeglądu', 'type': 'string',
             'format': 'date'},
        ])

    def test_invalid_paths_are_rejected(self):
        for n, paths in enumerate(([{'key': 'Zła Nazwa', 'path': 'x'}],
                                   [{'key': 'a', 'path': 'x'}, {'key': 'a', 'path': 'y'}],
                                   [{'key': 'a'}], 'Nazwa')):
            response = self.publish({'metadata_extraction_paths': paths}, key=f'zly-{n}')
            self.assertEqual(response.status_code, 422, f'{paths}: {response.text}')
            self.assertIn('metadata_extraction_paths', response.text)


if __name__ == '__main__':
    unittest.main()
