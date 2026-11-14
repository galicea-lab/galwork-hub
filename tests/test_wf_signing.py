# tests/test_wf_signing.py
# 2026-11-13: wspólny podproces "Podpis dokumentu" (spiffworkflow/wspolne/
# podpis-dokumentu) wywoływany przez callActivity; kroki systemowe
# signius.request / signius.wait (wf/services/signing_service.py) z atrapami
# konektorów Signius i Paperless.
from __future__ import annotations

import os
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from unittest import mock

from tests.base import ApiTestCase
from wf.models import Document, DocumentVersion, HumanTask, ProcessEvent, ProcessInstance
from wf.schemas.wf_schemas import CurrentUser
from wf.services.filesystem_sync import _load_files
from wf.services.process_service import get_service

HUB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PODPIS = os.path.join(HUB, 'spiffworkflow', 'wspolne', 'podpis-dokumentu')

PARENT = '''<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" id="d_u" targetNamespace="x">
  <bpmn:process id="Process_Umowa" isExecutable="true">
    <bpmn:startEvent id="s" />
    <bpmn:sequenceFlow id="f1" sourceRef="s" targetRef="Task_01" />
    <bpmn:userTask id="Task_01" name="Przygotuj umowę" />
    <bpmn:sequenceFlow id="f2" sourceRef="Task_01" targetRef="Call_Podpis" />
    <bpmn:callActivity id="Call_Podpis" name="Podpis" calledElement="Process_Podpis_dokumentu" />
    <bpmn:sequenceFlow id="f3" sourceRef="Call_Podpis" targetRef="Task_02" />
    <bpmn:userTask id="Task_02" name="Po podpisie" />
    <bpmn:sequenceFlow id="f4" sourceRef="Task_02" targetRef="e" />
    <bpmn:endEvent id="e" />
  </bpmn:process>
</bpmn:definitions>'''


class FakeSignius:
    def __init__(self):
        self.status = 'IN_PROGRESS'
        self.requests = []
        self.fail_upload = False

    def create_folder(self, name):
        return {'documentFolderId': 'F1'}

    def upload_document(self, folder_id, file_name, content):
        if self.fail_upload:
            from connector.base import ConnectorError
            raise ConnectorError('Signius niedostępny')
        return {'documentId': f'D{len(self.requests) + 1}'}

    def request_signature(self, document_id, email, phone, *, title, qualified):
        self.requests.append((document_id, email, phone, title, qualified))

    def get_document_status(self, document_id, folder_id):
        return self.status

    def get_signed_document(self, document_id):
        return b'%PDF-podpisany'


class FakePaperless:
    def __init__(self, directory):
        self.directory = directory

    def stage_bytes(self, content, filename):
        path = os.path.join(self.directory, filename)
        with open(path, 'wb') as handle:
            handle.write(content)
        return path

    @staticmethod
    def sha256_of(content):
        import hashlib
        return hashlib.sha256(content).hexdigest()


class SigningSubprocessTest(ApiTestCase):

    def prepare(self) -> None:
        self.user = self.create_user()
        self.current_user = CurrentUser(
            id=self.user.id, sub=self.user.keycloak_sub, username=self.user.name,
            email=self.user.email, display_name=self.user.name, groups=frozenset({'wf-admin'}))
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.signius = FakeSignius()
        for target, value in (('wf.services.signing_service.get_signius_connector', self.signius),
                              ('wf.services.signing_service.get_paperless_connector',
                               FakePaperless(self.tmp.name))):
            patcher = mock.patch(target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    # --- pomocnicze -----------------------------------------------------

    def _publish(self, key, process_id, files):
        model = self.client.post('/v1/wf/process-models', json={'key': key, 'name': key})
        version = self.client.post(f"/v1/wf/process-models/{model.json()['id']}/versions",
                                   json={'bpmn_process_id': process_id, 'files': files})
        self.assertEqual(version.status_code, 201, version.text)
        published = self.client.post(
            f"/v1/wf/process-models/{model.json()['id']}/versions/{version.json()['id']}/publish")
        self.assertEqual(published.status_code, 200, published.text)
        return published.json()

    def _setup_models(self):
        files = [{'file_name': n, 'file_type': t.value, 'content': c} for n, t, c in _load_files(PODPIS)]
        self._publish('podpis-dokumentu', 'Process_Podpis_dokumentu', files)
        return self._publish('umowa', 'Process_Umowa', [
            {'file_name': 'umowa.bpmn', 'file_type': 'bpmn', 'content': PARENT}])

    def _document(self) -> Document:
        path = os.path.join(self.tmp.name, 'umowa.pdf')
        with open(path, 'wb') as handle:
            handle.write(b'%PDF-umowa')
        document = Document(doc_type='umowa', title='Umowa 7/2026', created_by_id=self.user.id,
                            current_version_no=1)
        self.db.add(document)
        self.db.flush()
        self.db.add(DocumentVersion(document_id=document.id, version_no=1, file_name='umowa.pdf',
                                    mime_type='application/pdf', size_bytes=10, sha256='x',
                                    staging_path=path, uploaded_by_id=self.user.id))
        self.db.commit()
        return document

    def _instance(self, instance_id) -> ProcessInstance:
        import uuid
        self.db.expire_all()
        return self.db.get(ProcessInstance, uuid.UUID(instance_id))

    def _open(self, instance_id) -> list[HumanTask]:
        self.db.expire_all()
        return [t for t in self.db.query(HumanTask).all()
                if str(t.process_instance_id) == instance_id and t.status.value in ('open', 'claimed')]

    def _complete(self, instance_id, task, data):
        lock = self.client.get(f'/v1/wf/process-instances/{instance_id}').json()['lock_version']
        return self.client.post(f'/v1/wf/tasks/{task.id}/complete',
                                json={'data': data, 'expected_lock_version': lock})

    def _events(self, instance_id, event_type):
        self.db.expire_all()
        return [e for e in self.db.query(ProcessEvent).all()
                if str(e.process_instance_id) == instance_id and e.event_type == event_type]

    def _to_signing(self):
        """Umowa -> podproces -> wysłanie do Signius; zwraca (instancja, dokument)."""
        published = self._setup_models()
        self.assertEqual(published['warnings'], [])  # podproces ma ioSpecification
        document = self._document()
        instance_id = self.client.post('/v1/wf/process-instances', json={
            'process_model_key': 'umowa', 'title': 'Umowa 7'}).json()['id']
        task = self._open(instance_id)[0]
        self.assertEqual(self._complete(instance_id, task, {'dokument_id': str(document.id),
                                                             'pesel': '9001'}).status_code, 200)
        prep = self._open(instance_id)[0]
        self.assertEqual(prep.name, 'Przygotowanie do podpisu')
        form = self.client.get(f'/v1/wf/tasks/{prep.id}/form').json()
        self.assertNotIn('pesel', form['form_data'])
        done = self._complete(instance_id, prep, {'podpisujacy_email': 'rektor@uczelnia.pl',
                                                   'podpisujacy_telefon': '500100200'})
        self.assertEqual(done.status_code, 200, done.text)
        return instance_id, document

    # --- testy ------------------------------------------------------------

    def test_signed_document_returns_to_parent(self):
        instance_id, document = self._to_signing()
        # wysłane do Signius, proces czeka (bez zadań dla ludzi)
        self.assertEqual(self.signius.requests, [('D1', 'rektor@uczelnia.pl', '500100200',
                                                  'Umowa 7/2026', False)])
        instance = self._instance(instance_id)
        self.assertEqual(instance.status.value, 'waiting')
        self.assertIsNotNone(instance.next_wakeup_at)
        self.assertEqual(self._open(instance_id), [])

        # jeszcze nie podpisany - kolejne sprawdzenie nic nie zmienia
        self.assertEqual(self.client.post(f'/v1/wf/process-instances/{instance_id}/wake').status_code, 200)
        self.assertEqual(self._open(instance_id), [])

        self.signius.status = 'SIGNED'
        woken = self.client.post(f'/v1/wf/process-instances/{instance_id}/wake')
        self.assertEqual(woken.status_code, 200, woken.text)
        self.assertIsNone(woken.json()['next_wakeup_at'])
        task = self._open(instance_id)[0]
        self.assertEqual(task.name, 'Po podpisie')
        data = self.client.get(f'/v1/wf/tasks/{task.id}/form').json()['form_data']
        self.assertEqual(data['wynik_podpisu'], 'podpisany')
        self.assertEqual(data['podpisany_dokument_id'], str(document.id))
        self.assertNotIn('podpisujacy_email', data)  # tylko wyjścia podprocesu

        self.db.expire_all()
        document = self.db.get(Document, document.id)
        self.assertEqual(document.current_version_no, 2)
        self.assertEqual(document.current_version.label, 'Podpisany (Signius)')
        for event_type in ('signature_requested', 'signature_completed', 'service_task_completed'):
            self.assertTrue(self._events(instance_id, event_type), event_type)

    def test_rejected_signature_can_be_retried(self):
        instance_id, _document = self._to_signing()
        self.signius.status = 'REJECTED'
        self.client.post(f'/v1/wf/process-instances/{instance_id}/wake')
        task = self._open(instance_id)[0]
        self.assertEqual(task.name, 'Podpis odrzucony')
        form = self.client.get(f'/v1/wf/tasks/{task.id}/form').json()
        self.assertEqual(form['form_data']['status_signius'], 'REJECTED')
        self.assertEqual(self._complete(instance_id, task, {'chosen_path': 'Flow_Ponow'}).status_code, 200)
        self.assertEqual(self._open(instance_id)[0].name, 'Przygotowanie do podpisu')

    def test_connector_error_is_logged_and_retried(self):
        self.signius.fail_upload = True
        instance_id, _document = self._to_signing()
        instance = self._instance(instance_id)
        self.assertIn('Signius niedostępny', instance.error_message)
        self.assertIsNotNone(instance.next_wakeup_at)
        self.assertTrue(self._events(instance_id, 'service_task_failed'))

        # cron po czasie: Signius znowu działa -> wysłane, błąd skasowany
        self.signius.fail_upload = False
        woken = get_service().wake_due(self.db, self.current_user,
                                       now=datetime.now(UTC) + timedelta(hours=1))
        self.assertEqual([str(i.id) for i in woken], [instance_id])
        instance = self._instance(instance_id)
        self.assertIsNone(instance.error_message)
        self.assertEqual(len(self.signius.requests), 1)


    # --- dokument bez wejścia dokument_id -------------------------------

    def test_main_document_of_case_when_parent_passes_none(self):
        from wf.models import ProcessInstanceDocument
        from wf.schemas.wf_schemas import DocumentLinkRole
        import uuid
        self._setup_models()
        document = self._document()
        instance_id = self.client.post('/v1/wf/process-instances', json={
            'process_model_key': 'umowa', 'title': 'Umowa 8'}).json()['id']
        task = self._open(instance_id)[0]
        # proces wywołujący nie zna dokumentu - pierwszy krok bez dokumentu czeka
        self.assertEqual(self._complete(instance_id, task, {'pesel': '9001'}).status_code, 200)
        self.assertEqual(self._open(instance_id), [])
        self.assertIn('nie ma dołączonego dokumentu', self._instance(instance_id).error_message)

        # dokument dołączony do sprawy -> "Sprawdź teraz" -> przygotowanie
        self.db.add(ProcessInstanceDocument(process_instance_id=uuid.UUID(instance_id),
                                            document_id=document.id, role=DocumentLinkRole.MAIN))
        self.db.commit()
        self.client.post(f'/v1/wf/process-instances/{instance_id}/wake')
        prep = self._open(instance_id)[0]
        self.assertEqual(prep.name, 'Przygotowanie do podpisu')
        data = self.client.get(f'/v1/wf/tasks/{prep.id}/form').json()['form_data']
        self.assertEqual(data['dokument_tytul'], 'Umowa 7/2026')
        self.assertNotIn('pesel', data)
        self.assertIsNone(self._instance(instance_id).error_message)


if __name__ == '__main__':
    unittest.main()
