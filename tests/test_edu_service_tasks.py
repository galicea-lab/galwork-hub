# 2026-11-15: krok systemowy `edu.studia-studenta` (wf/services/edu_service_tasks.py)
# i etykiety słowników Frappe ze szczegółami. Bez aplikacji (main) i bez Frappe.
import unittest
from types import SimpleNamespace
from unittest import mock

from connector.frappe_education.studia import DaneStudenta, MojeStudia, StatusSemestru, ZapisNaKierunek
from wf.services import dictionary_service, edu_service_tasks, service_tasks
from wf.services.service_tasks import ServiceContext, ServiceTaskError


def _db(entry):
  db = mock.Mock()
  db.execute.return_value.scalars.return_value.first.return_value = entry
  return db


def _studia():
  return MojeStudia(
    student=DaneStudenta(student='EDU-STU-1', nazwisko_pelne='Anna Nowak', nr_albumu='12345',
                         email='a.nowak@uczelnia.pl', telefon='600100200', adres='ul. Polna 1',
                         miasto='Kraków', data_urodzenia='2003-05-01', obywatelstwo='polskie',
                         data_przyjecia='2023-10-01'),
    zapisy=[
      ZapisNaKierunek(id='PE-1', kierunek='INF', kierunek_nazwa='Informatyka', stopien='I stopnia',
                      tryb='Full-Time', rok_akademicki='2025/26', semestr='2025/26 zimowy', platne=False),
      ZapisNaKierunek(id='PE-2', kierunek='ZAR', kierunek_nazwa='Zarządzanie', tryb='Part-Time',
                      rok_akademicki='2025/26', semestr='2025/26 zimowy', platne=True),
    ],
    semestry=[StatusSemestru(id='S5', rok_akademicki='2025/26', semestr='2025/26 zimowy', nr_semestru=5)],
  )


class StudySectionsTest(unittest.TestCase):
  def test_selected_enrollment_fills_sections(self):
    sections = edu_service_tasks.study_sections(_studia(), 'PE-1')
    self.assertEqual(sections['DANE STUDENTA']['Imię i nazwisko'], 'Anna Nowak')
    self.assertEqual(sections['DANE STUDENTA']['Adres zamieszkania'], 'ul. Polna 1, Kraków')
    tok = sections['TOK STUDIÓW']
    self.assertEqual(tok['Kierunek'], 'Informatyka')
    self.assertEqual(tok['Tryb studiów'], 'Full-Time')
    self.assertEqual(tok['Semestr'], 5)
    self.assertEqual(tok['Rok studiów'], 3)
    self.assertEqual(tok['Zasady studiowania'], 'nieodplatne')

  def test_second_enrollment_of_same_student(self):
    tok = edu_service_tasks.study_sections(_studia(), 'PE-2')['TOK STUDIÓW']
    self.assertEqual(tok['Kierunek'], 'Zarządzanie')
    self.assertEqual(tok['Zasady studiowania'], 'odplatne')

  def test_unknown_enrollment(self):
    with self.assertRaises(ServiceTaskError):
      edu_service_tasks.study_sections(_studia(), 'PE-9')


class KindAttributesTest(unittest.TestCase):
  def test_attributes_from_extra(self):
    entry = SimpleNamespace(extra={'adresat': 'dziekan', 'opinia_promotora': True,
                                   'opinia_dyrektora': False, 'opinia_dziekana': True})
    self.assertEqual(edu_service_tasks.kind_attributes(_db(entry), 'rodzaj-pisma-studenta', 'TZPD'),
                     {'adresat': 'dziekan', 'opinia_promotora': True,
                      'opinia_dyrektora': False, 'opinia_dziekana': True})

  def test_missing_attributes_are_cautious(self):
    attrs = edu_service_tasks.kind_attributes(_db(SimpleNamespace(extra={})), 'x', 'POD')
    self.assertEqual(attrs, {'opinia_promotora': False,
                             'opinia_dyrektora': True, 'opinia_dziekana': True})

  def test_unknown_kind(self):
    with self.assertRaises(ServiceTaskError):
      edu_service_tasks.kind_attributes(_db(None), 'rodzaj-pisma-studenta', 'XYZ')


class HandlerTest(unittest.TestCase):
  def _ctx(self, data, entry):
    return ServiceContext(db=_db(entry), instance=mock.Mock(), actor=SimpleNamespace(username='anowak'),
                          element_id='ServiceTask_dane', name='Pobranie danych studenta', data=data)

  def test_registered(self):
    self.assertIs(service_tasks.get_handler('edu.studia-studenta'), edu_service_tasks.fill_student_data)

  def test_done_with_sections_and_routing(self):
    connector = mock.Mock()
    connector.get_doc.side_effect = lambda doctype, name: (
      {'student': 'EDU-STU-1'} if doctype == 'Program Enrollment' else {'album_number': '12345'})
    entry = SimpleNamespace(extra={'adresat': 'dyrektor', 'opinia_promotora': False,
                                   'opinia_dyrektora': True, 'opinia_dziekana': False})
    with mock.patch.object(edu_service_tasks, 'get_connector', return_value=connector), \
         mock.patch.object(edu_service_tasks, 'db_get_moje_studia', return_value=_studia()) as studia:
      result = edu_service_tasks.fill_student_data(
        self._ctx({'Studia studenta': 'PE-1', 'Rodzaj pisma': 'F-ISS'}, entry))
    studia.assert_called_once_with('12345')
    self.assertTrue(result.completed)
    self.assertEqual(result.data['wnioskodawca'], 'anowak')
    self.assertEqual(result.data['adresat'], 'dyrektor')
    self.assertFalse(result.data['opinia_dziekana'])
    self.assertEqual(result.data['TOK STUDIÓW']['Semestr'], 5)

  def test_missing_input(self):
    with self.assertRaises(ServiceTaskError):
      edu_service_tasks.fill_student_data(self._ctx({'Rodzaj pisma': 'F-ISS'}, None))


class FrappeLabelTest(unittest.TestCase):
  def test_detail_fields_and_filters(self):
    dictionary = dictionary_service.FRAPPE_DICTIONARIES['studia-studenta']
    connector = mock.Mock()
    connector.get_list.return_value = [
      {'name': 'PE-1', 'student_name': 'Anna Nowak', 'program': 'Informatyka', 'academic_year': '2025/26'},
      {'name': 'PE-3', 'student_name': None, 'program': None, 'academic_year': None},
    ]
    with mock.patch.object(dictionary_service, 'get_frappe_connector', return_value=connector):
      options = dictionary_service._frappe_options(dictionary, 'Now', 10)
    self.assertEqual([o.label for o in options], ['Anna Nowak (Informatyka, 2025/26)', 'PE-3'])
    kwargs = connector.get_list.call_args.kwargs
    self.assertEqual(kwargs['filters'], [['docstatus', '!=', 2], ['student_name', 'like', '%Now%']])
    self.assertEqual(kwargs['fields'], ['name', 'student_name', 'program', 'academic_year'])

  def test_academic_year_and_term(self):
    self.assertEqual(dictionary_service.FRAPPE_DICTIONARIES['rok-akademicki'].doctype, 'Academic Year')
    self.assertEqual(dictionary_service.FRAPPE_DICTIONARIES['semestr-akademicki'].doctype, 'Academic Term')


if __name__ == '__main__':
  unittest.main()
