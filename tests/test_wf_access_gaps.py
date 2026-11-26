# tests/test_wf_access_gaps.py
# 2026-11-25: uprawnienia zamykające luki "dla każdego zalogowanego":
# zadania bez przypisania (wf/services/task_access.py), POST /edr/sync-inbox
# (require_edr_operator) i odczyt kontrahentów (wf/routers/contact_api.py).
from __future__ import annotations

import os
from unittest import mock
from unittest.mock import MagicMock, patch

from tests.base import ApiTestCase

from wf.schemas.wf_schemas import CurrentUser

UNASSIGNED_BPMN = """<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"
  xmlns:flowable="http://flowable.org/bpmn"
  id="def_u" targetNamespace="urn:galwork:test">
  <bpmn:process id="proc_unassigned" isExecutable="true">
    <bpmn:startEvent id="start_1"/>
    <bpmn:sequenceFlow id="flow_1" sourceRef="start_1" targetRef="task_free"/>
    <bpmn:userTask id="task_free" name="Bez przypisania"/>
    <bpmn:sequenceFlow id="flow_2" sourceRef="task_free" targetRef="end_1"/>
    <bpmn:endEvent id="end_1"/>
  </bpmn:process>
</bpmn:definitions>
"""


def _user(user, groups=()) -> CurrentUser:
  return CurrentUser(id=user.id, sub=user.keycloak_sub, username=user.name, email=user.email,
                     display_name=user.name, groups=frozenset(groups))


class UnassignedTaskTest(ApiTestCase):

  model_key = 'proces-bez-przypisania'

  def prepare(self) -> None:
    self.initiator = self.create_user()
    self.other = self.create_user(email='obcy@example.com', name='obcy', keycloak_sub='sub-2')
    self.current_user = _user(self.initiator)
    self.create_process_model(key=self.model_key, bpmn=UNASSIGNED_BPMN,
                              bpmn_process_id='proc_unassigned')
    response = self.client.post('/v1/wf/process-instances', json={
      'process_model_key': self.model_key, 'title': 'Sprawa', 'data': {}})
    self.assertEqual(response.status_code, 201, response.text)
    self.instance_id = response.json()['id']
    from uuid import UUID
    self.instance_uuid = UUID(self.instance_id)
    from wf.models import HumanTask
    self.task_id = str(self.db.query(HumanTask).one().id)

  def _my_task_ids(self) -> list[str]:
    response = self.client.get('/v1/wf/tasks/my')
    self.assertEqual(response.status_code, 200, response.text)
    return [item['id'] for item in response.json()['items']]

  def _claim(self):
    from wf.models import ProcessInstance
    self.db.expire_all()
    lock_version = self.db.get(ProcessInstance, self.instance_uuid).lock_version
    return self.client.post(f'/v1/wf/tasks/{self.task_id}/claim',
                            json={'expected_lock_version': lock_version})

  def test_initiator_sees_and_claims(self):
    self.assertEqual(self._my_task_ids(), [self.task_id])
    self.assertEqual(self._claim().status_code, 200)

  def test_other_user_neither_sees_nor_claims(self):
    self.current_user = _user(self.other, groups={'student'})
    self.assertEqual(self._my_task_ids(), [])
    self.assertEqual(self._claim().status_code, 403)
    self.assertEqual(self.client.get(f'/v1/wf/tasks/{self.task_id}/form').status_code, 403)

  def test_substitute_of_initiator_may_act(self):
    self.current_user = _user(self.other).model_copy(
      update={'substituting_for': frozenset({self.initiator.id})})
    self.assertEqual(self._my_task_ids(), [self.task_id])
    self.assertEqual(self._claim().status_code, 200)

  def test_process_admin_may_act(self):
    self.current_user = _user(self.other, groups={'wf-admin'})
    self.assertEqual(self._my_task_ids(), [self.task_id])
    self.assertEqual(self._claim().status_code, 200)

  def test_open_mode_restores_old_behaviour(self):
    self.current_user = _user(self.other)
    with mock.patch.dict(os.environ, {'WF_UNASSIGNED_TASKS': 'open'}):
      self.assertEqual(self._my_task_ids(), [self.task_id])
      self.assertEqual(self._claim().status_code, 200)


class ActRepositoryUnassignedTaskTest(UnassignedTaskTest):
  """Repozytorium aktów: zadania bez przypisania wykonuje każdy redaktor aktów."""

  model_key = 'dokumenty'

  def test_act_editor_may_act(self):
    with mock.patch.dict(os.environ, {'WF_ACT_EDITOR_GROUPS': 'redakcja'}):
      self.current_user = _user(self.other, groups={'redakcja'})
      self.assertEqual(self._my_task_ids(), [self.task_id])
      self.assertEqual(self._claim().status_code, 200)

  def test_other_user_neither_sees_nor_claims(self):
    # bez listy act_editor_groups redaktorem jest każdy (jak przy starcie obiegu),
    # więc odmowa wymaga skonfigurowanej grupy
    with mock.patch.dict(os.environ, {'WF_ACT_EDITOR_GROUPS': 'redakcja'}):
      super().test_other_user_neither_sees_nor_claims()


class EdrSyncInboxAccessTest(ApiTestCase):

  def prepare(self) -> None:
    self.user = self.create_user()

  @patch('wf.routers.edr_paperless_api.edr_paperless.sync_inbox')
  @patch('wf.routers.edr_paperless_api.get_paperless_connector')
  def test_requires_operator_group(self, get_paperless, sync_inbox):
    get_paperless.return_value = MagicMock()
    sync_inbox.return_value = []
    self.current_user = _user(self.user, groups={'student'})
    self.assertEqual(self.client.post('/v1/wf/edr/sync-inbox').status_code, 403)
    sync_inbox.assert_not_called()

    for groups in ({'kancelaria'}, {'edr-system'}, {'wf-admin'}):
      self.current_user = _user(self.user, groups=groups)
      response = self.client.post('/v1/wf/edr/sync-inbox')
      self.assertEqual(response.status_code, 200, (groups, response.text))

  @patch('wf.routers.edr_paperless_api.edr_paperless.sync_inbox')
  @patch('wf.routers.edr_paperless_api.get_paperless_connector')
  def test_operator_groups_from_config(self, get_paperless, sync_inbox):
    get_paperless.return_value = MagicMock()
    sync_inbox.return_value = []
    with mock.patch.dict(os.environ, {'EDR_OPERATOR_GROUPS': 'sekretariat'}):
      self.current_user = _user(self.user, groups={'kancelaria'})
      self.assertEqual(self.client.post('/v1/wf/edr/sync-inbox').status_code, 403)
      self.current_user = _user(self.user, groups={'sekretariat'})
      self.assertEqual(self.client.post('/v1/wf/edr/sync-inbox').status_code, 200)


class ContactAccessTest(ApiTestCase):

  def prepare(self) -> None:
    from wf.models import Contact, ContactAddress
    self.user = self.create_user()
    firma = Contact(company_name='Firma Sp. z o.o.', nip='1234567890', extra={'uwagi': 'x'})
    osoba = Contact(first_name='Jan', last_name='Kowalski', pesel='90010112345')
    self.db.add_all([firma, osoba])
    self.db.flush()
    self.db.add(ContactAddress(contact_id=firma.id, label='Siedziba', ade='AE:PL-11111-11111-AAAAA-11'))
    self.db.commit()
    self.firma_id, self.osoba_id = str(firma.id), str(osoba.id)

  def test_limited_view_for_regular_user(self):
    self.current_user = _user(self.user, groups={'pracownik'})
    body = self.client.get('/v1/wf/contacts').json()
    self.assertEqual(body['total'], 1)
    item = body['items'][0]
    self.assertEqual(item['company_name'], 'Firma Sp. z o.o.')
    self.assertIsNone(item['pesel'])
    self.assertEqual(item['addresses'], [])
    self.assertEqual(item['extra'], {})
    # bez szukania po PESEL i bez dostępu do osoby fizycznej
    self.assertEqual(self.client.get('/v1/wf/contacts', params={'q': '9001011'}).json()['total'], 0)
    self.assertEqual(self.client.get(f'/v1/wf/contacts/{self.osoba_id}').status_code, 404)
    firma = self.client.get(f'/v1/wf/contacts/{self.firma_id}').json()
    self.assertEqual(firma['addresses'], [])

  def test_full_view_for_admin_and_contact_groups(self):
    self.current_user = _user(self.user, groups={'wf-admin'})
    body = self.client.get('/v1/wf/contacts').json()
    self.assertEqual(body['total'], 2)
    self.assertEqual(self.client.get('/v1/wf/contacts', params={'q': '9001011'}).json()['total'], 1)

    with mock.patch.dict(os.environ, {'WF_CONTACT_GROUPS': 'kancelaria'}):
      self.current_user = _user(self.user, groups={'kancelaria'})
      osoba = self.client.get(f'/v1/wf/contacts/{self.osoba_id}')
      self.assertEqual(osoba.status_code, 200)
      self.assertEqual(osoba.json()['pesel'], '90010112345')
      firma = self.client.get(f'/v1/wf/contacts/{self.firma_id}').json()
      self.assertEqual(len(firma['addresses']), 1)
