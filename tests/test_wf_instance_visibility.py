# 2026-11-22: lista instancji pokazuje tylko instancje widoczne dla
# użytkownika (wf/services/instance_access.py), `total` liczone w SQL;
# endpointy pojedynczej instancji zwracają 403 osobie, która sprawy nie widzi.
from __future__ import annotations

import json
import os
import tempfile
import unittest

from tests.base import ApiTestCase
from wf.schemas.wf_schemas import CurrentUser

NS = 'xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" xmlns:flowable="http://flowable.org/bpmn"'
TEAM = f'''<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions {NS} id="d" targetNamespace="urn:t">
  <bpmn:process id="proc_team" isExecutable="true">
    <bpmn:startEvent id="s"/><bpmn:sequenceFlow id="f1" sourceRef="s" targetRef="t"/>
    <bpmn:userTask id="t" name="Ocena" flowable:candidateGroups="iod"/>
    <bpmn:sequenceFlow id="f2" sourceRef="t" targetRef="e"/><bpmn:endEvent id="e"/>
  </bpmn:process>
</bpmn:definitions>'''
OPEN = TEAM.replace('proc_team', 'proc_open').replace(' flowable:candidateGroups="iod"', '')


class InstanceVisibilityTest(ApiTestCase):

    def prepare(self) -> None:
        self.users = {name: self.create_user(email=f'{name}@example.com', name=name, keycloak_sub=f'sub-{name}')
                      for name in ('admin', 'autor', 'iod1', 'obcy')}
        self.act('admin', 'wf-admin')
        self.publish('zespol', 'proc_team', TEAM)
        self.publish('otwarty', 'proc_open', OPEN)
        self.publish('jawny', 'proc_team', TEAM, visibility='all')

    def publish(self, key: str, pid: str, bpmn: str, visibility: str | None = None):
        files = [{'file_name': f'{key}.bpmn', 'file_type': 'bpmn', 'content': bpmn}]
        if visibility is not None:
            files.append({'file_name': 'process_model.json', 'file_type': 'other',
                          'content': json.dumps({'galwork': {'visibility': visibility}})})
        model = self.client.post('/v1/wf/process-models', json={'key': key, 'name': key}).json()
        version = self.client.post(f"/v1/wf/process-models/{model['id']}/versions",
                                   json={'bpmn_process_id': pid, 'files': files})
        if version.status_code == 201:
            self.client.post(f"/v1/wf/process-models/{model['id']}/versions/{version.json()['id']}/publish")
        return version

    def act(self, name: str, *groups: str) -> None:
        user = self.users[name]
        self.current_user = CurrentUser(id=user.id, sub=user.keycloak_sub, username=name, email=user.email,
                                        display_name=name, groups=frozenset(groups))

    def start(self, key: str) -> str:
        response = self.client.post('/v1/wf/process-instances', json={'process_model_key': key, 'title': key})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()['id']

    def visible(self, **params) -> tuple[set[str], int]:
        page = self.client.get('/v1/wf/process-instances', params=params).json()
        return {item['id'] for item in page['items']}, page['total']

    def test_visibility_by_role(self):
        self.act('autor')
        team = self.start('zespol')
        self.act('admin', 'wf-admin')
        other = self.start('zespol')
        self.assertEqual(self.visible(), ({team, other}, 2))

        self.act('autor')
        self.assertEqual(self.visible(), ({team}, 1))      # uruchomił
        self.act('iod1', 'iod')
        self.assertEqual(self.visible(), ({team, other}, 2))  # kandydat zadania
        self.act('obcy')
        self.assertEqual(self.visible(), (set(), 0))

    def test_open_task_without_candidates_does_not_reveal_instance(self):
        self.act('autor')
        instance = self.start('otwarty')
        self.act('obcy')
        self.assertEqual(self.visible(), (set(), 0))
        self.assertEqual(self.client.get(f'/v1/wf/process-instances/{instance}').status_code, 403)

    def test_total_respects_filters_and_paging(self):
        self.act('iod1', 'iod')
        ids = {self.start('zespol') for _ in range(3)}
        self.act('obcy')
        self.start('zespol')
        self.act('iod1', 'iod')
        items, total = self.visible(limit=2, process_model_key='zespol')
        self.assertEqual((len(items), total), (2, 4))   # 4. instancja: kandydat zadania
        self.act('autor')
        self.assertEqual(self.visible(limit=2), (set(), 0))
        self.assertTrue(ids)

    def test_single_instance_endpoints_require_access(self):
        self.act('autor')
        instance = self.start('zespol')
        lock = self.client.get(f'/v1/wf/process-instances/{instance}').json()['lock_version']
        self.act('obcy')
        base = f'/v1/wf/process-instances/{instance}'
        for method, url, body in (
                ('get', base, None),
                ('post', f'{base}/wake', None),
                ('post', f'{base}/cancel', {'expected_lock_version': lock}),
                ('post', f'{base}/suspend', {'expected_lock_version': lock}),
                ('get', f'{base}/comments', None),
                ('post', f'{base}/comments', {'body': 'x'}),
                ('get', f'{base}/documents', None),
                ('get', f'{base}/discussions', None),
                ('get', f'{base}/prints', None)):
            response = getattr(self.client, method)(url, **({'json': body} if body is not None else {}))
            self.assertEqual(response.status_code, 403, f'{method} {url}: {response.text}')
        self.assertEqual(self.client.get('/v1/wf/process-instances/00000000-0000-0000-0000-000000000000')
                         .status_code, 404)

        for name, groups in (('autor', ()), ('iod1', ('iod',)), ('admin', ('wf-admin',))):
            self.act(name, *groups)
            self.assertEqual(self.client.get(base).status_code, 200, name)
            self.assertEqual(self.client.get(f'{base}/comments').status_code, 200, name)

    def test_public_register_is_readable_but_not_actionable(self):
        self.act('autor')
        public, private = self.start('jawny'), self.start('zespol')
        lock = self.client.get(f'/v1/wf/process-instances/{public}').json()['lock_version']
        self.act('obcy')
        self.assertEqual(self.visible(), ({public}, 1))
        base = f'/v1/wf/process-instances/{public}'
        self.assertEqual(self.client.get(base).status_code, 200)
        self.assertEqual(self.client.get(f'{base}/comments').status_code, 200)
        for method, url, body in (
                ('post', f'{base}/wake', None),
                ('post', f'{base}/cancel', {'expected_lock_version': lock}),
                ('post', f'{base}/suspend', {'expected_lock_version': lock}),
                ('post', f'{base}/comments', {'body': 'x'}),
                ('get', f'{base}/documents', None),
                ('get', f'{base}/prints', None)):
            response = getattr(self.client, method)(url, **({'json': body} if body is not None else {}))
            self.assertEqual(response.status_code, 403, f'{method} {url}: {response.text}')
        self.assertEqual(self.client.get(f'/v1/wf/process-instances/{private}').status_code, 403)

    def test_invalid_visibility_is_rejected(self):
        self.act('admin', 'wf-admin')
        response = self.publish('zly', 'proc_team', TEAM, visibility='publiczny')
        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn('galwork.visibility', response.text)

    def test_sync_folder_versions_visibility(self):
        from wf.models import ProcessModelVersion
        from wf.services.filesystem_sync import sync_folder
        with tempfile.TemporaryDirectory() as folder:
            with open(os.path.join(folder, 'rej.bpmn'), 'w', encoding='utf-8') as f:
                f.write(TEAM.replace('proc_team', 'proc_rej'))
            meta = {'key': 'rej', 'display_name': 'Rejestr'}
            with open(os.path.join(folder, 'process_model.json'), 'w', encoding='utf-8') as f:
                json.dump(meta, f)
            self.assertEqual(sync_folder(self.db, folder).status, 'draft-created')
            first = self.db.query(ProcessModelVersion).filter_by(bpmn_process_id='proc_rej').one()
            self.assertNotIn('galwork', first.spec_json)
            self.assertNotIn('process_model.json', [f.file_name for f in first.files])

            with open(os.path.join(folder, 'process_model.json'), 'w', encoding='utf-8') as f:
                json.dump({**meta, 'galwork': {'visibility': 'all'}}, f)
            self.assertEqual(sync_folder(self.db, folder).status, 'draft-created')   # nowa wersja
            latest = (self.db.query(ProcessModelVersion).filter_by(bpmn_process_id='proc_rej')
                      .order_by(ProcessModelVersion.version.desc()).first())
            self.assertEqual(latest.spec_json['galwork'], {'visibility': 'all'})

            with open(os.path.join(folder, 'process_model.json'), 'w', encoding='utf-8') as f:
                json.dump({**meta, 'galwork': {'visibility': 'nikt'}}, f)
            result = sync_folder(self.db, folder)
            self.assertEqual(result.status, 'error')
            self.assertIn('galwork.visibility', result.detail)


if __name__ == '__main__':
    unittest.main()
