# tests/test_edu_koos.py
# Karta Okresowych Osiągnięć Studenta (KOOS) z
# education.portal.api.get_semester_card - mapowanie odpowiedzi portalu
# (connector/frappe_education/evaluation.py) i endpoint GET /v1/edu/oceny/karta.
# Frappe jest mockowane; przykładowa odpowiedź ma kształt wyniku
# education/portal/semester_card.py::build_card.
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from connector.frappe_education import evaluation
from connector.frappe_education.connector import ConnectorError
from tests.base import ApiTestCase
from wf.schemas.wf_schemas import CurrentUser

DEP = 'edu.dependencies.db_email2nr_albumu'
OCENY_API = 'edu.routers.oceny_api'
ALBUM = '12345'
TERM = '2026-2027 (Zimowy)'


def portal_card() -> dict:
  lis = {'instructor': 'INS-1', 'name': 'Adam Lis', 'title': 'dr', 'display': 'dr Adam Lis'}
  return {
    'student': {'name': 'STU-1', 'album_number': ALBUM, 'first_name': 'Jan', 'last_name': 'Kowalski'},
    'program': {'name': 'Informatyka', 'program_name': 'Informatyka', 'program_abbreviation': 'INF',
                'degree_type': 'Bachelor', 'study_mode': 'Full-Time'},
    'program_enrollment': 'PE-1',
    'academic_year': '2025-2026',
    'academic_term': {'name': TERM, 'term_name': 'Zimowy', 'academic_year': '2025-2026'},
    'semester_number': 3, 'year_of_study': 2, 'year_of_study_roman': 'II',
    'next_semester_number': 4,
    'semester_status': {'name': 'SAS-1', 'academic_year': '2025-2026', 'academic_term': TERM,
                        'semester_number': 3, 'status': 'Passed', 'is_paid': 1,
                        'counts_towards_average': 1, 'decision_date': '2026-02-20'},
    'passing_percentage': 50, 'generated_on': '2026-11-08',
    'courses': [
      {'code': 'ALG', 'name': 'Algorytmy', 'class_type': 'Lecture', 'passing_type': 'Graded',
       'ects': 6, 'plan_semester': 3, 'required': True, 'category': 'program',
       'additional': False, 'lecturer': lis, 'lecturers': [lis],
       'attempts': [
         {'number': 1, 'label': 'I', 'assessment_group': 'Termin I', 'assessment_result': 'AR-1',
          'assessment_plan': 'AP-1', 'date': '2026-01-20', 'score': 30, 'maximum': 100,
          'percentage': 30.0, 'grade': '2', 'passed': False, 'examiner': 'INS-1'},
         {'number': 2, 'label': 'II', 'assessment_group': 'Poprawka', 'assessment_result': 'AR-2',
          'assessment_plan': 'AP-2', 'date': '2026-02-10', 'score': 70, 'maximum': 100,
          'percentage': 70.0, 'grade': '4,5', 'passed': True, 'examiner': 'INS-1'}],
       'final': None, 'grade': '4,5', 'passed': True},
      {'code': 'MAT', 'name': 'Matematyka', 'class_type': 'Lecture', 'passing_type': 'Graded',
       'ects': 5, 'plan_semester': 1, 'required': True, 'category': 'repeat',
       'additional': True, 'lecturer': None, 'lecturers': [], 'attempts': [],
       'final': None, 'grade': None, 'passed': None},
    ],
    'totals': {'courses': 2, 'passed': 1, 'failed': 0, 'not_graded': 1, 'ects_planned': 6,
               'ects_earned': 6, 'ects_additional': 5, 'ects_additional_earned': 0,
               'average_percentage': 70.0, 'average_grade': 4.5},
  }


class KartaMappingTest(unittest.TestCase):

  def test_portal_card_is_mapped_to_hub_model(self):
    karta = evaluation.karta_z_portalu(portal_card())
    self.assertEqual((karta.nr_albumu, karta.k_skrot, karta.semestr), (ALBUM, 'INF', TERM))
    self.assertEqual((karta.nr_semestru, karta.rok_studiow_rzymski, karta.na_semestr), (3, 'II', 4))
    self.assertEqual(karta.status_semestru.status, 'Passed')
    self.assertTrue(karta.status_semestru.zaplacony)

    alg, mat = karta.przedmioty
    self.assertEqual((alg.forma, alg.forma_zal, alg.ects), ('Lecture', 'Graded', 6))
    self.assertEqual(alg.wykladowca.opis, 'dr Adam Lis')
    self.assertEqual([p.termin for p in alg.podejscia], ['I', 'II'])
    self.assertEqual((alg.ocena, alg.zaliczony), ('4,5', True))
    self.assertEqual((mat.kategoria, mat.dodatkowy, mat.zaliczony), ('repeat', True, None))
    self.assertIsNone(mat.wykladowca)

    self.assertEqual(karta.sumy.ects_planowe, 6)
    self.assertEqual(karta.sumy.ects_dodatkowe, 5)
    self.assertEqual(karta.sumy.srednia_ocen, 4.5)

  @patch('connector.frappe_education.evaluation.get_connector')
  def test_db_get_karta_calls_portal_method(self, get_connector):
    get_connector.return_value.call_method.return_value = portal_card()
    karta = evaluation.db_get_karta_okresowa(ALBUM, TERM, 'Informatyka')
    get_connector.return_value.call_method.assert_called_once_with(
      'education.portal.api.get_semester_card',
      {'student': ALBUM, 'academic_term': TERM, 'program': 'Informatyka'}, http_method='POST')
    self.assertEqual(karta.kierunek, 'Informatyka')

  @patch('connector.frappe_education.evaluation.get_connector')
  def test_defaults_are_left_to_frappe(self, get_connector):
    get_connector.return_value.call_method.return_value = portal_card()
    evaluation.db_get_karta_okresowa(ALBUM)
    self.assertEqual(get_connector.return_value.call_method.call_args[0][1], {'student': ALBUM})


class EduKartyLegacyTest(unittest.TestCase):
  """EduKarty(rok, letni).db_get_student(kierunek_skrot, nr_albumu) - słownik karty."""

  def connector(self) -> MagicMock:
    connector = MagicMock()

    def get_list(doctype, **kwargs):
      if doctype == 'Program':
        return [{'name': 'Informatyka', 'program_name': 'Informatyka', 'program_abbreviation': 'INF'}]
      if doctype == 'Academic Term':
        return [{'name': '2024-2025 (Zimowy)', 'academic_year': '2024-2025'},
                {'name': TERM, 'academic_year': '2025-2026'}]
      return []
    connector.get_list.side_effect = get_list
    connector.call_method.return_value = portal_card()
    return connector

  @patch('connector.frappe_education.evaluation.get_connector')
  def test_resolves_term_and_program(self, get_connector):
    connector = self.connector()
    get_connector.return_value = connector
    card = evaluation.EduKarty(2025, 0).db_get_student('INF', ALBUM)
    self.assertEqual(card['nr_albumu'], ALBUM)
    self.assertEqual(card['przedmioty'][0]['kod'], 'ALG')
    params = connector.call_method.call_args[0][1]
    self.assertEqual((params['academic_term'], params['program']), (TERM, 'Informatyka'))

  @patch('connector.frappe_education.evaluation.get_connector')
  def test_missing_enrollment_returns_empty_dict(self, get_connector):
    connector = self.connector()
    connector.call_method.side_effect = ConnectorError(
      'frappe_education call_method(...) -> 404: {"exc_type": "DoesNotExistError"}')
    get_connector.return_value = connector
    self.assertEqual(evaluation.EduKarty(2025, 0).db_get_student('INF', ALBUM), {})

  @patch('connector.frappe_education.evaluation.get_connector')
  def test_other_errors_are_raised(self, get_connector):
    connector = self.connector()
    connector.call_method.side_effect = ConnectorError('frappe_education ... -> 500: boom')
    get_connector.return_value = connector
    with self.assertRaises(ConnectorError):
      evaluation.EduKarty(2025, 0).db_get_student('INF', ALBUM)


class KartaApiTest(ApiTestCase):

  def prepare(self) -> None:
    self.user = self.create_user(email='jan@example.com', name='jan')
    self.current_user = CurrentUser(id=self.user.id, sub='sub-1', username='jan',
                                    email='jan@example.com', display_name='jan')

  @patch(DEP, return_value=ALBUM)
  @patch(f'{OCENY_API}.db_get_karta_okresowa')
  def test_student_reads_own_card(self, get_karta, _album):
    get_karta.return_value = evaluation.karta_z_portalu(portal_card())
    result = self.client.get('/v1/edu/oceny/karta', params={'semestr': TERM})
    self.assertEqual(result.status_code, 200, result.text)
    body = result.json()
    self.assertEqual(body['code'], 0, body)
    self.assertEqual(body['karta']['przedmioty'][0]['wykladowca']['opis'], 'dr Adam Lis')
    get_karta.assert_called_once_with(ALBUM, TERM, None)

  @patch(DEP, return_value=ALBUM)
  @patch(f'{OCENY_API}.db_get_karta_okresowa')
  def test_student_cannot_read_foreign_card(self, get_karta, _album):
    result = self.client.get('/v1/edu/oceny/karta', params={'nr_albumu': '999'})
    self.assertEqual(result.status_code, 403, result.text)
    get_karta.assert_not_called()

  @patch(f'{OCENY_API}.db_get_karta_okresowa')
  def test_staff_reads_card_of_any_student(self, get_karta):
    get_karta.return_value = evaluation.karta_z_portalu(portal_card())
    self.current_user = CurrentUser(id=self.user.id, sub='sub-1', username='ewa',
                                    email='ewa@example.com', display_name='ewa',
                                    groups=frozenset({'dziekanat'}))
    result = self.client.get('/v1/edu/oceny/karta',
                             params={'nr_albumu': '999', 'kierunek': 'Informatyka'})
    self.assertEqual(result.status_code, 200, result.text)
    get_karta.assert_called_once_with('999', None, 'Informatyka')

  @patch(DEP, return_value=ALBUM)
  @patch(f'{OCENY_API}.db_get_karta_okresowa', side_effect=ConnectorError('-> 502'))
  def test_frappe_error_is_reported(self, _get_karta, _album):
    body = self.client.get('/v1/edu/oceny/karta').json()
    self.assertEqual(body['code'], -1)
    self.assertIsNone(body['karta'])
