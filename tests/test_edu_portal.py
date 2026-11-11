# tests/test_edu_portal.py
# testy serwisu studenckiego w module edu - plan, oceny,
# frekwencja, opłaty i mLegitymacja. Funkcje konektora Frappe Education są
# mockowane, więc testy nie wymagają żywego Frappe.
from __future__ import annotations

from unittest.mock import patch

from connector.frappe_education.connector import ConnectorError
from connector.frappe_education.frekwencja import EduZapisFrekwencji
from connector.frappe_education.oceny import (
  EduKryterium,
  EduOcena,
  EduOceny,
  EduPlanOceniania,
  EduPodejscie,
  EduPrzedmiot,
  EduSrednia,
  EduStudentGrupy,
  EduZapisOcen,
)
from connector.frappe_education.oplaty import EduOplata, EduOplaty
from connector.frappe_education.plan import EduCourseRef, EduPlan, EduScheduleEntry
from connector.frappe_education.student_card import EduKodyLegitymacji, EduLegitymacja
from connector.frappe_education.studia import DaneStudenta, MojeStudia, StatusSemestru, ZapisNaKierunek
# tożsamość i role użytkownika (/v1/edu/ja).
from connector.frappe_education.tozsamosc import (
  EduTozsamosc,
  StudentTozsamosc,
  WykladowcaTozsamosc,
)
from tests.base import ApiTestCase
from wf.schemas.wf_schemas import CurrentUser

DEP = 'edu.dependencies.db_email2nr_albumu'
PLAN_API = 'edu.routers.plan_api'
OCENY_API = 'edu.routers.oceny_api'
OPLATY_API = 'edu.routers.oplaty_api'
FREK_API = 'edu.routers.frekwencja_api'
LEG_API = 'edu.routers.legitymacja_api'

ALBUM = '12345'
ACADEMIC_YEAR = '2025-2026'


def przykladowy_plan() -> EduPlan:
  return EduPlan(
    od='2026-11-09', do='2026-11-15',
    wpisy=[EduScheduleEntry(
      id='CS-1', data='2026-11-10', dzien_tygodnia=1, od='08:00:00', do='09:30:00',
      przedmiot=EduCourseRef(code='INF-201', name='Bazy danych', type='Lecture'),
      grupa='INF-2A', sala='204', wykladowca='dr A. Nowak')])


def przykladowe_oceny() -> EduOceny:
  return EduOceny(
    student=ALBUM, prog_zaliczenia=50,
    przedmioty=[EduOcena(
      przedmiot=EduPrzedmiot(code='INF-201', name='Bazy danych', ects=5),
      final=EduPodejscie(id='AR-1', procent=78.0, ocena='4.0', zaliczone=True),
      podejscia=[EduPodejscie(id='AR-1', procent=78.0, ocena='4.0', zaliczone=True)],
      zaliczone=True, najlepszy_procent=78.0)],
    srednia=EduSrednia(wartosc=78.0, przedmioty=1, zaliczone=1),
  )


def przykladowe_oplaty() -> EduOplaty:
  return EduOplaty(
    student=ALBUM,
    oplaty=[EduOplata(id='EDU-FEE-1', termin='2026-11-30', kwota=1200, zaleglosc=1200,
                      stan='due', waluta='PLN')],
    suma_zaleglosci=1200, suma_zaplacona=0, suma_przeterminowana=0, wymaga_zaplaty=True)


class EduPortalTest(ApiTestCase):

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

  def test_openapi_lists_portal_endpoints(self):
    paths = self.client.get('/v1/edu/openapi.json').json()['paths']
    for path in ('/plan', '/plan/wykladowca', '/oceny', '/frekwencja',
                 '/oplaty', '/legitymacja', '/legitymacja/kody'):
      self.assertIn(path, paths)

  @patch(f'{DEP}', return_value=ALBUM)
  @patch(f'{PLAN_API}.db_get_plan_studenta', return_value=przykladowy_plan())
  def test_student_gets_own_plan(self, get_plan, get_album):
    result = self.client.get('/v1/edu/plan')
    self.assertEqual(result.status_code, 200, result.text)
    body = result.json()
    self.assertEqual(body['code'], 0, body)
    self.assertEqual(body['plan']['wpisy'][0]['id'], 'CS-1')
    self.assertEqual(body['plan']['wpisy'][0]['przedmiot']['code'], 'INF-201')
    get_plan.assert_called_once_with(ALBUM, None, None, None)

  @patch(f'{DEP}', return_value=ALBUM)
  @patch(f'{PLAN_API}.db_get_plan_studenta')
  def test_student_cannot_read_foreign_plan(self, get_plan, get_album):
    result = self.client.get('/v1/edu/plan', params={'nr_albumu': '999'})
    self.assertEqual(result.status_code, 403, result.text)
    get_plan.assert_not_called()

  @patch(f'{PLAN_API}.db_get_plan_studenta', return_value=przykladowy_plan())
  def test_staff_reads_plan_of_any_student(self, get_plan):
    self.as_staff()
    result = self.client.get('/v1/edu/plan', params={'nr_albumu': '999'})
    self.assertEqual(result.status_code, 200, result.text)
    self.assertEqual(result.json()['code'], 0)
    get_plan.assert_called_once_with('999', None, None, None)

  @patch(f'{PLAN_API}.db_get_plan_wykladowcy', return_value=przykladowy_plan())
  def test_lecturer_plan_uses_token_email(self, get_plan):
    result = self.client.get('/v1/edu/plan/wykladowca')
    self.assertEqual(result.status_code, 200, result.text)
    self.assertEqual(result.json()['code'], 0)
    get_plan.assert_called_once_with('jan@example.com', None, None)

  @patch(f'{PLAN_API}.db_get_plan_wykladowcy')
  def test_lecturer_cannot_ask_for_foreign_email(self, get_plan):
    result = self.client.get('/v1/edu/plan/wykladowca',
                             params={'email': 'obcy@example.com'})
    self.assertEqual(result.status_code, 403, result.text)
    get_plan.assert_not_called()

  @patch(f'{DEP}', return_value=ALBUM)
  @patch(f'{OCENY_API}.db_get_oceny_studenta', return_value=przykladowe_oceny())
  def test_student_reads_own_grades(self, get_oceny, get_album):
    result = self.client.get('/v1/edu/oceny')
    self.assertEqual(result.status_code, 200, result.text)
    body = result.json()
    self.assertEqual(body['code'], 0, body)
    self.assertEqual(body['oceny']['przedmioty'][0]['przedmiot']['code'], 'INF-201')
    self.assertTrue(body['oceny']['przedmioty'][0]['zaliczone'])
    get_oceny.assert_called_once_with(ALBUM, None, None)

  @patch(f'{DEP}', return_value=ALBUM)
  @patch(f'{OPLATY_API}.db_get_oplaty_studenta', return_value=przykladowe_oplaty())
  def test_student_reads_own_fees(self, get_oplaty, get_album):
    result = self.client.get('/v1/edu/oplaty')
    self.assertEqual(result.status_code, 200, result.text)
    body = result.json()
    self.assertEqual(body['code'], 0, body)
    self.assertEqual(body['oplaty']['suma_zaleglosci'], 1200)
    self.assertTrue(body['oplaty']['wymaga_zaplaty'])

  @patch(f'{FREK_API}.db_save_lista_obecnosci',
         return_value=EduZapisFrekwencji(grupa='INF-2A', data='2026-11-10',
                                         zapisane=['EDU-ATT-1']))
  def test_lecturer_saves_attendance_sheet(self, save):
    body = {'data': '2026-11-10',
            'wpisy': [{'student': 'STU-1', 'status': 'Present'},
                      {'student': 'STU-2', 'status': 'Absent'}]}
    result = self.client.post('/v1/edu/frekwencja/grupa',
                              params={'grupa': 'INF-2A'}, json=body)
    self.assertEqual(result.status_code, 200, result.text)
    self.assertEqual(result.json()['code'], 0, result.text)
    args = save.call_args
    self.assertEqual(args.args[0], 'INF-2A')
    self.assertEqual(args.args[1], 'jan@example.com')
    self.assertEqual(args.args[2], '2026-11-10')
    self.assertEqual([w['status'] for w in args.args[3]], ['Present', 'Absent'])

  @patch(f'{DEP}', return_value=ALBUM)
  @patch(f'{LEG_API}.db_get_legitymacja',
         return_value=EduLegitymacja(student=ALBUM, stan='issued',
                                     zamowienie='EDU-SCO-1', kody_dostepne=True))
  def test_student_reads_card_state(self, get_card, get_album):
    result = self.client.get('/v1/edu/legitymacja')
    self.assertEqual(result.status_code, 200, result.text)
    body = result.json()
    self.assertEqual(body['legitymacja']['stan'], 'issued')
    self.assertTrue(body['legitymacja']['kody_dostepne'])
    self.assertNotIn('qr', body['legitymacja'])


  @patch(f'{DEP}', return_value=ALBUM)
  @patch(f'{LEG_API}.db_odswiez_legitymacje',
         return_value=EduLegitymacja(student=ALBUM, stan='issued',
                                     zamowienie='EDU-SCO-1', kody_dostepne=True))
  def test_student_refreshes_card_state(self, refresh, get_album):
    result = self.client.post('/v1/edu/legitymacja/odswiez')
    self.assertEqual(result.status_code, 200, result.text)
    self.assertEqual(result.json()['code'], 0, result.text)
    self.assertEqual(result.json()['legitymacja']['stan'], 'issued')
    refresh.assert_called_once_with(ALBUM)

  @patch(f'{DEP}', return_value=ALBUM)
  @patch(f'{LEG_API}.db_pobierz_kody_legitymacji',
         return_value=EduKodyLegitymacji(zamowienie='EDU-SCO-1', qr='QR',
                                         kod_aktywacyjny='KOD', wazny_do='2026-12-31'))
  def test_student_reveals_card_codes(self, get_codes, get_album):
    result = self.client.post('/v1/edu/legitymacja/kody')
    self.assertEqual(result.status_code, 200, result.text)
    body = result.json()
    self.assertEqual(body['kody']['qr'], 'QR')
    get_codes.assert_called_once_with(ALBUM)

  @patch(f'{DEP}', return_value=ALBUM)
  @patch(f'{LEG_API}.db_pobierz_kody_legitymacji',
         side_effect=ConnectorError('frappe_education call_method() -> 417: not ready'))
  def test_card_codes_error_is_reported(self, get_codes, get_album):
    result = self.client.post('/v1/edu/legitymacja/kody')
    self.assertEqual(result.status_code, 200, result.text)
    body = result.json()
    self.assertEqual(body['code'], -1)
    self.assertIn('not ready', body['message'])


def przykladowy_plan_ocen() -> EduPlanOceniania:
  return EduPlanOceniania(
    id='AP-1', nazwa='Kolokwium 1', przedmiot='INF-201', grupa='INF-2A',
    skala='Skala 2-5', maksimum=20, zatwierdzony=False,
    kryteria=[EduKryterium(kryterium='Test', maksimum=20, waga=100)],
    wystawione=0)


def przykladowy_student_grupy() -> EduStudentGrupy:
  return EduStudentGrupy(student='STU-1', nazwisko='Jan Nowak',
                         nr_albumu='12345', nr_w_grupie=1)



# 2026-11-08: "Moje studia" - profil, zapisy i statusy semestrów.
STUDIA_API = 'edu.routers.studia_api'


def przykladowe_studia() -> MojeStudia:
  return MojeStudia(
    student=DaneStudenta(student='STU-1', imie='Jan', nazwisko='Nowak',
                         nr_albumu=ALBUM, email='jan@example.com',
                         nr_dowodu='****3456', miasto='Warszawa'),
    zapisy=[ZapisNaKierunek(id='PE-1', kierunek='Informatyka',
                            kierunek_nazwa='Informatyka', kierunek_skrot='INF',
                            stopien='Bachelor', tryb='Full-Time',
                            rok_akademicki=ACADEMIC_YEAR, semestr='Winter',
                            platne=False)],
    semestry=[StatusSemestru(id='SAS-1', rok_akademicki=ACADEMIC_YEAR,
                             nr_semestru=3, status='Passed', zaplacony=True)],
    aktualny_zapis=ZapisNaKierunek(id='PE-1', kierunek='Informatyka'),
    aktualny_semestr=StatusSemestru(id='SAS-1', nr_semestru=3, status='Passed'),
    ustawienia={'current_academic_year': ACADEMIC_YEAR},
  )


class EduStudiaTest(ApiTestCase):

  def prepare(self) -> None:
    self.user = self.create_user(email='jan@example.com', name='jan')
    self.current_user = CurrentUser(
      id=self.user.id, sub='sub-1', username='jan', email='jan@example.com',
      display_name='jan')

  def as_staff(self) -> None:
    self.current_user = CurrentUser(
      id=self.user.id, sub='sub-1', username='ewa', email='ewa@example.com',
      display_name='ewa', groups=frozenset({'dziekanat'}))

  @patch(f'{DEP}', return_value=ALBUM)
  @patch(f'{STUDIA_API}.db_get_moje_studia', return_value=przykladowe_studia())
  def test_student_reads_own_studies(self, get_studia, get_album):
    result = self.client.get('/v1/edu/studia')
    self.assertEqual(result.status_code, 200, result.text)
    body = result.json()
    self.assertEqual(body['code'], 0, body)
    self.assertEqual(body['studia']['student']['nr_albumu'], ALBUM)
    self.assertEqual(body['studia']['zapisy'][0]['kierunek_skrot'], 'INF')
    self.assertEqual(body['studia']['semestry'][0]['nr_semestru'], 3)
    # numer dokumentu jest zamaskowany po stronie Frappe
    self.assertTrue(body['studia']['student']['nr_dowodu'].endswith('3456'))
    get_studia.assert_called_once_with(ALBUM)

  @patch(f'{DEP}', return_value=ALBUM)
  @patch(f'{STUDIA_API}.db_get_moje_studia')
  def test_student_cannot_read_foreign_studies(self, get_studia, get_album):
    result = self.client.get('/v1/edu/studia', params={'nr_albumu': '999'})
    self.assertEqual(result.status_code, 403, result.text)
    get_studia.assert_not_called()

  @patch(f'{STUDIA_API}.db_get_moje_studia', return_value=przykladowe_studia())
  def test_staff_reads_studies_by_album(self, get_studia):
    self.as_staff()
    result = self.client.get('/v1/edu/studia', params={'nr_albumu': '999'})
    self.assertEqual(result.status_code, 200, result.text)
    get_studia.assert_called_once_with('999')

# 2026-11-08: oceny wykładowcy - siatka ocen grupy i zapis wyników.
class EduPortalOcenyTest(ApiTestCase):

  def prepare(self) -> None:
    self.user = self.create_user(email='jan@example.com', name='jan')
    self.current_user = CurrentUser(
      id=self.user.id, sub='sub-1', username='jan', email='jan@example.com',
      display_name='jan')

  def as_staff(self) -> None:
    self.current_user = CurrentUser(
      id=self.user.id, sub='sub-1', username='ewa', email='ewa@example.com',
      display_name='ewa', groups=frozenset({'dziekanat'}))

  @patch(f'{OCENY_API}.db_get_oceny_grupy',
         return_value=([przykladowy_plan_ocen()], [przykladowy_student_grupy()]))
  def test_lecturer_reads_group_grade_grid(self, get_grid):
    result = self.client.get('/v1/edu/oceny/grupa', params={'grupa': 'INF-2A'})
    self.assertEqual(result.status_code, 200, result.text)
    body = result.json()
    self.assertEqual(body['code'], 0, body)
    self.assertEqual(body['plany'][0]['id'], 'AP-1')
    self.assertEqual(body['plany'][0]['kryteria'][0]['kryterium'], 'Test')
    self.assertEqual(body['studenci'][0]['nr_albumu'], '12345')
    get_grid.assert_called_once_with('INF-2A', 'jan@example.com')

  @patch(f'{OCENY_API}.db_get_oceny_grupy')
  def test_lecturer_cannot_read_foreign_email(self, get_grid):
    result = self.client.get('/v1/edu/oceny/grupa',
                             params={'grupa': 'INF-2A', 'email': 'obcy@example.com'})
    self.assertEqual(result.status_code, 403, result.text)
    get_grid.assert_not_called()

  @patch(f'{OCENY_API}.db_save_oceny_grupy',
         return_value=EduZapisOcen(grupa='INF-2A', assessment_plan='AP-1',
                                   zapisane=[{'student': 'STU-1',
                                              'assessment_result': 'AR-1',
                                              'docstatus': 0}], count=1))
  def test_lecturer_saves_grades(self, save):
    body = {
      'assessment_plan': 'AP-1',
      'wpisy': [{'student': 'STU-1', 'punkty': {'Test': 18}, 'suma': 18,
                 'komentarz': 'dobrze'}],
      'zatwierdz': False,
    }
    result = self.client.post('/v1/edu/oceny/grupa',
                              params={'grupa': 'INF-2A'}, json=body)
    self.assertEqual(result.status_code, 200, result.text)
    self.assertEqual(result.json()['code'], 0, result.text)
    args = save.call_args
    self.assertEqual(args.args[0], 'INF-2A')
    self.assertEqual(args.args[1], 'jan@example.com')
    self.assertEqual(args.args[2], 'AP-1')
    self.assertEqual(args.args[3][0]['details'], {'Test': 18})
    self.assertEqual(args.args[3][0]['total_score'], 18)
    self.assertFalse(args.args[4])

  @patch(f'{OCENY_API}.db_save_oceny_grupy',
         side_effect=ConnectorError('frappe_education call_method() -> 417: '
                                    'Student STU-9 is not an active member'))
  def test_saving_foreign_student_is_reported(self, save):
    body = {'assessment_plan': 'AP-1',
            'wpisy': [{'student': 'STU-9', 'punkty': {'Test': 1}}]}
    result = self.client.post('/v1/edu/oceny/grupa',
                              params={'grupa': 'INF-2A'}, json=body)
    self.assertEqual(result.status_code, 200, result.text)
    self.assertEqual(result.json()['code'], -1)
    self.assertIn('not an active member', result.json()['message'])

  def test_staff_must_name_the_lecturer(self):
    self.as_staff()
    result = self.client.get('/v1/edu/oceny/grupa', params={'grupa': 'INF-2A'})
    self.assertEqual(result.status_code, 400, result.text)


# tożsamość i role (GET /v1/edu/ja) - to na tej podstawie
# interfejs pokazuje rolę użytkownika i dobiera menu.
JA_API = 'edu.routers.ja_api'


def tozsamosc_studenta(album: str = ALBUM) -> EduTozsamosc:
  return EduTozsamosc(
    email='jan@example.com',
    student=StudentTozsamosc(student='STU-1', nr_albumu=album, imie='Jan',
                             nazwisko='Nowak', imie_nazwisko='Jan Nowak',
                             email='jan@example.com'))


def tozsamosc_wykladowcy() -> EduTozsamosc:
  return EduTozsamosc(
    email='jan@example.com',
    wykladowca=WykladowcaTozsamosc(instructor='INS-1', imie_nazwisko='dr Jan Nowak',
                                   wydzial='Informatyka', email='jan@example.com'))


class EduTozsamoscTest(ApiTestCase):

  def prepare(self) -> None:
    self.user = self.create_user(email='jan@example.com', name='jan')
    self.as_student()

  def as_student(self, email: str = 'jan@example.com') -> None:
    self.current_user = CurrentUser(
      id=self.user.id, sub='sub-1', username='jan', email=email, display_name='jan')

  def as_group(self, group: str, email: str = 'ewa@example.com') -> None:
    self.current_user = CurrentUser(
      id=self.user.id, sub='sub-1', username='ewa', email=email, display_name='ewa',
      groups=frozenset({group}))

  def as_role(self, role: str, email: str = 'adam@example.com') -> None:
    self.current_user = CurrentUser(
      id=self.user.id, sub='sub-1', username='adam', email=email, display_name='adam',
      roles=frozenset({role}))

  def test_openapi_lists_identity_endpoint(self):
    paths = self.client.get('/v1/edu/openapi.json').json()['paths']
    self.assertIn('/ja', paths)

  @patch(f'{JA_API}.db_get_tozsamosc', return_value=tozsamosc_studenta())
  def test_student_gets_student_role(self, tozsamosc):
    result = self.client.get('/v1/edu/ja')
    self.assertEqual(result.status_code, 200, result.text)
    body = result.json()
    self.assertEqual(body['code'], 0, body)
    self.assertEqual(body['ostrzezenie'], '')
    uzytkownik = body['uzytkownik']
    self.assertEqual(uzytkownik['role'], ['student'])
    self.assertEqual(uzytkownik['student']['nr_albumu'], ALBUM)
    self.assertIsNone(uzytkownik['wykladowca'])
    self.assertFalse(uzytkownik['dziekanat'])
    self.assertFalse(uzytkownik['administrator'])
    tozsamosc.assert_called_once_with('jan@example.com')

  @patch(f'{JA_API}.db_get_tozsamosc', return_value=tozsamosc_wykladowcy())
  def test_lecturer_gets_lecturer_role(self, tozsamosc):
    body = self.client.get('/v1/edu/ja').json()
    self.assertEqual(body['uzytkownik']['role'], ['wykladowca'])
    self.assertEqual(body['uzytkownik']['wykladowca']['wydzial'], 'Informatyka')

  @patch(f'{JA_API}.db_get_tozsamosc')
  def test_lecturer_and_student_get_both_roles(self, tozsamosc):
    tozsamosc.return_value = tozsamosc_studenta()
    tozsamosc.return_value.wykladowca = tozsamosc_wykladowcy().wykladowca
    body = self.client.get('/v1/edu/ja').json()
    self.assertEqual(body['uzytkownik']['role'], ['wykladowca', 'student'])

  @patch(f'{JA_API}.db_get_tozsamosc', return_value=tozsamosc_studenta())
  def test_staff_keeps_deans_office_role(self, tozsamosc):
    self.as_group('dziekanat')
    body = self.client.get('/v1/edu/ja').json()
    self.assertEqual(body['uzytkownik']['role'], ['dziekanat', 'student'])
    self.assertTrue(body['uzytkownik']['dziekanat'])

  @patch(f'{JA_API}.db_get_tozsamosc', return_value=tozsamosc_studenta())
  def test_administrator_comes_first(self, tozsamosc):
    self.as_role('galwork-admin')
    body = self.client.get('/v1/edu/ja').json()
    self.assertEqual(body['uzytkownik']['role'], ['administrator', 'student'])
    self.assertTrue(body['uzytkownik']['administrator'])

  @patch(f'{JA_API}.db_get_tozsamosc', return_value=EduTozsamosc(email='kto@example.com'))
  def test_person_without_education_record_is_a_candidate(self, tozsamosc):
    self.as_student(email='kto@example.com')
    body = self.client.get('/v1/edu/ja').json()
    self.assertEqual(body['uzytkownik']['role'], ['kandydat'])
    self.assertIsNone(body['uzytkownik']['student'])

  @patch(f'{JA_API}.db_get_tozsamosc',
         side_effect=ConnectorError('frappe_education call_method() -> 503'))
  def test_frappe_failure_keeps_keycloak_roles(self, tozsamosc):
    self.as_group('dziekanat')
    body = self.client.get('/v1/edu/ja').json()
    self.assertEqual(body['code'], 0, body)
    self.assertEqual(body['uzytkownik']['role'], ['dziekanat'])
    self.assertIn('503', body['ostrzezenie'])

  @patch(f'{JA_API}.db_get_tozsamosc',
         side_effect=ConnectorError('frappe_education call_method() -> 503'))
  def test_frappe_failure_leaves_roles_empty(self, tozsamosc):
    body = self.client.get('/v1/edu/ja').json()
    self.assertEqual(body['uzytkownik']['role'], [])
    self.assertTrue(body['ostrzezenie'])
