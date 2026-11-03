# tests/test_edr_paperless_api.py
# router akcji dla e-Doręczeń przez Paperless-ngx
# (wf/routers/edr_paperless_api.py) + proces BPMN
# scripts/process_definitions/edr_korespondencja.bpmn (zob. analiza_edr.md).
# Mockujemy EdrConnector (klient protokołu EDR) i get_paperless_connector
# (singleton konektora) - testy nie wymagają żywego EDR/Paperless-ngx,
# tylko realnej instancji procesu w bazie testowej (jak test_signius_api.py).
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

from tests.base import ApiTestCase

from connector.edoreczenia.ua_model import (
    Contributor,
    Message,
    MessageAddressData,
    MessageControlData,
    MessageMetadata,
)
from connector.paperless.connector import PaperlessConnector
from wf.schemas.wf_schemas import CurrentUser, DocumentLinkRole

BPMN_PATH = os.path.join(
    os.path.dirname(__file__), '..', 'scripts', 'process_definitions', 'edr_korespondencja.bpmn')
with open(BPMN_PATH, encoding='utf-8') as _f:
    EDR_BPMN = _f.read()


def _message(message_id='msg-1', subject='Wezwanie', ade_from='AE:PL-FROM', ade_to='AE:PL-TO',
             text_body='Tresc wiadomosci', attachments=None):
    return Message(
        messageMetadata=MessageMetadata(
            messageId=message_id, subject=subject,
            **{'from': MessageAddressData(eDeliveryAddress=ade_from, contributor=Contributor())},
            to=[MessageAddressData(eDeliveryAddress=ade_to, contributor=Contributor())],
            shippingService='electronic',
        ),
        messageControlData=MessageControlData(labels=[]),
        textBody=text_body,
        attachments=attachments or [],
        evidences=[],
    )


class EdrPaperlessApiTest(ApiTestCase):

    def prepare(self) -> None:
        self.user = self.create_user()
        self.current_user = CurrentUser(
            id=self.user.id, sub=self.user.keycloak_sub, username=self.user.name,
            email=self.user.email, display_name=self.user.name,
            groups=frozenset({'kancelaria'}),
        )
        self.create_process_model(key='edr-korespondencja', bpmn=EDR_BPMN,
                                  bpmn_process_id='edr_korespondencja')

    # --- sync-inbox ------------------------------------------------------

    @patch('connector.edoreczenia.edr_paperless.EdrConnector')
    @patch('wf.routers.edr_paperless_api.get_paperless_connector')
    def test_sync_inbox_creates_instance_with_candidate_group_task(self, get_paperless, EdrConnectorCls):
        paperless = MagicMock()
        paperless.stage_bytes.return_value = '/tmp/staged'
        paperless.sha256_of = PaperlessConnector.sha256_of
        get_paperless.return_value = paperless

        edrc = MagicMock()
        edrc.inbox.return_value = ['msg-1']
        edrc.message.return_value = _message()
        EdrConnectorCls.return_value = edrc

        result = self.client.post('/v1/wf/edr/sync-inbox')
        self.assertEqual(result.status_code, 200, result.text)
        body = result.json()
        self.assertEqual(body['created'], 1)
        self.assertEqual(len(body['process_instance_ids']), 1)

        from wf.models import ProcessInstance
        instance = self.db.query(ProcessInstance).filter_by(business_key='msg-1').first()
        self.assertIsNotNone(instance)
        self.assertEqual(instance.summary['ade_from'], 'AE:PL-FROM')
        self.assertEqual(instance.summary['ade_to'], 'AE:PL-TO')

        # Zadanie gotowe do odebrania przez grupę "kancelaria" (BPMN).
        tasks = self.client.get('/v1/wf/tasks/my')
        self.assertEqual(tasks.status_code, 200, tasks.text)
        self.assertTrue(any(t['process_instance_id'] == str(instance.id)
                            for t in tasks.json()['items']))

    @patch('connector.edoreczenia.edr_paperless.EdrConnector')
    @patch('wf.routers.edr_paperless_api.get_paperless_connector')
    def test_sync_inbox_is_idempotent_on_message_id(self, get_paperless, EdrConnectorCls):
        """Druga wiadomość o tym samym messageId nie tworzy drugiej instancji
        (deduplikacja przez ProcessInstance.business_key)."""
        paperless = MagicMock()
        paperless.stage_bytes.return_value = '/tmp/staged'
        paperless.sha256_of = PaperlessConnector.sha256_of
        get_paperless.return_value = paperless

        edrc = MagicMock()
        edrc.inbox.return_value = ['msg-1']
        edrc.message.return_value = _message()
        EdrConnectorCls.return_value = edrc

        first = self.client.post('/v1/wf/edr/sync-inbox')
        second = self.client.post('/v1/wf/edr/sync-inbox')
        self.assertEqual(first.json()['created'], 1)
        self.assertEqual(second.json()['created'], 0)

    def test_sync_inbox_requires_token(self):
        """Bez zalogowania (bez podmiany zależności) endpoint musi zwrócić
        401 - jak test_wf_flow.py::test_endpoints_require_token."""
        for sub_app in self.sub_apps:
            sub_app.dependency_overrides.clear()
        result = self.client.post('/v1/wf/edr/sync-inbox')
        self.assertEqual(result.status_code, 401, result.text)

    # --- send --------------------------------------------------------

    def _create_outbound_instance_with_main_document(self, *, ade_to='AE:PL-ODBIORCA',
                                                       with_paperless_id=True):
        response = self.client.post('/v1/wf/process-instances', json={
            'process_model_key': 'edr-korespondencja', 'title': 'Odpowiedz',
            'business_key': None,
            'data': {'ade_to': ade_to, 'subject': 'Odpowiedz w sprawie'},
        })
        self.assertEqual(response.status_code, 201, response.text)
        instance_id = response.json()['id']

        from uuid import UUID
        from wf.models import Document, DocumentVersion, ProcessInstance, ProcessInstanceDocument
        # `data` z POST /process-instances trafia prosto do `summary`
        # (wf/services/process_service.py::start_instance) - to samo pole,
        # które czyta edr_paperless.send_document, więc nie trzeba go tu
        # ustawiać drugi raz.
        instance = self.db.get(ProcessInstance, UUID(instance_id))

        document = Document(doc_type='edr_out', title='Odpowiedz', created_by_id=self.user.id,
                            current_version_no=1,
                            paperless_document_id=42 if with_paperless_id else None)
        self.db.add(document)
        self.db.flush()
        version = DocumentVersion(document_id=document.id, version_no=1, file_name='odpowiedz.txt',
                                  mime_type='text/plain', size_bytes=4, sha256='abcd',
                                  uploaded_by_id=self.user.id, ingest_status='done',
                                  staging_path=None)
        self.db.add(version)
        self.db.add(ProcessInstanceDocument(process_instance_id=instance.id, document_id=document.id,
                                            role=DocumentLinkRole.MAIN))
        self.db.commit()
        return instance_id

    @patch('connector.edoreczenia.edr_paperless.EdrConnector')
    @patch('wf.routers.edr_paperless_api.get_paperless_connector')
    def test_send_document_happy_path(self, get_paperless, EdrConnectorCls):
        instance_id = self._create_outbound_instance_with_main_document()

        paperless = MagicMock()
        paperless.download_document.return_value = b'Tresc odpowiedzi'
        get_paperless.return_value = paperless

        edrc = MagicMock()
        edrc.send.return_value = {'MessageId': 'sent-123', 'Status': 'ok'}
        EdrConnectorCls.return_value = edrc

        result = self.client.post(f'/v1/wf/edr/process-instances/{instance_id}/send', json={})
        self.assertEqual(result.status_code, 200, result.text)
        body = result.json()
        self.assertEqual(body['message_id'], 'sent-123')

        edrc.send.assert_called_once()
        call_args = edrc.send.call_args[0]
        self.assertEqual(call_args[0], 'AE:PL-ODBIORCA')  # ade
        self.assertEqual(call_args[2], 'Tresc odpowiedzi')  # body z Paperless

        from wf.models import ProcessInstance
        from uuid import UUID
        instance = self.db.get(ProcessInstance, UUID(instance_id))
        self.assertEqual(instance.summary['message_id'], 'sent-123')

    def test_send_document_without_ade_to_is_409(self):
        response = self.client.post('/v1/wf/process-instances', json={
            'process_model_key': 'edr-korespondencja', 'title': 'Bez adresata',
        })
        instance_id = response.json()['id']
        result = self.client.post(f'/v1/wf/edr/process-instances/{instance_id}/send', json={})
        self.assertEqual(result.status_code, 409, result.text)
        self.assertIn('ade_to', result.json()['detail'])

    @patch('wf.routers.edr_paperless_api.get_paperless_connector')
    def test_send_document_without_ingested_main_document_is_409(self, get_paperless):
        instance_id = self._create_outbound_instance_with_main_document(with_paperless_id=False)
        result = self.client.post(f'/v1/wf/edr/process-instances/{instance_id}/send', json={})
        self.assertEqual(result.status_code, 409, result.text)
        self.assertIn('zaingestowan', result.json()['detail'])

    def test_send_document_unknown_instance_is_404(self):
        import uuid
        result = self.client.post(f'/v1/wf/edr/process-instances/{uuid.uuid4()}/send', json={})
        self.assertEqual(result.status_code, 404, result.text)

    # --- refresh-evidences -------------------------------------------

    def test_refresh_evidences_without_business_key_is_409(self):
        response = self.client.post('/v1/wf/process-instances', json={
            'process_model_key': 'edr-korespondencja', 'title': 'Bez messageId',
        })
        instance_id = response.json()['id']
        result = self.client.post(f'/v1/wf/edr/process-instances/{instance_id}/refresh-evidences')
        self.assertEqual(result.status_code, 409, result.text)

    @patch('wf.routers.edr_paperless_api.EdrConnector')
    @patch('connector.edoreczenia.edr_paperless.EdrConnector')
    @patch('wf.routers.edr_paperless_api.get_paperless_connector')
    def test_refresh_evidences_fetches_new_evidence_as_related_document(
            self, get_paperless, EdrConnectorCls, EdrConnectorClsRouter):
        """`sync_inbox` (łącznie z EdrConnector) żyje w `edr_paperless.py`,
        a `refresh_evidences` tworzy `EdrConnector` wprost w routerze - stąd
        dwa patche (różne importy tej samej klasy), oba oddające ten sam
        `edrc`, żeby zaprogramowane `PobranieDowodow`/`PobranieDowoduF`
        były widoczne w obu wywołaniach."""
        paperless = MagicMock()
        paperless.stage_bytes.return_value = '/tmp/staged-ev'
        paperless.sha256_of = PaperlessConnector.sha256_of
        get_paperless.return_value = paperless

        edrc = MagicMock()
        edrc.inbox.return_value = ['msg-2']
        edrc.message.return_value = _message(message_id='msg-2')
        EdrConnectorCls.return_value = edrc
        EdrConnectorClsRouter.return_value = edrc
        sync_result = self.client.post('/v1/wf/edr/sync-inbox')
        instance_id = sync_result.json()['process_instance_ids'][0]

        edrc.edr.PobranieDowodow.return_value = MagicMock(data=(
            '{"Evidences": [{"evidenceId": "ev-1", "type": "BPOP"}]}'
        ))
        edrc.edr.PobranieDowoduF.return_value = MagicMock(data=b'%PDF-dowod')

        result = self.client.post(
            f'/v1/wf/edr/process-instances/{instance_id}/refresh-evidences')
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()['fetched'], 1)

        from wf.models import Document, ProcessInstanceDocument
        related = (self.db.query(Document)
                  .join(ProcessInstanceDocument, ProcessInstanceDocument.document_id == Document.id)
                  .filter(ProcessInstanceDocument.role == DocumentLinkRole.RELATED)
                  .all())
        self.assertEqual(len(related), 1)
        self.assertEqual(related[0].doc_type, 'edr_evidence')
