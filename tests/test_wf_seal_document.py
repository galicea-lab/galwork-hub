# tests/test_wf_seal_document.py
# 2026-11-17:  pieczęć elektroniczna dokumentu sprawy jako krok systemowy
# (wf/services/seal_service_tasks.py, handler `pieczec.dokument`). Usługę
# galwork-seal zastępuje FakeSealValkey z test_edu_sealing - endesive składa
# prawdziwy PAdES, który weryfikujemy.
from __future__ import annotations

import os
import unittest
import uuid
from unittest import mock

from tests.test_edu_sealing import FakeSealValkey, make_pdf, make_pki
from tests.test_wf_instance_documents import NS, _Base
from wf.models import Document, ProcessEvent

SEAL_PROPS = '''<bpmn:extensionElements><spiffworkflow:properties>
        <spiffworkflow:property name="handler" value="pieczec.dokument" />{extra}
      </spiffworkflow:properties></bpmn:extensionElements>'''


def repo_bpmn(seal_steps: int = 1, extra: str = '') -> str:
    """Repozytorium: dokument główny -> pieczęć (N razy) -> repozytorium."""
    steps, flows, prev = [], [], 'Service_Dokument'
    for n in range(1, seal_steps + 1):
        steps.append(f'<bpmn:serviceTask id="Seal_{n}" name="Pieczęć">'
                     f'{SEAL_PROPS.format(extra=extra)}</bpmn:serviceTask>')
        flows.append(f'<bpmn:sequenceFlow id="fs{n}" sourceRef="{prev}" targetRef="Seal_{n}" />')
        prev = f'Seal_{n}'
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions {NS} id="d_r" targetNamespace="x">
  <bpmn:process id="Process_Dokumenty" isExecutable="true">
    <bpmn:startEvent id="s" />
    <bpmn:sequenceFlow id="f1" sourceRef="s" targetRef="Service_Dokument" />
    <bpmn:serviceTask id="Service_Dokument" name="Dokument główny">
      <bpmn:extensionElements><spiffworkflow:properties>
        <spiffworkflow:property name="handler" value="dokumenty.glowny" />
      </spiffworkflow:properties></bpmn:extensionElements>
    </bpmn:serviceTask>
    {''.join(steps)}{''.join(flows)}
    <bpmn:sequenceFlow id="f2" sourceRef="{prev}" targetRef="Task_02" />
    <bpmn:userTask id="Task_02" name="Repozytorium dokumentów" />
    <bpmn:sequenceFlow id="f3" sourceRef="Task_02" targetRef="e" />
    <bpmn:endEvent id="e" />
  </bpmn:process>
</bpmn:definitions>'''


class FakeSealConnector:
    keyid = 'pieczec'
    timeout = 5

    def __init__(self, client):
        self.client = client

    def connect(self):
        return self.client


class SealDocumentTest(_Base):

    def setUp(self) -> None:
        super().setUp()
        self.pki = make_pki()
        self.pdf = make_pdf()
        self.valkey = FakeSealValkey(self.pki)
        for patcher in (
                mock.patch.dict(os.environ, {'SEAL_ENABLED': 'true'}),
                mock.patch('wf.services.seal_service_tasks._seal_connector',
                           side_effect=lambda: FakeSealConnector(self.valkey))):
            patcher.start()
            self.addCleanup(patcher.stop)

    # dane startowe można podać w teście (np. rezygnacja z pieczęci).
    def register(self, bpmn: str | None = None, content: bytes | None = None,
                 name: str = 'zarzadzenie.pdf', data: dict | None = None) -> tuple[dict, dict]:
        """Rejestracja ręczna: instancja czeka na dokument, wgranie + "Sprawdź teraz"."""
        self.publish('dokumenty', 'Process_Dokumenty', bpmn or repo_bpmn())
        instance = self.start('dokumenty', data)
        document = self.upload(instance['id'], content=content or self.pdf, name=name, role='main')
        self.assertEqual(document.status_code, 201, document.text)
        woken = self.client.post(f"/v1/wf/process-instances/{instance['id']}/wake")
        self.assertEqual(woken.status_code, 200, woken.text)
        return woken.json(), document.json()

    def events(self, instance_id: str, kind: str) -> list[ProcessEvent]:
        self.db.expire_all()
        return [e for e in self.db.query(ProcessEvent).all()
                if str(e.process_instance_id) == instance_id and e.event_type == kind]

    def test_document_is_sealed_as_new_version(self):
        from endesive import pdf

        instance, document = self.register()
        self.assertEqual([t.name for t in self.open_tasks(instance['id'])],
                         ['Repozytorium dokumentów'])

        detail = self.client.get(
            f"/v1/wf/process-instances/{instance['id']}/documents/{document['id']}").json()
        self.assertEqual(detail['current_version_no'], 2)
        self.assertEqual(detail['versions'][1]['label'], 'Opieczętowany (pieczęć elektroniczna)')
        self.assertEqual(detail['versions'][1]['file_name'], 'zarzadzenie-pieczec.pdf')
        self.assertEqual(detail['metadata']['pieczec']['version_no'], 2)
        self.assertEqual(detail['metadata']['pieczec']['source_sha256'],
                         detail['versions'][0]['sha256'])

        signed = self.client.get(
            f"/v1/wf/process-instances/{instance['id']}/documents/{document['id']}/content")
        self.assertEqual(signed.status_code, 200, signed.text)
        self.assertTrue(signed.content.startswith(self.pdf))  # podpis jako aktualizacja przyrostowa
        result, _rest = pdf.verify(signed.content, [self.pki['ca_pem']])
        self.assertTrue(result.signatureok and result.hashok and result.certok)

        original = self.client.get(
            f"/v1/wf/process-instances/{instance['id']}/documents/{document['id']}/content",
            params={'version_no': 1})
        self.assertEqual(original.content, self.pdf)

        request, = self.valkey.requests
        self.assertEqual(request['source'], 'dokumenty')  # klucz modelu procesu
        self.assertEqual(request['reference'], document['id'])
        completed, = self.events(instance['id'], 'signature_completed')
        self.assertEqual(completed.payload['version_no'], 2)
        self.assertEqual(completed.payload['signed_sha256'], detail['versions'][1]['sha256'])

    def test_sealed_document_is_not_sealed_again(self):
        instance, document = self.register(repo_bpmn(seal_steps=2))
        self.assertEqual(len(self.valkey.requests), 1)
        self.db.expire_all()
        self.assertEqual(self.db.get(Document, uuid.UUID(document['id'])).current_version_no, 2)

    def test_non_pdf_waits_for_pdf_version(self):
        instance, document = self.register(content=b'PK\x03\x04docx', name='projekt.docx')
        self.assertEqual(self.open_tasks(instance['id']), [])
        self.assertIn('Pieczęć wymaga PDF', instance['error_message'])
        self.assertEqual(self.valkey.requests, [])

        self.upload(instance['id'], content=self.pdf, name='zarzadzenie.pdf',
                    document_id=document['id'])
        woken = self.client.post(f"/v1/wf/process-instances/{instance['id']}/wake").json()
        self.assertEqual([t.name for t in self.open_tasks(instance['id'])],
                         ['Repozytorium dokumentów'])
        self.assertIsNone(woken['error_message'])
        self.assertEqual(len(self.valkey.requests), 1)

    # pole formularza pozwala zrezygnować z pieczęci dla tej sprawy.
    def test_form_opt_out_skips_seal_even_when_enabled(self):
        instance, document = self.register(
            data={'Czy opieczętować elektronicznie?': False})
        self.assertEqual([t.name for t in self.open_tasks(instance['id'])],
                         ['Repozytorium dokumentów'])
        skipped, = self.events(instance['id'], 'signature_completed')
        self.assertTrue(skipped.payload['skipped'])
        self.assertIn('formularzu', skipped.payload['reason'])
        self.assertEqual(self.valkey.requests, [])
        self.db.expire_all()
        self.assertEqual(self.db.get(Document, uuid.UUID(document['id'])).current_version_no, 1)

    def test_disabled_seal_blocks_unless_optional(self):
        with mock.patch.dict(os.environ, {'SEAL_ENABLED': 'false'}):
            instance, _document = self.register()
        self.assertEqual(self.open_tasks(instance['id']), [])
        self.assertIn('wyłączona', instance['error_message'])

    def test_optional_seal_is_skipped_when_disabled(self):
        extra = '\n        <spiffworkflow:property name="required" value="false" />'
        with mock.patch.dict(os.environ, {'SEAL_ENABLED': 'false'}):
            instance, document = self.register(repo_bpmn(extra=extra))
        self.assertEqual([t.name for t in self.open_tasks(instance['id'])],
                         ['Repozytorium dokumentów'])
        skipped, = self.events(instance['id'], 'signature_completed')
        self.assertTrue(skipped.payload['skipped'])
        self.db.expire_all()
        self.assertEqual(self.db.get(Document, uuid.UUID(document['id'])).current_version_no, 1)

    def test_refusal_keeps_document_and_waits(self):
        self.valkey.error = ('forbidden', "źródło 'dokumenty' nie może używać klucza 'pieczec'")
        instance, document = self.register()
        self.assertEqual(self.open_tasks(instance['id']), [])
        self.assertIn('forbidden', instance['error_message'])
        self.db.expire_all()
        self.assertEqual(self.db.get(Document, uuid.UUID(document['id'])).current_version_no, 1)


if __name__ == '__main__':
    unittest.main()
