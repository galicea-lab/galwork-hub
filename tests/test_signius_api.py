# tests/test_signius_api.py
# integracja Frappe Education (Student Correspondence) +
# Signius + instancja procesu BPMN (wf/routers/signius_api.py). Mockujemy
# oba konektory (get_connector singletons) - testy nie wymagają żywego
# Frappe/Signius, tylko realnej instancji procesu w bazie testowej (tak jak
# tests/test_wf_flow.py).
from __future__ import annotations

from unittest.mock import MagicMock, patch

from tests.base import ApiTestCase

from wf.schemas.wf_schemas import CurrentUser


class SigniusApiTest(ApiTestCase):

  def prepare(self) -> None:
    self.user = self.create_user()
    self.current_user = CurrentUser(
      id=self.user.id, sub=self.user.keycloak_sub, username=self.user.name,
      email=self.user.email, display_name=self.user.name,
    )
    self.create_process_model()
    response = self.client.post('/v1/wf/process-instances', json={
      'process_model_key': 'test-proces',
      'title': 'Wniosek o urlop dziekański',
      'business_key': 'EDU-COR-2026-00001',
    })
    self.assertEqual(response.status_code, 201, response.text)
    self.instance_id = response.json()['id']

  def _url(self, suffix: str) -> str:
    return f'/v1/wf/signius/process-instances/{self.instance_id}/{suffix}'

  def test_request_signature_without_business_key_is_409(self):
    response = self.client.post('/v1/wf/process-instances', json={
      'process_model_key': 'test-proces', 'title': 'Bez numeru sprawy',
    })
    instance_id = response.json()['id']
    result = self.client.post(
      f'/v1/wf/signius/process-instances/{instance_id}/request-signature',
      json={'signee_email': 'jan@example.com', 'signee_phone': '+48600000000'})
    self.assertEqual(result.status_code, 409, result.text)
    self.assertIn('business_key', result.json()['detail'])

  @patch('wf.routers.signius_api.get_frappe_connector')
  def test_request_signature_without_pdf_attachment_is_409(self, get_frappe):
    frappe = MagicMock()
    frappe.get_doc.return_value = {'name': 'EDU-COR-2026-00001', 'signature_status': 'Not Required'}
    frappe.get_file_list.return_value = []
    get_frappe.return_value = frappe

    result = self.client.post(self._url('request-signature'), json={
      'signee_email': 'jan@example.com', 'signee_phone': '+48600000000'})
    self.assertEqual(result.status_code, 409, result.text)
    self.assertIn('PDF', result.json()['detail'])

  @patch('wf.routers.signius_api.get_signius_connector')
  @patch('wf.routers.signius_api.get_frappe_connector')
  def test_request_signature_happy_path(self, get_frappe, get_signius):
    frappe = MagicMock()
    frappe.get_doc.return_value = {
      'name': 'EDU-COR-2026-00001', 'subject': 'Wniosek o urlop',
      'signature_status': 'Not Required', 'signius_folder_id': None,
    }
    frappe.get_file_list.return_value = [
      {'file_name': 'wniosek.pdf', 'file_url': '/private/files/wniosek.pdf'},
    ]
    frappe.get_file_content.return_value = b'%PDF-source'
    get_frappe.return_value = frappe

    signius = MagicMock()
    signius.create_folder.return_value = {'documentFolderId': 'folder-1'}
    signius.upload_document.return_value = {'documentId': 'doc-1'}
    get_signius.return_value = signius

    result = self.client.post(self._url('request-signature'), json={
      'signee_email': 'jan@example.com', 'signee_phone': '+48600000000'})
    self.assertEqual(result.status_code, 200, result.text)
    body = result.json()
    self.assertEqual(body['signature_status'], 'Pending')
    self.assertEqual(body['signius_document_id'], 'doc-1')
    self.assertEqual(body['business_key'], 'EDU-COR-2026-00001')

    # Sprawdź, że właściwe dane poszły do Signiusa i do Frappe.
    signius.upload_document.assert_called_once_with('folder-1', 'wniosek.pdf', b'%PDF-source')
    signius.request_signature.assert_called_once_with(
      'doc-1', 'jan@example.com', '+48600000000', title='Wniosek o urlop', qualified=True)
    frappe.call_method.assert_called_once()
    call_args = frappe.call_method.call_args
    self.assertIn('record_signius_document', call_args[0][0])
    self.assertEqual(call_args[0][1], {
      'name': 'EDU-COR-2026-00001', 'folder_id': 'folder-1', 'document_id': 'doc-1'})

    # Zdarzenie dopisane do dziennika instancji.
    from wf.models import ProcessEvent
    events = self.db.query(ProcessEvent).all()
    self.assertTrue(any(e.event_type == 'signature_requested' for e in events))

  @patch('wf.routers.signius_api.get_frappe_connector')
  def test_request_signature_already_signed_is_409(self, get_frappe):
    frappe = MagicMock()
    frappe.get_doc.return_value = {'name': 'EDU-COR-2026-00001', 'signature_status': 'Signed'}
    get_frappe.return_value = frappe

    result = self.client.post(self._url('request-signature'), json={
      'signee_email': 'jan@example.com', 'signee_phone': '+48600000000'})
    self.assertEqual(result.status_code, 409, result.text)

  @patch('wf.routers.signius_api.get_frappe_connector')
  def test_status_returns_current_state_without_polling_when_not_pending(self, get_frappe):
    frappe = MagicMock()
    frappe.get_doc.return_value = {
      'name': 'EDU-COR-2026-00001', 'signature_status': 'Not Required',
    }
    get_frappe.return_value = frappe

    result = self.client.get(self._url('status'))
    self.assertEqual(result.status_code, 200, result.text)
    self.assertEqual(result.json()['signature_status'], 'Not Required')

  @patch('wf.routers.signius_api.get_signius_connector')
  @patch('wf.routers.signius_api.get_frappe_connector')
  def test_status_downloads_and_attaches_signed_document_when_ready(self, get_frappe, get_signius):
    frappe = MagicMock()
    frappe.get_doc.return_value = {
      'name': 'EDU-COR-2026-00001', 'signature_status': 'Pending',
      'signius_folder_id': 'folder-1', 'signius_document_id': 'doc-1',
    }
    frappe.upload_file.return_value = {'file_url': '/private/files/podpisano.pdf'}
    get_frappe.return_value = frappe

    signius = MagicMock()
    signius.get_document_status.return_value = 'SIGNED'
    signius.get_signed_document.return_value = b'%PDF-signed'
    get_signius.return_value = signius

    result = self.client.get(self._url('status'))
    self.assertEqual(result.status_code, 200, result.text)
    body = result.json()
    self.assertEqual(body['signature_status'], 'Signed')
    self.assertEqual(body['signed_document_url'], '/private/files/podpisano.pdf')

    signius.get_document_status.assert_called_once_with('doc-1', 'folder-1')
    frappe.upload_file.assert_called_once()
    self.assertEqual(frappe.upload_file.call_args.kwargs['fieldname'], 'signed_document')

    from wf.models import ProcessEvent
    events = self.db.query(ProcessEvent).all()
    self.assertTrue(any(e.event_type == 'signature_completed' for e in events))

  @patch('wf.routers.signius_api.get_signius_connector')
  @patch('wf.routers.signius_api.get_frappe_connector')
  def test_status_stays_pending_when_signius_not_done(self, get_frappe, get_signius):
    frappe = MagicMock()
    frappe.get_doc.return_value = {
      'name': 'EDU-COR-2026-00001', 'signature_status': 'Pending',
      'signius_folder_id': 'folder-1', 'signius_document_id': 'doc-1',
    }
    get_frappe.return_value = frappe

    signius = MagicMock()
    signius.get_document_status.return_value = 'SENT'
    get_signius.return_value = signius

    result = self.client.get(self._url('status'))
    self.assertEqual(result.status_code, 200, result.text)
    self.assertEqual(result.json()['signature_status'], 'Pending')
    frappe.upload_file.assert_not_called()
