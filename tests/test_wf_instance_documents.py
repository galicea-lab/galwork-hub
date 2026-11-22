# tests/test_wf_instance_documents.py
# 2026-11-17: dokumenty sprawy wgrywane przez użytkownika
# (wf/routers/instance_document_router.py, wf/services/instance_documents.py)
# oraz krok `dokumenty.glowny` dołączający dokument przekazany z innej sprawy
# - jak rejestracja zarządzenia w repozytorium regulacji.
from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
import uuid
from unittest import mock

from tests.base import ApiTestCase
from wf.models import DocumentVersion, HumanTask, ProcessEvent, ProcessInstanceDocument
from wf.schemas.wf_schemas import CurrentUser

NS = ('xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" '
      'xmlns:spiffworkflow="http://spiffworkflow.org/bpmn/schema/1.0/core"')

# Uproszczony `regulacje/dokumenty`: start -> dokument główny -> repozytorium
REPO = f'''<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions {NS} id="d_r" targetNamespace="x">
  <bpmn:process id="Process_Dokumenty" isExecutable="true">
    <bpmn:startEvent id="s" />
    <bpmn:sequenceFlow id="f1" sourceRef="s" targetRef="Service_Dokument" />
    <bpmn:serviceTask id="Service_Dokument" name="Dokument główny">
      <bpmn:extensionElements><spiffworkflow:properties>
        <spiffworkflow:property name="handler" value="dokumenty.glowny" />
      </spiffworkflow:properties></bpmn:extensionElements>
    </bpmn:serviceTask>
    <bpmn:sequenceFlow id="f2" sourceRef="Service_Dokument" targetRef="Task_02" />
    <bpmn:userTask id="Task_02" name="Repozytorium dokumentów" />
    <bpmn:sequenceFlow id="f3" sourceRef="Task_02" targetRef="e" />
    <bpmn:endEvent id="e" />
  </bpmn:process>
</bpmn:definitions>'''

# Uproszczony `przygotuj-zarzadzenie`: publikacja -> dokument -> rejestracja
ZARZADZENIE = f'''<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions {NS} id="d_z" targetNamespace="x">
  <bpmn:process id="Process_Zarzadzenie" isExecutable="true">
    <bpmn:startEvent id="s" />
    <bpmn:sequenceFlow id="f1" sourceRef="s" targetRef="Task_06" />
    <bpmn:userTask id="Task_06" name="Publikacja" />
    <bpmn:sequenceFlow id="f2" sourceRef="Task_06" targetRef="Service_Dokument" />
    <bpmn:serviceTask id="Service_Dokument" name="Dokument zarządzenia">
      <bpmn:extensionElements><spiffworkflow:properties>
        <spiffworkflow:property name="handler" value="dokumenty.glowny" />
      </spiffworkflow:properties></bpmn:extensionElements>
    </bpmn:serviceTask>
    <bpmn:sequenceFlow id="f3" sourceRef="Service_Dokument" targetRef="Send_Rejestr" />
    <bpmn:sendTask id="Send_Rejestr" name="Rejestracja w repozytorium">
      <bpmn:extensionElements><spiffworkflow:properties>
        <spiffworkflow:property name="startProcess" value="dokumenty" />
        <spiffworkflow:property name="startInput" value="dokument_id-&gt;dokument_id, Szczegóły dokumentu.Tytuł-&gt;Tytuł dokumentu" />
        <spiffworkflow:property name="startOnce" value="true" />
      </spiffworkflow:properties></bpmn:extensionElements>
    </bpmn:sendTask>
    <bpmn:sequenceFlow id="f4" sourceRef="Send_Rejestr" targetRef="Task_07" />
    <bpmn:userTask id="Task_07" name="Archiwum" />
    <bpmn:sequenceFlow id="f5" sourceRef="Task_07" targetRef="g" />
    <bpmn:exclusiveGateway id="g" default="f6" />
    <bpmn:sequenceFlow id="f6" sourceRef="g" targetRef="e" />
    <bpmn:sequenceFlow id="f7" name="Zapis do rejestru" sourceRef="g" targetRef="Service_Dokument">
      <bpmn:conditionExpression>chosen_path == 'f7'</bpmn:conditionExpression>
    </bpmn:sequenceFlow>
    <bpmn:endEvent id="e" />
  </bpmn:process>
</bpmn:definitions>'''

PDF = b'%PDF-1.7\nzarzadzenie 12/2026\n%%EOF'
PDF2 = b'%PDF-1.7\nzarzadzenie 12/2026 - tekst jednolity\n%%EOF'


class FakePaperless:
    """Staging w katalogu tymczasowym; wysyłka do Paperless tylko zapisywana."""

    def __init__(self, directory):
        self.directory = directory
        self.uploads = []

    def stage_bytes(self, content, filename):
        path = os.path.join(self.directory, f'{uuid.uuid4()}_{filename}')
        with open(path, 'wb') as handle:
            handle.write(content)
        return path

    def read_staged(self, path):
        with open(path, 'rb') as handle:
            return handle.read()

    @staticmethod
    def sha256_of(content):
        return hashlib.sha256(content).hexdigest()

    def upload_document(self, content, filename, **kwargs):
        self.uploads.append(filename)
        return f'task-{len(self.uploads)}'

    def get_or_create_tag(self, name):
        return 1


class _Base(ApiTestCase):

    def prepare(self) -> None:
        self.user = self.create_user()
        self.other = self.create_user(email='obcy@example.com', name='obcy', keycloak_sub='sub-obcy')
        self.as_owner()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.paperless = FakePaperless(self.tmp.name)
        for target in ('connector.paperless.get_connector',
                       'wf.services.signing_service.get_paperless_connector'):
            patcher = mock.patch(target, return_value=self.paperless)
            patcher.start()
            self.addCleanup(patcher.stop)

    def as_owner(self):
        self.current_user = CurrentUser(
            id=self.user.id, sub=self.user.keycloak_sub, username=self.user.name,
            email=self.user.email, display_name=self.user.name, groups=frozenset({'wf-admin'}))

    def as_other(self):
        self.current_user = CurrentUser(
            id=self.other.id, sub=self.other.keycloak_sub, username=self.other.name,
            email=self.other.email, display_name=self.other.name)

    def publish(self, key: str, process_id: str, bpmn: str) -> None:
        model = self.client.post('/v1/wf/process-models', json={'key': key, 'name': key.title()})
        self.assertEqual(model.status_code, 201, model.text)
        version = self.client.post(f"/v1/wf/process-models/{model.json()['id']}/versions", json={
            'bpmn_process_id': process_id,
            'files': [{'file_name': f'{key}.bpmn', 'file_type': 'bpmn', 'content': bpmn}]})
        self.assertEqual(version.status_code, 201, version.text)
        published = self.client.post(
            f"/v1/wf/process-models/{model.json()['id']}/versions/{version.json()['id']}/publish")
        self.assertEqual(published.status_code, 200, published.text)

    def start(self, key: str, data: dict | None = None) -> dict:
        response = self.client.post('/v1/wf/process-instances', json={
            'process_model_key': key, 'title': 'Zarządzenie 12/2026', 'data': data or {}})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def upload(self, instance_id: str, content: bytes = PDF, name: str = 'zarzadzenie.pdf', **form):
        return self.client.post(f'/v1/wf/process-instances/{instance_id}/documents',
                                files={'file': (name, content, 'application/octet-stream')},
                                data={k: str(v) for k, v in form.items()})

    def open_tasks(self, instance_id: str) -> list[HumanTask]:
        self.db.expire_all()
        return [t for t in self.db.query(HumanTask).all()
                if str(t.process_instance_id) == instance_id and t.status.value == 'open']

    def complete(self, instance_id: str, task: HumanTask, data: dict | None = None):
        lock = self.client.get(f'/v1/wf/process-instances/{instance_id}').json()['lock_version']
        response = self.client.post(f'/v1/wf/tasks/{task.id}/complete',
                                    json={'data': data or {}, 'expected_lock_version': lock})
        self.assertEqual(response.status_code, 200, response.text)


class InstanceDocumentApiTest(_Base):

    def setUp(self) -> None:
        super().setUp()
        self.publish('dokumenty', 'Process_Dokumenty', REPO)
        self.instance = self.start('dokumenty')

    def test_upload_list_and_download_with_hash(self):
        response = self.upload(self.instance['id'], title='Zarządzenie 12/2026', role='main')
        self.assertEqual(response.status_code, 201, response.text)
        document = response.json()
        self.assertEqual(document['title'], 'Zarządzenie 12/2026')
        self.assertEqual(document['current_version_no'], 1)
        self.assertEqual(document['current_version']['sha256'], hashlib.sha256(PDF).hexdigest())
        self.assertEqual(document['current_version']['mime_type'], 'application/pdf')
        self.assertEqual(document['current_version']['ingest_status'], 'submitted')

        listed = self.client.get(f"/v1/wf/process-instances/{self.instance['id']}/documents")
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertEqual([(d['role'], d['document']['id']) for d in listed.json()],
                         [('main', document['id'])])

        content = self.client.get(
            f"/v1/wf/process-instances/{self.instance['id']}/documents/{document['id']}/content")
        self.assertEqual(content.status_code, 200, content.text)
        self.assertEqual(content.content, PDF)
        self.assertEqual(content.headers['x-content-sha256'], hashlib.sha256(PDF).hexdigest())
        self.assertEqual(content.headers['content-type'], 'application/pdf')

    def test_new_version_keeps_previous(self):
        document = self.upload(self.instance['id'], role='main').json()
        second = self.upload(self.instance['id'], content=PDF2, name='zarzadzenie-tj.pdf',
                             document_id=document['id'], label='Tekst jednolity')
        self.assertEqual(second.status_code, 201, second.text)
        self.assertEqual(second.json()['id'], document['id'])
        self.assertEqual(second.json()['current_version_no'], 2)

        detail = self.client.get(
            f"/v1/wf/process-instances/{self.instance['id']}/documents/{document['id']}").json()
        self.assertEqual([(v['version_no'], v['label']) for v in detail['versions']],
                         [(1, None), (2, 'Tekst jednolity')])

        base = f"/v1/wf/process-instances/{self.instance['id']}/documents/{document['id']}/content"
        self.assertEqual(self.client.get(base, params={'version_no': 1}).content, PDF)
        self.assertEqual(self.client.get(base).content, PDF2)

        self.db.expire_all()
        events = [e.event_type for e in self.db.query(ProcessEvent).all()
                  if str(e.process_instance_id) == self.instance['id']]
        self.assertIn('document_added', events)
        self.assertIn('document_version_added', events)

    def test_tampered_file_is_not_served(self):
        document = self.upload(self.instance['id']).json()
        version = self.db.query(DocumentVersion).filter_by(document_id=uuid.UUID(document['id'])).one()
        with open(version.staging_path, 'wb') as handle:
            handle.write(b'%PDF-1.7\npodmieniona tresc\n%%EOF')
        response = self.client.get(
            f"/v1/wf/process-instances/{self.instance['id']}/documents/{document['id']}/content")
        self.assertEqual(response.status_code, 409, response.text)

    def test_rejects_disallowed_and_fake_pdf(self):
        self.assertEqual(self.upload(self.instance['id'], b'MZ\x90', 'program.exe').status_code, 415)
        self.assertEqual(self.upload(self.instance['id'], b'<html>', 'udaje.pdf').status_code, 415)
        self.assertEqual(self.upload(self.instance['id'], b'', 'pusty.pdf').status_code, 422)

    def test_version_of_foreign_document_is_rejected(self):
        other = self.start('dokumenty')
        foreign = self.upload(other['id']).json()
        response = self.upload(self.instance['id'], content=PDF2, document_id=foreign['id'])
        self.assertEqual(response.status_code, 404, response.text)
        response = self.client.get(
            f"/v1/wf/process-instances/{self.instance['id']}/documents/{foreign['id']}/content")
        self.assertEqual(response.status_code, 404, response.text)

    def test_outsider_has_no_access(self):
        document = self.upload(self.instance['id']).json()
        self.as_other()
        base = f"/v1/wf/process-instances/{self.instance['id']}/documents"
        self.assertEqual(self.client.get(base).status_code, 403)
        self.assertEqual(self.upload(self.instance['id']).status_code, 403)
        self.assertEqual(self.client.get(f"{base}/{document['id']}/content").status_code, 403)


class RepositoryRegistrationTest(_Base):

    def test_manual_registration_waits_for_document(self):
        self.publish('dokumenty', 'Process_Dokumenty', REPO)
        instance = self.start('dokumenty')
        self.assertEqual(self.open_tasks(instance['id']), [])
        self.assertIsNotNone(self.client.get(
            f"/v1/wf/process-instances/{instance['id']}").json()['next_wakeup_at'])

        self.assertEqual(self.upload(instance['id'], role='main').status_code, 201)
        woken = self.client.post(f"/v1/wf/process-instances/{instance['id']}/wake")
        self.assertEqual(woken.status_code, 200, woken.text)
        self.assertEqual([t.name for t in self.open_tasks(instance['id'])],
                         ['Repozytorium dokumentów'])

    def test_published_order_is_registered_with_same_document(self):
        self.publish('dokumenty', 'Process_Dokumenty', REPO)
        self.publish('zarzadzenie', 'Process_Zarzadzenie', ZARZADZENIE)
        parent = self.start('zarzadzenie')
        document = self.upload(parent['id'], role='main').json()

        [task] = self.open_tasks(parent['id'])
        self.complete(parent['id'], task, {'Szczegóły dokumentu': {'Tytuł': 'O cyfryzacji'}})

        children = self.client.get('/v1/wf/process-instances',
                                   params={'parent_instance_id': parent['id']}).json()['items']
        self.assertEqual(len(children), 1)
        child_id = children[0]['id']
        self.assertEqual([t.name for t in self.open_tasks(child_id)], ['Repozytorium dokumentów'])

        # ten sam dokument (nie kopia) - główny w repozytorium
        link = self.db.get(ProcessInstanceDocument, (uuid.UUID(child_id), uuid.UUID(document['id'])))
        self.assertIsNotNone(link)
        self.assertEqual(link.role.value, 'main')
        content = self.client.get(
            f"/v1/wf/process-instances/{child_id}/documents/{document['id']}/content")
        self.assertEqual(content.content, PDF)

    def test_second_registration_is_skipped(self):
        self.publish('dokumenty', 'Process_Dokumenty', REPO)
        self.publish('zarzadzenie', 'Process_Zarzadzenie', ZARZADZENIE)
        parent = self.start('zarzadzenie')
        self.upload(parent['id'], role='main')
        [task] = self.open_tasks(parent['id'])
        self.complete(parent['id'], task)
        [task] = self.open_tasks(parent['id'])
        self.assertEqual(task.name, 'Archiwum')
        self.complete(parent['id'], task, {'chosen_path': 'f7'})  # powtórny zapis do rejestru

        children = self.client.get('/v1/wf/process-instances',
                                   params={'parent_instance_id': parent['id']}).json()['items']
        self.assertEqual(len(children), 1)
        self.db.expire_all()
        skipped = [e for e in self.db.query(ProcessEvent).all()
                   if str(e.process_instance_id) == parent['id']
                   and e.event_type == 'child_instance_skipped']
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0].payload['existingInstanceId'], children[0]['id'])

    def test_cancelled_registration_can_be_repeated(self):
        self.publish('dokumenty', 'Process_Dokumenty', REPO)
        self.publish('zarzadzenie', 'Process_Zarzadzenie', ZARZADZENIE)
        parent = self.start('zarzadzenie')
        self.upload(parent['id'], role='main')
        [task] = self.open_tasks(parent['id'])
        self.complete(parent['id'], task)
        [first] = self.client.get('/v1/wf/process-instances',
                                  params={'parent_instance_id': parent['id']}).json()['items']
        cancelled = self.client.post(f"/v1/wf/process-instances/{first['id']}/cancel",
                                     json={'reason': 'pomyłka', 'expected_lock_version': first['lock_version']})
        self.assertEqual(cancelled.status_code, 200, cancelled.text)

        [task] = self.open_tasks(parent['id'])
        self.complete(parent['id'], task, {'chosen_path': 'f7'})
        children = self.client.get('/v1/wf/process-instances',
                                   params={'parent_instance_id': parent['id']}).json()['items']
        self.assertEqual(sorted(c['status'] for c in children), ['cancelled', 'running'])


if __name__ == '__main__':
    unittest.main()
