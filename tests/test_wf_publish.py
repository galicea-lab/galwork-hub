# tests/test_wf_publish.py
# publikacja wersji modelu zapisuje publikującego (published_by)- ślad audytowy.
"""Publikacja wersji procesu: kto i kiedy opublikował, wycofanie poprzedniej."""
from __future__ import annotations

import unittest
import uuid

from tests.base import ApiTestCase
from wf.models import ProcessModelVersion
from wf.schemas.wf_schemas import CurrentUser

BPMN = '''<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" id="d" targetNamespace="x">
  <bpmn:process id="Process_Prosty" isExecutable="true">
    <bpmn:startEvent id="s" />
    <bpmn:sequenceFlow id="f1" sourceRef="s" targetRef="Task_01" />
    <bpmn:userTask id="Task_01" name="Krok {n}" />
    <bpmn:sequenceFlow id="f2" sourceRef="Task_01" targetRef="e" />
    <bpmn:endEvent id="e" />
  </bpmn:process>
</bpmn:definitions>'''


class PublishTest(ApiTestCase):

    def prepare(self) -> None:
        self.user = self.create_user()
        self.current_user = CurrentUser(
            id=self.user.id, sub=self.user.keycloak_sub, username=self.user.name,
            email=self.user.email, display_name=self.user.name,
            groups=frozenset({'wf-admin'}))

    def _model(self) -> str:
        response = self.client.post('/v1/wf/process-models', json={'key': 'prosty', 'name': 'Prosty'})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()['id']

    def _version(self, model_id: str, n: int) -> dict:
        response = self.client.post(f'/v1/wf/process-models/{model_id}/versions', json={
            'bpmn_process_id': 'Process_Prosty',
            'files': [{'file_name': 'prosty.bpmn', 'file_type': 'bpmn',
                       'content': BPMN.format(n=n)}]})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def _publish(self, model_id: str, version_id: str) -> dict:
        response = self.client.post(
            f'/v1/wf/process-models/{model_id}/versions/{version_id}/publish')
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_draft_has_no_publisher(self):
        version = self._version(self._model(), 1)
        self.assertIsNone(version['published_by'])
        self.assertIsNone(version['published_at'])

    def test_publish_records_publisher(self):
        model_id = self._model()
        version = self._version(model_id, 1)
        published = self._publish(model_id, version['id'])

        self.assertEqual(published['status'], 'published')
        self.assertEqual(published['published_by'], self.user.id)
        self.assertIsNotNone(published['published_at'])

        self.db.expire_all()
        row = self.db.get(ProcessModelVersion, uuid.UUID(published['id']))
        self.assertEqual(row.published_by, self.user.id)

    def test_republish_keeps_publisher_of_retired_version(self):
        model_id = self._model()
        first = self._publish(model_id, self._version(model_id, 1)['id'])
        second = self._publish(model_id, self._version(model_id, 2)['id'])

        self.assertEqual(second['published_by'], self.user.id)
        versions = self.client.get(f'/v1/wf/process-models/{model_id}/versions')
        self.assertEqual(versions.status_code, 200, versions.text)
        by_id = {v['id']: v for v in versions.json()}
        self.assertEqual(by_id[first['id']]['status'], 'retired')
        self.assertEqual(by_id[first['id']]['published_by'], self.user.id)


if __name__ == '__main__':
    unittest.main()
