# tests/test_edr_wysylka.py
# Proces edr-wysylka (spiffworkflow/kancelaria/edr-wysylka):
# przygotowanie -> zatwierdzenie -> kolejka "Wysyłka EDR" obsługiwana przez
# edr_paperless.send_queued (cron scripts/edr.py send) -> potwierdzenie
# doręczenia albo "Problem z wysyłką". EDR i Paperless-ngx są mockowane.
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

from tests.base import ApiTestCase

from connector.paperless.connector import PaperlessConnector
from wf.schemas.wf_schemas import CurrentUser, DocumentLinkRole, ProcessInstanceStatus

MODEL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         'spiffworkflow', 'kancelaria', 'edr-wysylka')
EDR = 'connector.edoreczenia.edr_paperless.EdrConnector'
ADE = 'AE:PL-12345-67890-ABCDE-12'


def _paperless() -> MagicMock:
  paperless = MagicMock()
  paperless.get_document.side_effect = lambda pid: {
    'id': pid, 'title': f'Pismo {pid}', 'original_file_name': f'pismo-{pid}.pdf',
    'mime_type': 'application/pdf'}
  paperless.download_document.side_effect = lambda pid, original=False: f'%PDF-{pid}'.encode()
  paperless.sha256_of = PaperlessConnector.sha256_of
  paperless.stage_bytes.return_value = '/tmp/staged-ev'
  return paperless


class EdrWysylkaBase(ApiTestCase):
  """Model edr-wysylka, użytkownicy i pomocnicze (bez testów)."""

  def prepare(self) -> None:
    from wf.models import ProcessModelVersion
    from wf.schemas.wf_schemas import ProcessModelVersionStatus
    from wf.services.filesystem_sync import sync_folder

    self.user = self.create_user()
    self.system = self.create_user(email='cron@example.org', name='cron', keycloak_sub='sub-cron')
    self.current_user = CurrentUser(
      id=self.user.id, sub=self.user.keycloak_sub, username=self.user.name,
      email=self.user.email, display_name=self.user.name, groups=frozenset({'kancelaria'}))
    self.cron = CurrentUser(
      id=self.system.id, sub='sub-cron', username='cron', email='cron@example.org',
      display_name='Cron', groups=frozenset({'edr-system'}))
    result = sync_folder(self.db, MODEL_DIR)
    self.assertEqual(result.status, 'draft-created', result.detail)
    for version in self.db.query(ProcessModelVersion).all():
      version.status = ProcessModelVersionStatus.PUBLISHED
    self.db.commit()

  # --- pomocnicze ---------------------------------------------------------

  def my_tasks(self) -> list[dict]:
    return self.client.get('/v1/wf/tasks/my').json()['items']

  def complete(self, name: str, data: dict) -> None:
    task = next(t for t in self.my_tasks() if t['name'] == name)
    response = self.client.post(f"/v1/wf/tasks/{task['id']}/complete", json={
      'data': data, 'expected_lock_version': task['process_instance_lock_version']})
    self.assertEqual(response.status_code, 200, response.text)

  def instance(self):
    from wf.models import ProcessInstance
    self.db.expire_all()
    return self.db.query(ProcessInstance).one()

  def start_and_approve(self, document_ids=(10, 11)) -> None:
    response = self.client.post('/v1/wf/process-instances', json={
      'process_model_key': 'edr-wysylka', 'title': 'Odpowiedź na wezwanie', 'data': {}})
    self.assertEqual(response.status_code, 201, response.text)
    self.complete('Przygotowanie przesyłki', {
      'odbiorca': 'Urząd', 'ade_to': ADE, 'subject': 'Odpowiedź na wezwanie',
      'text_body': 'W załączeniu odpowiedź.', 'paperless_document_ids': list(document_ids)})
    self.complete('Zatwierdzenie wysyłki', {'chosen_path': '__default__'})

  def send(self, edrc: MagicMock, paperless: MagicMock | None = None):
    from connector.edoreczenia import edr_paperless
    with patch(EDR, return_value=edrc):
      return edr_paperless.send_queued(self.db, paperless or _paperless(), actor=self.cron)

  def linked(self) -> dict[int, DocumentLinkRole]:
    from wf.models import ProcessInstanceDocument
    instance = self.instance()
    return {link.document.paperless_document_id: link.role
            for link in self.db.query(ProcessInstanceDocument)
            .filter_by(process_instance_id=instance.id).all()}


class EdrWysylkaTest(EdrWysylkaBase):

  # --- przebieg -------------------------------------------------------------

  def test_approved_shipment_is_queued_for_system_group_only(self):
    from connector.edoreczenia.edr_paperless import queued_send_tasks
    self.start_and_approve()
    self.assertEqual([t.name for t in queued_send_tasks(self.db)], ['Wysyłka EDR'])
    self.assertNotIn('Wysyłka EDR', [t['name'] for t in self.my_tasks()])

  def test_send_queued_sends_documents_and_moves_to_delivery(self):
    self.start_and_approve()
    edrc = MagicMock()
    edrc.send.return_value = {'MessageId': 'sent-1', 'Status': 'ok'}

    result = self.send(edrc)

    self.assertEqual((result.sent, result.failed, result.deferred), (1, 0, 0))
    ade, subject, body, attachments = edrc.send.call_args[0]
    self.assertEqual((ade, subject, body), (ADE, 'Odpowiedź na wezwanie', 'W załączeniu odpowiedź.'))
    self.assertEqual([a['filename'] for a in attachments], ['pismo-10.pdf', 'pismo-11.pdf'])
    self.assertEqual(attachments[0]['contentType'], 'application/pdf')

    instance = self.instance()
    self.assertEqual(instance.business_key, 'sent-1')  # fetch_evidences ją obejmie
    self.assertEqual(instance.summary['message_id'], 'sent-1')
    self.assertEqual(self.linked(), {10: DocumentLinkRole.MAIN, 11: DocumentLinkRole.ATTACHMENT})
    self.assertEqual([t['name'] for t in self.my_tasks()], ['Potwierdzenie doręczenia'])

    # Kolejka jest pusta - drugi przebieg niczego nie wysyła.
    edrc.send.reset_mock()
    self.assertEqual(self.send(edrc).sent, 0)
    edrc.send.assert_not_called()

    self.complete('Potwierdzenie doręczenia', {'chosen_path': '__default__'})
    self.assertEqual(self.instance().status, ProcessInstanceStatus.COMPLETED)

  def test_edr_error_is_retried_then_goes_to_problem_and_can_be_resent(self):
    from connector.edoreczenia import edr_paperless
    self.start_and_approve()
    edrc = MagicMock()
    edrc.send.return_value = {'MessageId': '', 'Status': 'error invalid_request'}

    for attempt in range(1, edr_paperless.SEND_MAX_ATTEMPTS):
      self.assertEqual(self.send(edrc).deferred, 1)
      self.assertEqual(self.instance().summary['send_attempts'], attempt)
    self.assertEqual(self.send(edrc).failed, 1)

    self.assertEqual([t['name'] for t in self.my_tasks()], ['Problem z wysyłką'])
    task = self.my_tasks()[0]
    form = self.client.get(f"/v1/wf/tasks/{task['id']}/form").json()
    self.assertIn('invalid_request', form['form_data']['send_error'])

    self.complete('Problem z wysyłką', {'chosen_path': '__default__'})  # ponów
    edrc.send.return_value = {'MessageId': 'sent-2', 'Status': 'ok'}
    self.assertEqual(self.send(edrc).sent, 1)
    self.assertEqual(self.instance().business_key, 'sent-2')

  def test_corrected_shipment_relinks_documents(self):
    self.start_and_approve(document_ids=(10, 11))
    edrc = MagicMock()
    edrc.send.side_effect = RuntimeError('EDR: brak połączenia')
    from connector.edoreczenia import edr_paperless
    for _ in range(edr_paperless.SEND_MAX_ATTEMPTS):
      self.send(edrc)

    self.complete('Problem z wysyłką', {'chosen_path': 'Flow_Popraw'})
    self.complete('Przygotowanie przesyłki', {
      'ade_to': ADE, 'subject': 'Odpowiedź - poprawiona', 'paperless_document_ids': [12]})
    self.complete('Zatwierdzenie wysyłki', {'chosen_path': '__default__'})

    edrc.send.side_effect = None
    edrc.send.return_value = {'MessageId': 'sent-3', 'Status': 'ok'}
    self.assertEqual(self.send(edrc).sent, 1)
    _ade, subject, _body, attachments = edrc.send.call_args[0]
    self.assertEqual(subject, 'Odpowiedź - poprawiona')
    self.assertEqual([a['filename'] for a in attachments], ['pismo-12.pdf'])
    self.assertEqual(self.linked(), {12: DocumentLinkRole.MAIN})

  def test_invalid_ade_is_rejected_by_form(self):
    response = self.client.post('/v1/wf/process-instances', json={
      'process_model_key': 'edr-wysylka', 'title': 'Zły adres', 'data': {}})
    self.assertEqual(response.status_code, 201, response.text)
    task = next(t for t in self.my_tasks() if t['name'] == 'Przygotowanie przesyłki')
    response = self.client.post(f"/v1/wf/tasks/{task['id']}/complete", json={
      'data': {'ade_to': 'jan@example.com', 'subject': 'x', 'paperless_document_ids': [1]},
      'expected_lock_version': task['process_instance_lock_version']})
    self.assertEqual(response.status_code, 422, response.text)


class EdrWysylkaDeliveryTest(EdrWysylkaBase):
  """Automatyczne zamknięcie "Potwierdzenia doręczenia"."""

  def sent_instance(self):
    self.start_and_approve()
    edrc = MagicMock()
    edrc.send.return_value = {'MessageId': 'sent-1', 'Status': 'ok'}
    self.send(edrc)
    return self.instance()

  def evidences(self, edrc: MagicMock, *types: str) -> None:
    evidences = [{'evidenceId': f'ev-{t}', 'type': t} for t in types]
    import json
    edrc.edr.PobranieDowodow.return_value = MagicMock(data=json.dumps({'Evidences': evidences}))
    edrc.edr.PobranieDowoduF.return_value = MagicMock(data=b'%PDF-dowod')

  def test_delivery_evidence_completes_delivery_task(self):
    from connector.edoreczenia import edr_paperless
    instance = self.sent_instance()
    edrc = MagicMock()
    self.evidences(edrc, 'BPWX')  # akceptacja - jeszcze nie doręczenie
    edr_paperless.fetch_evidences_for_instance(self.db, _paperless(), instance, edrc, actor=self.cron)
    self.db.commit()
    self.assertEqual(edr_paperless.complete_delivered(self.db, actor=self.cron), 0)
    self.assertEqual([t['name'] for t in self.my_tasks()], ['Potwierdzenie doręczenia'])

    self.evidences(edrc, 'BPWX', 'BPOP')
    edr_paperless.fetch_evidences_for_instance(self.db, _paperless(), self.instance(), edrc,
                                               actor=self.cron)
    self.db.commit()
    self.assertEqual(edr_paperless.complete_delivered(self.db, actor=self.cron), 1)
    instance = self.instance()
    self.assertEqual(instance.status, ProcessInstanceStatus.COMPLETED)
    self.assertEqual(instance.summary['evidence_types'], ['BPOP', 'BPWX'])

  @patch('wf.routers.edr_paperless_api.get_paperless_connector')
  def test_refresh_evidences_endpoint_completes_delivery(self, get_paperless):
    instance = self.sent_instance()
    get_paperless.return_value = _paperless()
    edrc = MagicMock()
    self.evidences(edrc, 'BPOP')
    with patch('wf.routers.edr_paperless_api.EdrConnector', return_value=edrc):
      result = self.client.post(f'/v1/wf/edr/process-instances/{instance.id}/refresh-evidences')
    self.assertEqual(result.status_code, 200, result.text)
    self.assertEqual(self.instance().status, ProcessInstanceStatus.COMPLETED)

  def test_disabled_automation_leaves_task_open(self):
    from connector.edoreczenia import edr_paperless
    instance = self.sent_instance()
    instance.summary = {**instance.summary, 'evidence_types': ['BPOP']}
    self.db.commit()
    with patch.object(edr_paperless, 'DELIVERED_EVIDENCE_TYPES', ()):
      self.assertEqual(edr_paperless.complete_delivered(self.db, actor=self.cron), 0)


class EdrReplyTest(EdrWysylkaBase):
  """ "wymaga odpowiedzi" w edr-korespondencja startuje edr-wysylka."""

  def prepare(self) -> None:
    super().prepare()
    from wf.models import ProcessModel, ProcessModelFile, ProcessModelVersion
    from wf.schemas.wf_schemas import ProcessFileType, ProcessModelVersionStatus
    from wf.services.process_service import build_spec_json
    folder = os.path.join(os.path.dirname(MODEL_DIR), '..', '..', 'scripts', 'process_definitions')
    files = []
    for name, kind in (('edr_korespondencja.bpmn', ProcessFileType.BPMN),
                       ('przeglad.json', ProcessFileType.JSON_SCHEMA),
                       ('przeglad.uischema.json', ProcessFileType.UI_SCHEMA)):
      with open(os.path.join(folder, name), encoding='utf-8') as f:
        files.append((name, kind, f.read()))
    model = ProcessModel(key='edr-korespondencja', name='Korespondencja')
    self.db.add(model)
    self.db.flush()
    self.db.add(ProcessModelVersion(
      process_model_id=model.id, version=1, status=ProcessModelVersionStatus.PUBLISHED,
      bpmn_process_id='edr_korespondencja', content_hash='sha256:edr',
      spec_json=build_spec_json(files),
      files=[ProcessModelFile(file_name=n, file_type=k, content=c, sha256='x') for n, k, c in files]))
    self.db.commit()

  def incoming(self) -> str:
    response = self.client.post('/v1/wf/process-instances', json={
      'process_model_key': 'edr-korespondencja', 'title': 'Wezwanie do uzupełnienia',
      'business_key': 'msg-in-1',
      'data': {'ade_from': ADE, 'subject': 'Wezwanie do uzupełnienia', 'direction': 'in'}})
    self.assertEqual(response.status_code, 201, response.text)
    return response.json()['id']

  def instances(self) -> dict:
    from wf.models import ProcessInstance
    self.db.expire_all()
    return {i.version.process_model.key: i for i in self.db.query(ProcessInstance).all()}

  def test_needs_reply_starts_prefilled_shipment(self):
    incoming_id = self.incoming()
    self.complete('Przeglad korespondencji', {'decyzja': 'wymaga_odpowiedzi'})

    by_key = self.instances()
    reply = by_key['edr-wysylka']
    self.assertEqual(by_key['edr-korespondencja'].summary['reply_instance_id'], str(reply.id))
    self.assertEqual(reply.summary['reply_to_instance_id'], incoming_id)
    self.assertEqual(reply.summary['reply_to_message_id'], 'msg-in-1')

    task = next(t for t in self.my_tasks() if t['name'] == 'Przygotowanie przesyłki')
    form = self.client.get(f"/v1/wf/tasks/{task['id']}/form").json()
    self.assertEqual(form['form_data']['ade_to'], ADE)
    self.assertEqual(form['form_data']['subject'], 'Odp: Wezwanie do uzupełnienia')

  def test_registered_only_does_not_start_shipment(self):
    self.incoming()
    self.complete('Przeglad korespondencji', {'decyzja': 'zarejestrowano'})
    self.assertNotIn('edr-wysylka', self.instances())
