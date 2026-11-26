# tests/test_wf_access_staff.py
# 2026-11-25: dostęp do danych wewnętrznych tylko dla pracowników
# ([wf] staff_groups) i grup uprawnionych - TODO bezpieczeństwo 2.3.
from __future__ import annotations

import os
from decimal import Decimal
from unittest import mock
from unittest.mock import MagicMock, patch

from tests.base import ApiTestCase

from wf.schemas.wf_schemas import CurrentUser

STAFF_ENV = {'WF_STAFF_GROUPS': 'pracownicy'}


def _user(user, groups=()) -> CurrentUser:
  return CurrentUser(id=user.id, sub=user.keycloak_sub, username=user.name, email=user.email,
                     display_name=user.name, groups=frozenset(groups))


class StaffAccessTest(ApiTestCase):

  def prepare(self) -> None:
    env = mock.patch.dict(os.environ, STAFF_ENV)
    env.start()
    self.addCleanup(env.stop)
    self.staff_user = self.create_user()
    self.student_user = self.create_user(email='student@example.com', name='student',
                                         keycloak_sub='sub-s')
    self.staff = _user(self.staff_user, {'pracownicy', 'recenzenci'})
    self.student = _user(self.student_user, {'student'})
    self.current_user = self.staff

  # --- pomocniki -------------------------------------------------------

  def _model(self, key: str, galwork: dict | None = None) -> None:
    from wf.models import ProcessModelVersion
    model_id = self.create_process_model(key=key)
    if galwork is not None:
      version = self.db.query(ProcessModelVersion).filter_by(process_model_id=model_id).one()
      version.spec_json = {**(version.spec_json or {}), 'galwork': galwork}
      self.db.commit()

  def _start(self, key: str):
    return self.client.post('/v1/wf/process-instances', json={
      'process_model_key': key, 'title': 'Sprawa', 'data': {}})

  # --- start obiegu ----------------------------------------------------

  def test_start_without_start_groups_only_for_staff(self):
    self._model('wewnetrzny')
    self.current_user = self.student
    self.assertEqual(self._start('wewnetrzny').status_code, 403)
    self.current_user = self.staff
    self.assertEqual(self._start('wewnetrzny').status_code, 201)

  def test_start_groups_limit_start(self):
    self._model('dla-studenta', {'start_groups': ['student']})
    self.current_user = self.student
    self.assertEqual(self._start('dla-studenta').status_code, 201)
    self.current_user = self.staff
    self.assertEqual(self._start('dla-studenta').status_code, 403)
    self.current_user = _user(self.staff_user, {'wf-admin'})
    self.assertEqual(self._start('dla-studenta').status_code, 201)

  def test_invalid_start_groups_rejected_in_metadata(self):
    from wf.services.process_service import FormRenderError, read_model_meta
    with self.assertRaises(FormRenderError):
      read_model_meta('{"galwork": {"start_groups": "student"}}')
    self.assertEqual(read_model_meta('{"galwork": {"start_groups": ["student"]}}')['start_groups'],
                     ['student'])

  # --- szczegóły zadania i rejestry jawne ------------------------------

  def test_task_details_need_access(self):
    self._model('test-proces')
    instance = self._start('test-proces').json()
    task_id = self.client.get('/v1/wf/tasks/my').json()['items'][0]['id']
    self.assertEqual(self.client.get(f'/v1/wf/tasks/{task_id}').status_code, 200)
    self.current_user = self.student
    self.assertEqual(self.client.get(f'/v1/wf/tasks/{task_id}').status_code, 403)
    self.assertEqual(self.client.get(f"/v1/wf/process-instances/{instance['id']}").status_code, 403)

  def test_public_register_only_for_staff(self):
    self._model('rejestr', {'visibility': 'all'})
    instance = self._start('rejestr').json()
    other_staff = self.create_user(email='inny@example.com', name='inny', keycloak_sub='sub-3')
    self.current_user = _user(other_staff, {'pracownicy'})
    self.assertEqual(self.client.get(f"/v1/wf/process-instances/{instance['id']}").status_code, 200)
    self.assertEqual(self.client.get('/v1/wf/process-instances').json()['total'], 1)
    self.current_user = self.student
    self.assertEqual(self.client.get(f"/v1/wf/process-instances/{instance['id']}").status_code, 403)
    self.assertEqual(self.client.get('/v1/wf/process-instances').json()['total'], 0)

  # --- katalog osób i słowniki -----------------------------------------

  def test_user_directory_only_for_staff(self):
    self.assertEqual(self.client.get('/v1/wf/users').status_code, 200)
    self.current_user = self.student
    self.assertEqual(self.client.get('/v1/wf/users').status_code, 403)

  def test_dictionaries_for_student(self):
    self.current_user = self.student
    for slug in ('pracownicy', 'kontrahenci', 'studenci', 'teczki-spraw', 'grupy-ad',
                 'adres-e-doreczen', 'nieznany-rejestr'):
      self.assertEqual(self.client.get(f'/v1/wf/dictionaries/{slug}').status_code, 403, slug)
    for slug in ('kraj', 'kierunki', 'rodzaj-pisma-studenta'):
      self.assertNotEqual(self.client.get(f'/v1/wf/dictionaries/{slug}').status_code, 403, slug)
    with mock.patch.dict(os.environ, {'WF_EXTERNAL_DICTIONARIES': 'teczki-spraw'}):
      self.assertNotEqual(self.client.get('/v1/wf/dictionaries/teczki-spraw').status_code, 403)

  def test_contact_address_dictionary_needs_contact_or_edr_rights(self):
    self.assertEqual(self.client.get('/v1/wf/dictionaries/adres-e-doreczen').status_code, 403)
    self.current_user = _user(self.staff_user, {'pracownicy', 'kancelaria'})
    self.assertEqual(self.client.get('/v1/wf/dictionaries/adres-e-doreczen').status_code, 200)

  # --- e-Doręczenia, Paperless, ERPNext --------------------------------

  def test_ade_search_needs_edr_operator(self):
    with patch('wf.routers.edr_paperless_api._ade_call', return_value=[]):
      self.assertEqual(self.client.get('/v1/wf/edr/ade/person',
                                       params={'firstname': 'Jan', 'surname': 'K'}).status_code, 403)
      self.assertEqual(self.client.get('/v1/wf/edr/ade', params={'ade': 'AE:PL'}).status_code, 403)
      self.current_user = _user(self.staff_user, {'kancelaria'})
      self.assertEqual(self.client.get('/v1/wf/edr/ade/person',
                                       params={'firstname': 'Jan', 'surname': 'K'}).status_code, 200)

  @patch('wf.routers.paperless_document_api.paperless_document_service.list_by_tag')
  def test_paperless_documents(self, list_by_tag):
    list_by_tag.return_value = {'items': [], 'total': 0}
    self.assertEqual(self.client.get('/v1/wf/paperless/documents', params={'tag': 'x'}).status_code, 200)
    self.current_user = self.student
    self.assertEqual(self.client.get('/v1/wf/paperless/documents', params={'tag': 'x'}).status_code, 403)
    with mock.patch.dict(os.environ, {'PAPERLESS_READER_GROUPS': 'archiwum'}):
      self.current_user = self.staff
      self.assertEqual(self.client.get('/v1/wf/paperless/documents', params={'tag': 'x'}).status_code, 403)
      self.current_user = _user(self.staff_user, {'archiwum'})
      self.assertEqual(self.client.get('/v1/wf/paperless/documents', params={'tag': 'x'}).status_code, 200)

  @patch('wf.routers.erpnext_invoice_api.erpnext_invoice_service.list_pending_approval')
  @patch('wf.routers.erpnext_invoice_api.get_connector')
  def test_pending_invoices_for_approvers(self, get_connector, list_pending):
    get_connector.return_value = MagicMock(config={'approver_groups': 'faktury_zakupu',
                                                   'invoice_describer_group': 'faktury_opis'})
    list_pending.return_value = {'items': [], 'total': 0}
    url = '/v1/wf/erpnext/purchase-invoices/pending-approval'
    self.assertEqual(self.client.get(url).status_code, 403)
    for groups in ({'faktury_zakupu'}, {'faktury_opis'}, {'wf-admin'}):
      self.current_user = _user(self.staff_user, groups)
      self.assertEqual(self.client.get(url).status_code, 200, groups)

  # --- wnioski zakupowe ------------------------------------------------

  def _purchase_request(self, instance_id):
    from uuid import UUID

    from wf.models import PurchaseRequest
    row = PurchaseRequest(process_instance_id=UUID(instance_id), title='Zakup',
                          amount_estimated=Decimal('100'), expense_type='operating',
                          purchase_kind='service', requires_investment_approval=False,
                          requires_project_owner_approval=False, requires_additional_approval=False,
                          requires_tender=False, requires_warehouse_receipt=False)
    self.db.add(row)
    self.db.commit()
    return str(row.id)

  def test_purchase_requests_visibility(self):
    self._model('test-proces')
    own = self._purchase_request(self._start('test-proces').json()['id'])
    other_user = self.create_user(email='inny@example.com', name='inny', keycloak_sub='sub-3')
    self.current_user = _user(other_user, {'pracownicy'})
    foreign = self._purchase_request(self._start('test-proces').json()['id'])

    self.current_user = _user(other_user, {'pracownicy'})
    ids = [item['id'] for item in self.client.get('/v1/wf/purchase-requests').json()['items']]
    self.assertEqual(ids, [foreign])
    self.assertEqual(self.client.get(f'/v1/wf/purchase-requests/{own}').status_code, 403)

    self.current_user = _user(other_user, {'pracownicy', 'dzial_zakupow'})
    self.assertEqual(self.client.get('/v1/wf/purchase-requests').json()['total'], 2)

    self.current_user = self.student
    self.assertEqual(self.client.get('/v1/wf/purchase-requests').status_code, 403)
    self.assertEqual(self.client.post('/v1/wf/purchase-requests', json={
      'title': 'x', 'amount_estimated': 1, 'expense_type': 'operating',
      'purchase_kind': 'service'}).status_code, 403)

  # --- zgodność wsteczna -----------------------------------------------

  def test_empty_staff_groups_keeps_old_behaviour(self):
    self._model('wewnetrzny')
    with mock.patch.dict(os.environ, {'WF_STAFF_GROUPS': ''}):
      self.current_user = self.student
      self.assertEqual(self._start('wewnetrzny').status_code, 201)
      self.assertEqual(self.client.get('/v1/wf/users').status_code, 200)
