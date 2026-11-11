# tests/test_edu_api.py
# 2026-11-05: moduł edu (router.py: /v1/edu) - montowanie routerów
# i kontrola dostępu: student widzi tylko własne dane i sprawy, pracownik
# dziekanatu ([edu] staff_groups) - wszystkie. Funkcje konektora Frappe
# Education są mockowane, testy nie wymagają żywego Frappe.
from __future__ import annotations

from unittest.mock import patch

from tests.base import ApiTestCase

from wf.schemas.wf_schemas import CurrentUser

KOR = 'edu.routers.korespondencja_api'
STU = 'edu.routers.student_api'

CASE = {
  'name': 'EDU-COR-2026-00001', 'student': 'STU-0001', 'email': 'jan@example.com',
  'correspondence_type': 'Pismo ogólne', 'status': 'Draft', 'form_data': '{"tresc": "a"}',
}
# właściciel sprawy (same pola email / student) - sprawdzany
# przed pobraniem pełnej sprawy.
OWNER = {'email': CASE['email'], 'student': CASE['student']}


class EduApiTest(ApiTestCase):

  def prepare(self) -> None:
    self.user = self.create_user(email='jan@example.com', name='jan')
    self.as_student()

  def as_student(self, email: str = 'jan@example.com') -> None:
    self.current_user = CurrentUser(
      id=self.user.id, sub='sub-1', username='jan', email=email, display_name='jan')

  def as_staff(self) -> None:
    self.current_user = CurrentUser(
      id=self.user.id, sub='sub-1', username='ewa', email='ewa@example.com',
      display_name='ewa', groups=frozenset({'dziekanat'}))

  def test_openapi_lists_edu_endpoints(self):
    paths = self.client.get('/v1/edu/openapi.json').json()['paths']
    for path in ('/jform/sprawa/new', '/jform/sprawa/list', '/student/studia', '/kierunki', '/sale'):
      self.assertIn(path, paths)

  @patch(f'{KOR}.db_get_student_correspondences', return_value=[CASE])
  def test_student_lists_own_cases(self, get_cases):
    result = self.client.get('/v1/edu/jform/sprawa/list')
    self.assertEqual(result.json()['code'], 0, result.text)
    self.assertEqual(result.json()['list'][0]['id'], 'EDU-COR-2026-00001')
    get_cases.assert_called_once_with(email='jan@example.com', nr_albumu=None)

  @patch(f'{KOR}.db_get_student_correspondences')
  def test_student_cannot_list_other_student_cases(self, get_cases):
    result = self.client.get('/v1/edu/jform/sprawa/list', params={'email': 'obcy@example.com'})
    self.assertEqual(result.json()['code'], -2)
    get_cases.assert_not_called()

  @patch(f'{KOR}.db_get_student_correspondences', return_value=[])
  def test_staff_lists_cases_by_album(self, get_cases):
    self.as_staff()
    result = self.client.get('/v1/edu/jform/sprawa/list', params={'nr_albumu': '12345'})
    self.assertEqual(result.json()['code'], 0, result.text)
    get_cases.assert_called_once_with(email=None, nr_albumu='12345')

  @patch(f'{KOR}.db_get_correspondence_owner', return_value=OWNER)
  @patch(f'{KOR}.db_find_student', return_value={'name': 'STU-0002'})
  @patch(f'{KOR}.db_get_correspondence', return_value=CASE)
  def test_student_cannot_read_foreign_case(self, get_case, _find, _owner):
    self.as_student('obcy@example.com')
    result = self.client.get('/v1/edu/jform/sprawa/form', params={'id': CASE['name']})
    self.assertEqual(result.status_code, 403, result.text)
    # treść cudzej sprawy nie jest w ogóle pobierana.
    get_case.assert_not_called()

  # // 2026-11-08
  @patch(f'{KOR}.db_get_correspondence_owner', return_value=None)
  @patch(f'{KOR}.db_get_correspondence')
  def test_unknown_case_is_403_like_foreign(self, get_case, _owner):
    result = self.client.get('/v1/edu/jform/sprawa/form', params={'id': 'EDU-COR-2026-99999'})
    self.assertEqual(result.status_code, 403, result.text)
    get_case.assert_not_called()

  @patch(f'{KOR}.db_list_correspondence_files')
  @patch(f'{KOR}.db_get_correspondence_owner', return_value=OWNER)
  @patch(f'{KOR}.db_find_student', return_value={'name': 'STU-0002'})
  @patch(f'{KOR}.db_get_correspondence')
  def test_foreign_attachments_are_not_listed(self, get_case, _find, _owner, list_files):
    self.as_student('obcy@example.com')
    result = self.client.get('/v1/edu/jform/sprawa/attachments', params={'id': CASE['name']})
    self.assertEqual(result.status_code, 403, result.text)
    list_files.assert_not_called()
    get_case.assert_not_called()

  @patch(f'{KOR}.db_list_correspondence_files', return_value=[])
  @patch(f'{KOR}.db_get_correspondence_owner')
  @patch(f'{KOR}.db_get_correspondence')
  def test_staff_lists_attachments_without_loading_the_case(self, get_case, owner, _files):
    self.as_staff()
    result = self.client.get('/v1/edu/jform/sprawa/attachments', params={'id': CASE['name']})
    self.assertEqual(result.json()['code'], 0, result.text)
    owner.assert_not_called()
    get_case.assert_not_called()

  @patch(f'{STU}.db_get_student_form', return_value={'email': 'jan@example.com'})
  @patch(f'{STU}.db_email2nr_albumu', return_value='12345')
  def test_student_form_only_own_album(self, _album, get_form):
    result = self.client.get('/v1/edu/student/form', params={'nr_albumu': '99999', 'kierunek': 'INF'})
    self.assertEqual(result.json()['code'], -2, result.text)
    get_form.assert_not_called()
    result = self.client.get('/v1/edu/student/form', params={'nr_albumu': '12345', 'kierunek': 'INF'})
    self.assertEqual(result.json()['code'], 0, result.text)
    get_form.assert_called_once_with('12345', 'INF')

  @patch(f'{STU}.db_get_student_form', return_value={'email': 'jan@example.com'})
  def test_staff_reads_any_student_form(self, get_form):
    self.as_staff()
    result = self.client.get('/v1/edu/student/form', params={'nr_albumu': '99999', 'kierunek': 'INF'})
    self.assertEqual(result.json()['code'], 0, result.text)
    get_form.assert_called_once_with('99999', 'INF')
  # //--

  @patch(f'{KOR}.case_form_schema', return_value=({'type': 'object'}, {}))
  @patch(f'{KOR}.correspondence_process_model_key', return_value='pismo')
  @patch(f'{KOR}.db_get_correspondence_owner', return_value=OWNER)
  @patch(f'{KOR}.db_get_correspondence', return_value=CASE)
  def test_student_reads_own_case(self, _get_case, _owner, _key, _schema):
    result = self.client.get('/v1/edu/jform/sprawa/form', params={'id': CASE['name']})
    self.assertEqual(result.status_code, 200, result.text)
    self.assertEqual(result.json()['data'], {'tresc': 'a'})

  @patch(f'{KOR}.case_form_schema', return_value=({}, {}))
  @patch(f'{KOR}.correspondence_process_model_key', return_value='')
  @patch(f'{KOR}.db_create_correspondence', return_value=CASE)
  @patch(f'{KOR}.db_find_student', return_value={'name': 'STU-0001'})
  def test_student_creates_case_for_self_only(self, find, create, _key, _schema):
    result = self.client.post('/v1/edu/jform/sprawa/new',
                              params={'formtype': 'Pismo ogólne', 'student': '99999'})
    self.assertEqual(result.json()['code'], 0, result.text)
    find.assert_called_once_with(email='jan@example.com')
    self.assertEqual(create.call_args.kwargs['student'], 'STU-0001')

  @patch(f'{KOR}.db_upload_correspondence_file', return_value={'name': 'F-1'})
  @patch(f'{KOR}.db_get_correspondence_owner', return_value=OWNER)
  @patch(f'{KOR}.db_find_student', return_value={'name': 'STU-0002'})
  @patch(f'{KOR}.db_get_correspondence', return_value=CASE)
  def test_attach_to_foreign_case_is_403(self, _get_case, _find, _owner, upload):
    self.as_student('obcy@example.com')
    result = self.client.post('/v1/edu/jform/sprawa/attach',
                              data={'id': CASE['name'], 'att_name': 'a.pdf'},
                              files={'file': ('a.pdf', b'%PDF', 'application/pdf')})
    self.assertEqual(result.status_code, 403, result.text)
    upload.assert_not_called()

  @patch(f'{STU}.db_get_studenci_aktywni', return_value=[])
  def test_student_list_requires_staff(self, _list):
    self.assertEqual(self.client.get('/v1/edu/studenci').status_code, 403)
    self.as_staff()
    self.assertEqual(self.client.get('/v1/edu/studenci').status_code, 200)

  @patch(f'{STU}.db_get_student_studia')
  def test_student_studies_only_own(self, get_studies):
    result = self.client.get('/v1/edu/student/studia', params={'nr_albumu': '12345'})
    self.assertEqual(result.json()['code'], -2)
    get_studies.assert_not_called()

  @patch(f'{STU}.db_get_plik_wydruku', return_value=b'docx')
  @patch(f'{STU}.db_get_rej_wydruku', return_value=[])
  @patch(f'{STU}.db_email2nr_albumu', return_value='12345')
  def test_printout_outside_own_register_is_403(self, _album, _register, get_file):
    result = self.client.get('/v1/edu/plik_wydruku', params={'id': 'F-9', 'fn': 'zaświadczenie.docx'})
    self.assertEqual(result.status_code, 403, result.text)
    get_file.assert_not_called()
