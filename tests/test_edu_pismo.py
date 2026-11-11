# tests/test_edu_pismo.py
# Proces edu-pismo (spiffworkflow/edu/edu-pismo) od złożenia
# pisma (POST /v1/edu/jform/sprawa/submit) przez rozpatrzenie w dziekanacie,
# uzupełnienie przez studenta, aż po odpowiedź. Frappe Education jest
# mockowane; model procesu wczytujemy z dysku (filesystem_sync) i publikujemy.
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from tests.base import ApiTestCase

from wf.engine import UNRESOLVED_PRINCIPAL, TaskAssignment
from wf.schemas.wf_schemas import CurrentUser, ProcessInstanceStatus

KOR = 'edu.routers.korespondencja_api'
SYNC = 'edu.services.case_sync'
MODEL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         'spiffworkflow', 'edu', 'edu-pismo')


def make_case(**extra) -> dict:
  case = {
    'name': 'EDU-COR-2026-00001', 'student': 'STU-0001', 'student_name': 'Jan Kowalski',
    'album_number': '12345', 'correspondence_type': 'Pismo ogólne',
    'process_model_key': 'edu-pismo', 'subject': 'Pismo ogólne', 'status': 'Draft',
    'form_data': '{"temat": "Przedłużenie sesji", "tresc": "Proszę o przedłużenie sesji."}',
  }
  case.update(extra)
  return case


class EduPismoBase(ApiTestCase):
  """Model edu-pismo, użytkownicy i mocki Frappe (bez testów - baza też dla
  tests/test_edu_sealing.py)."""

  def prepare(self) -> None:
    self.student = self.create_user(email='jan@example.com', name='jan', keycloak_sub='sub-jan')
    self.staff = self.create_user(email='ewa@example.com', name='ewa', keycloak_sub='sub-ewa')
    self.publish_model()
    self.as_student()
    self.case = make_case()
    self.patches = [
      patch(f'{KOR}.db_get_correspondence', side_effect=lambda _name: self.case),
      # właściciel sprawy sprawdzany przed pobraniem treści.
      patch(f'{KOR}.db_get_correspondence_owner',
            side_effect=lambda _name: {'email': self.case.get('email'), 'student': self.case.get('student')}),
      patch(f'{KOR}.db_find_student', return_value={'name': 'STU-0001'}),
      patch(f'{KOR}.db_update_correspondence', return_value={}),
      patch(f'{SYNC}.db_get_correspondence', side_effect=lambda _name: self.case),
    ]
    for p in self.patches:
      p.start()
    self.set_process = self.start_patch(f'{SYNC}.db_set_correspondence_process')
    self.set_status = self.start_patch(f'{SYNC}.db_set_correspondence_status')
    self.record_response = self.start_patch(f'{SYNC}.db_record_correspondence_response')
    self.update_form = self.start_patch(f'{SYNC}.db_update_correspondence_form')

  def start_patch(self, target: str):
    p = patch(target)
    self.patches.append(p)
    return p.start()

  def tearDown(self) -> None:
    for p in self.patches:
      p.stop()
    super().tearDown()

  def publish_model(self) -> None:
    from wf.models import ProcessModelVersion
    from wf.schemas.wf_schemas import ProcessModelVersionStatus
    from wf.services.filesystem_sync import sync_folder

    result = sync_folder(self.db, MODEL_DIR)
    self.assertEqual(result.status, 'draft-created', result.detail)
    for version in self.db.query(ProcessModelVersion).all():
      version.status = ProcessModelVersionStatus.PUBLISHED
    self.db.commit()

  def as_student(self, email: str = 'jan@example.com') -> None:
    self.current_user = CurrentUser(
      id=self.student.id, sub='sub-jan', username='jan', email=email, display_name='jan')

  def as_staff(self) -> None:
    self.current_user = CurrentUser(
      id=self.staff.id, sub='sub-ewa', username='ewa', email='ewa@example.com',
      display_name='ewa', groups=frozenset({'dziekanat'}))

  # --- pomocnicze ---------------------------------------------------------

  def submit(self) -> dict:
    return self.client.post('/v1/edu/jform/sprawa/submit', params={'id': self.case['name']}).json()

  def my_task_names(self) -> list[str]:
    return [t['name'] for t in self.client.get('/v1/wf/tasks/my').json()['items']]

  def complete_my_task(self, name: str, data: dict):
    task = next(t for t in self.client.get('/v1/wf/tasks/my').json()['items'] if t['name'] == name)
    return self.client.post(f"/v1/wf/tasks/{task['id']}/complete", json={
      'data': data, 'expected_lock_version': task['process_instance_lock_version']})

  def task_form(self, name: str) -> dict:
    task = next(t for t in self.client.get('/v1/wf/tasks/my').json()['items'] if t['name'] == name)
    return self.client.get(f"/v1/wf/tasks/{task['id']}/form").json()

  def instance(self):
    from wf.models import ProcessInstance
    self.db.expire_all()
    return self.db.query(ProcessInstance).one()



class EduPismoTest(EduPismoBase):

  # --- przebieg -------------------------------------------------------------

  def test_full_flow_with_supplement(self):
    result = self.submit()
    self.assertEqual(result['code'], 0, result)
    instance = self.instance()
    self.assertEqual(instance.business_key, 'EDU-COR-2026-00001')
    self.assertEqual(instance.summary['student_username'], 'jan')
    self.set_process.assert_called_once_with('EDU-COR-2026-00001', str(instance.id))
    self.set_status.assert_called_with('EDU-COR-2026-00001', 'Submitted')

    # Student nie widzi zadania dziekanatu
    self.assertEqual(self.my_task_names(), [])

    self.as_staff()
    self.assertEqual(self.my_task_names(), ['Rozpatrzenie'])
    form = self.task_form('Rozpatrzenie')
    self.assertEqual(form['form_data']['temat'], 'Przedłużenie sesji')
    self.assertEqual(form['form_data']['album_number'], '12345')
    self.assertIn('chosen_path', form['json_schema']['properties'])

    done = self.complete_my_task('Rozpatrzenie', {
      'odpowiedz': 'Proszę dołączyć zaświadczenie lekarskie.', 'chosen_path': 'Flow_Uzupelnienie'})
    self.assertEqual(done.status_code, 200, done.text)
    self.record_response.assert_called_once_with(
      'EDU-COR-2026-00001', 'Proszę dołączyć zaświadczenie lekarskie.', 'In Progress')
    self.assertEqual(self.my_task_names(), [])  # zadanie studenta - nie dla dziekanatu

    from wf.models import Notification
    note = self.db.query(Notification).one()
    self.assertEqual(note.user_id, self.student.id)
    self.assertEqual(note.payload['status'], 'In Progress')

    self.as_student()
    self.assertEqual(self.my_task_names(), ['Uzupełnienie'])
    self.assertEqual(self.task_form('Uzupełnienie')['form_data']['odpowiedz'],
                     'Proszę dołączyć zaświadczenie lekarskie.')
    done = self.complete_my_task('Uzupełnienie', {'uzupelnienie': 'Dołączam zaświadczenie.'})
    self.assertEqual(done.status_code, 200, done.text)
    self.update_form.assert_called_once()
    self.assertEqual(self.update_form.call_args.args[1]['uzupelnienie'], 'Dołączam zaświadczenie.')
    self.set_status.assert_called_with('EDU-COR-2026-00001', 'Submitted')

    self.as_staff()
    second = self.task_form('Rozpatrzenie')['form_data']
    self.assertEqual(second['uzupelnienie'], 'Dołączam zaświadczenie.')
    # poprzedni wybór ("Do uzupełnienia") nie jest zaznaczony z góry
    self.assertEqual(second.get('chosen_path'), '__default__')
    done = self.complete_my_task('Rozpatrzenie', {
      'odpowiedz': 'Sesja przedłużona do 30 września.', 'chosen_path': '__default__'})
    self.assertEqual(done.status_code, 200, done.text)
    self.record_response.assert_called_with(
      'EDU-COR-2026-00001', 'Sesja przedłużona do 30 września.', 'Completed')
    self.assertEqual(self.instance().status, ProcessInstanceStatus.COMPLETED)

  def test_rejection_sets_rejected(self):
    self.assertEqual(self.submit()['code'], 0)
    self.as_staff()
    done = self.complete_my_task('Rozpatrzenie', {'odpowiedz': 'Brak podstaw.', 'chosen_path': 'Flow_Odmowa'})
    self.assertEqual(done.status_code, 200, done.text)
    self.record_response.assert_called_once_with('EDU-COR-2026-00001', 'Brak podstaw.', 'Rejected')
    self.assertEqual(self.instance().status, ProcessInstanceStatus.COMPLETED)

  def test_staff_cannot_complete_student_supplement(self):
    self.assertEqual(self.submit()['code'], 0)
    self.as_staff()
    self.complete_my_task('Rozpatrzenie', {'odpowiedz': 'Uzupełnij.', 'chosen_path': 'Flow_Uzupelnienie'})
    from wf.models import HumanTask
    task = self.db.query(HumanTask).filter(HumanTask.name == 'Uzupełnienie').one()
    response = self.client.post(f'/v1/wf/tasks/{task.id}/complete', json={
      'data': {'uzupelnienie': 'x'}, 'expected_lock_version': self.instance().lock_version})
    self.assertEqual(response.status_code, 403, response.text)

  # --- złożenie pisma -------------------------------------------------------

  def test_submit_twice_is_refused(self):
    self.assertEqual(self.submit()['code'], 0)
    self.assertEqual(self.submit()['code'], -4)

  def test_staff_does_not_submit_for_student(self):
    self.as_staff()
    with patch(f'{KOR}.db_find_student', return_value=None):
      self.assertEqual(self.submit()['code'], -2)

  def test_foreign_student_cannot_submit(self):
    self.as_student('obcy@example.com')
    with patch(f'{KOR}.db_find_student', return_value={'name': 'STU-0002'}):
      response = self.client.post('/v1/edu/jform/sprawa/submit', params={'id': self.case['name']})
    self.assertEqual(response.status_code, 403, response.text)

  def test_incomplete_form_is_refused(self):
    self.case = make_case(form_data='{"temat": "Sesja"}')
    result = self.submit()
    self.assertEqual(result['code'], -3, result)
    self.set_process.assert_not_called()

  def test_update_after_submit_is_refused(self):
    self.case = make_case(status='Submitted', process_instance_id='x')
    with patch(f'{KOR}.db_update_correspondence_form') as update:
      result = self.client.post('/v1/edu/jform/sprawa/update', params={'id': self.case['name']},
                                json={'tresc': 'zmiana'}).json()
    self.assertEqual(result['code'], -4)
    update.assert_not_called()

  def test_frappe_failure_does_not_undo_submit(self):
    self.set_process.side_effect = RuntimeError('Frappe niedostępne')
    self.assertEqual(self.submit()['code'], 0)
    from wf.models import ProcessEvent
    errors = self.db.query(ProcessEvent).filter(ProcessEvent.event_type == 'instance_error').all()
    self.assertEqual(len(errors), 1)
    # drugi start tej samej sprawy blokuje instancja w hubie
    self.assertEqual(self.submit()['code'], -4)


class DynamicAssignmentTest(unittest.TestCase):

  def test_variable_resolves_from_data(self):
    assignment = TaskAssignment(assignee='${student_username}')
    self.assertTrue(assignment.is_dynamic)
    self.assertEqual(assignment.resolve({'student_username': 'jan'}).assignee, 'jan')

  def test_list_variable_becomes_candidates(self):
    resolved = TaskAssignment(candidate_groups=('${grupy}', 'dziekanat')).resolve(
      {'grupy': ['biblioteka', 'kwestura']})
    self.assertEqual(resolved.candidate_groups, ('biblioteka', 'kwestura', 'dziekanat'))

  def test_missing_variable_is_not_open_to_everyone(self):
    resolved = TaskAssignment(assignee='${student_username}').resolve({})
    self.assertEqual(resolved.candidate_groups, (UNRESOLVED_PRINCIPAL,))
    self.assertFalse(resolved.allows('jan', ()))
