# tests/test_kalendarz.py
# moduł kalendarz (/v1/kalendarz) - łączenie źródeł, format
# zdarzeń (FullCalendar), kontrola dostępu, wyniki częściowe, terminy zadań
# z bazy huba i kalendarz uczelni z ICS. Frappe jest mockowane.
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from urllib.parse import urlsplit
from unittest.mock import patch

from connector.frappe_education.connector import ConnectorError
from connector.frappe_education.kalendarz import EduKalendarz, EduZdarzenie
from connector.frappe_education.plan import EduCourseRef, EduPlan, EduScheduleEntry
from connector.frappe_education.tozsamosc import EduTozsamosc, StudentTozsamosc
from kalendarz.config import KalendarzSettings
from kalendarz import subskrypcja as subskrypcje
from kalendarz.zrodla import ics
from tests.base import ApiTestCase
from wf.schemas.wf_schemas import CurrentUser

EDU = 'kalendarz.zrodla.edu'
ROUTER = 'kalendarz.router'

PLAN = EduPlan(od='2027-01-25', do='2027-01-31', wpisy=[
  EduScheduleEntry(id='CS-1', data='2027-01-26', od='8:00:00', do='9:30:00',
                   przedmiot=EduCourseRef(code='MAT', name='Matematyka', type='Lecture'),
                   grupa='G1', sala='A-1', wykladowca='dr X'),
])

KALENDARZ = EduKalendarz(od='2027-01-25', do='2027-01-31', strefa='Europe/Warsaw', zdarzenia=[
  EduZdarzenie(id='Academic Term:2026-27 (zimowy):exam_session', typ='exam_session',
               tytul='2026-27 (zimowy)', caly_dzien=True, start='2027-01-25', koniec='2027-02-10'),
  EduZdarzenie(id='Assessment Plan:AP-1', typ='exam', tytul='', caly_dzien=False,
               start='2027-01-28T09:00:00', koniec='2027-01-28T10:30:00',
               szczegoly={'course': {'code': 'MAT', 'name': 'Matematyka'}, 'room': 'B-2'}),
  EduZdarzenie(id='Fees:FEE-1', typ='fee_due', tytul='FEE-1', start='2027-01-31', koniec='2027-01-31',
               szczegoly={'state': 'due', 'outstanding': 1200, 'currency': 'PLN'}),
])

ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//test//PL
BEGIN:VEVENT
UID:dzien-rektorski@test
DTSTART;VALUE=DATE:20270127
DTEND;VALUE=DATE:20270128
SUMMARY:Dzień rektorski
END:VEVENT
BEGIN:VEVENT
UID:senat@test
DTSTART;TZID=Europe/Warsaw:20270104T100000
DTEND;TZID=Europe/Warsaw:20270104T120000
RRULE:FREQ=WEEKLY;COUNT=6
SUMMARY:Posiedzenie senatu
LOCATION:Aula
END:VEVENT
BEGIN:VEVENT
UID:odwolane@test
DTSTART;VALUE=DATE:20270129
SUMMARY:Odwołane
STATUS:CANCELLED
END:VEVENT
END:VCALENDAR
"""

ZAKRES = {'od': '2027-01-25', 'do': '2027-01-31'}


def _settings(**kwargs) -> KalendarzSettings:
  return KalendarzSettings(**kwargs)


class KalendarzTestBase(ApiTestCase):
  """Wspólne przygotowanie i pomocniki (bez testów)."""

  def prepare(self) -> None:
    self.user = self.create_user(email='jan@example.com', name='jan')
    self.as_student()
    patcher = patch(f'{ROUTER}.get_settings', return_value=_settings())
    patcher.start()
    self.addCleanup(patcher.stop)

  def as_student(self) -> None:
    self.current_user = CurrentUser(id=self.user.id, sub='sub-1', username='jan',
                                    email='jan@example.com', display_name='jan')

  def as_staff(self) -> None:
    self.current_user = CurrentUser(id=self.user.id, sub='sub-1', username='ewa',
                                    email='ewa@example.com', display_name='ewa',
                                    groups=frozenset({'dziekanat'}))

  def get(self, **params):
    response = self.client.get('/v1/kalendarz/', params={**ZAKRES, **params})
    self.assertEqual(response.status_code, 200, response.text)
    return response.json()

  def zrodlo(self, result, zrodlo_id):
    return next(z for z in result['zrodla'] if z['id'] == zrodlo_id)

  def _instance(self):
    from wf.models import ProcessInstance, ProcessModelVersion
    from wf.schemas.wf_schemas import ProcessInstanceStatus
    from sqlalchemy import select

    model_id = self.create_process_model()
    version = self.db.execute(select(ProcessModelVersion).where(
      ProcessModelVersion.process_model_id == model_id)).scalars().first()
    instance = ProcessInstance(process_model_version_id=version.id, title='Podanie o urlop',
                               status=ProcessInstanceStatus.WAITING)
    self.db.add(instance)
    self.db.flush()
    return instance

  def _task(self, instance, name, due, status='open', claimed_by=None, candidates=()):
    from wf.models import HumanTask, HumanTaskCandidate
    from wf.schemas.wf_schemas import HumanTaskStatus, PrincipalType

    task = HumanTask(id=uuid.uuid4(), process_instance_id=instance.id, bpmn_task_id=name,
                     name=name, status=HumanTaskStatus(status), due_at=due,
                     claimed_by_id=claimed_by)
    self.db.add(task)
    self.db.flush()
    for value in candidates:
      self.db.add(HumanTaskCandidate(human_task_id=task.id, principal_type=PrincipalType.GROUP,
                                     principal_value=value))
    self.db.commit()
    return task


class KalendarzApiTest(KalendarzTestBase):

  # --- źródła edu --------------------------------------------------------

  @patch(f'{EDU}.db_get_kalendarz_studenta', return_value=KALENDARZ)
  @patch(f'{EDU}.db_get_plan_studenta', return_value=PLAN)
  @patch('edu.dependencies.db_email2nr_albumu', return_value='12345')
  def test_student_calendar_merges_sources(self, _album, plan, kalendarz):
    result = self.get(rola='student')
    self.assertEqual(result['code'], 0)
    plan.assert_called_once_with('12345', '2027-01-25', '2027-01-31')
    kalendarz.assert_called_once_with('12345', '2027-01-25', '2027-01-31')

    by_id = {e['id']: e for e in result['events']}
    zajecia = by_id['zajecia:CS-1']
    self.assertEqual(zajecia['title'], 'Matematyka')
    self.assertEqual(zajecia['start'], '2027-01-26T08:00:00+01:00')
    self.assertEqual(zajecia['end'], '2027-01-26T09:30:00+01:00')
    self.assertFalse(zajecia['allDay'])
    self.assertEqual(zajecia['extendedProps']['miejsce'], 'A-1')
    self.assertEqual(zajecia['extendedProps']['ref'], '/plan')

    sesja = by_id['akademickie:Academic Term:2026-27 (zimowy):exam_session']
    self.assertTrue(sesja['allDay'])
    self.assertEqual(sesja['start'], '2027-01-25')
    self.assertEqual(sesja['end'], '2027-02-11')  # wyłączny koniec
    self.assertEqual(sesja['title'], 'Sesja egzaminacyjna 2026-27 (zimowy)')

    egzamin = by_id['akademickie:Assessment Plan:AP-1']
    self.assertEqual(egzamin['title'], 'Egzamin: Matematyka')
    self.assertEqual(egzamin['start'], '2027-01-28T09:00:00+01:00')
    self.assertEqual(egzamin['extendedProps']['miejsce'], 'B-2')

    oplata = by_id['akademickie:Fees:FEE-1']
    self.assertEqual(oplata['title'], 'Termin płatności: 1 200,00 PLN')
    self.assertEqual(oplata['extendedProps']['ref'], '/oplaty')

    # całodniowe przed czasowymi tego samego dnia, chronologicznie
    starts = [e['start'][:10] for e in result['events']]
    self.assertEqual(starts, sorted(starts))
    self.assertEqual(self.zrodlo(result, 'zajecia')['liczba'], 1)
    self.assertEqual(self.zrodlo(result, 'akademickie')['liczba'], 3)

  @patch(f'{EDU}.db_get_kalendarz_studenta', side_effect=ConnectorError('Frappe nie odpowiada'))
  @patch(f'{EDU}.db_get_plan_studenta', return_value=PLAN)
  @patch('edu.dependencies.db_email2nr_albumu', return_value='12345')
  def test_failing_source_gives_partial_result(self, *_):
    result = self.get(rola='student')
    self.assertEqual(result['code'], 0)
    self.assertEqual([e['id'] for e in result['events']], ['zajecia:CS-1'])
    akademickie = self.zrodlo(result, 'akademickie')
    self.assertFalse(akademickie['ok'])
    self.assertIn('Frappe nie odpowiada', akademickie['komunikat'])

  @patch(f'{EDU}.db_get_plan_studenta')
  @patch('edu.dependencies.db_email2nr_albumu', return_value='12345')
  def test_student_cannot_read_another_album(self, _album, plan):
    response = self.client.get('/v1/kalendarz/', params={**ZAKRES, 'nr_albumu': '99999'})
    self.assertEqual(response.status_code, 403)
    plan.assert_not_called()

  @patch(f'{EDU}.db_get_kalendarz_studenta', return_value=EduKalendarz())
  @patch(f'{EDU}.db_get_plan_studenta', return_value=EduPlan(od='', do=''))
  def test_staff_reads_student_by_album(self, plan, _kalendarz):
    self.as_staff()
    self.get(rola='student', nr_albumu='777')
    self.assertEqual(plan.call_args.args[0], '777')

  @patch(f'{EDU}.db_get_kalendarz_wykladowcy', return_value=EduKalendarz())
  @patch(f'{EDU}.db_get_plan_wykladowcy', return_value=PLAN)
  def test_lecturer_role_uses_token_email(self, plan, kalendarz):
    result = self.get(rola='wykladowca')
    plan.assert_called_once_with('jan@example.com', '2027-01-25', '2027-01-31')
    kalendarz.assert_called_once()
    self.assertEqual(len(result['events']), 1)

  @patch(f'{EDU}.db_get_plan_studenta')
  def test_other_role_has_no_academic_sources(self, plan):
    result = self.get(rola='dziekanat')
    plan.assert_not_called()
    self.assertFalse(self.zrodlo(result, 'zajecia')['dotyczy'])
    self.assertFalse(self.zrodlo(result, 'akademickie')['dotyczy'])
    self.assertTrue(self.zrodlo(result, 'zadania')['dotyczy'])

  @patch(f'{EDU}.db_get_kalendarz_studenta', return_value=EduKalendarz())
  @patch(f'{EDU}.db_get_plan_studenta', return_value=EduPlan(od='', do=''))
  @patch(f'{ROUTER}.db_get_tozsamosc', return_value=EduTozsamosc(
    email='jan@example.com', student=StudentTozsamosc(student='EDU-STU-1', nr_albumu='12345')))
  def test_role_detected_from_identity(self, _tozsamosc, plan, _kalendarz):
    self.get()
    self.assertEqual(plan.call_args.args[0], '12345')

  @patch(f'{ROUTER}.db_get_tozsamosc', side_effect=ConnectorError('timeout'))
  def test_identity_failure_is_a_warning(self, _tozsamosc):
    result = self.get()
    self.assertIn('Nie udało się ustalić roli', result['ostrzezenie'])
    self.assertFalse(self.zrodlo(result, 'zajecia')['dotyczy'])

  # --- parametry ---------------------------------------------------------

  def test_invalid_ranges(self):
    for params in ({'od': '2027-02-01', 'do': '2027-01-01'},
                   {'od': '2027-01-01', 'do': '2029-01-01'},
                   {'od': '1.01.2027'}):
      response = self.client.get('/v1/kalendarz/', params={**params, 'rola': 'kandydat'})
      self.assertEqual(response.status_code, 400, params)

  def test_unknown_source_is_rejected(self):
    response = self.client.get('/v1/kalendarz/', params={**ZAKRES, 'zrodla': 'zadania,party'})
    self.assertEqual(response.status_code, 400)

  def test_default_range(self):
    result = self.client.get('/v1/kalendarz/', params={'rola': 'kandydat'}).json()
    od = datetime.fromisoformat(result['od']).date()
    do = datetime.fromisoformat(result['do']).date()
    self.assertEqual((do - od).days, 30)

  def test_sources_listing_without_ics(self):
    with patch('kalendarz.zrodla.ics.get_settings', return_value=_settings()):
      ids = [z['id'] for z in self.client.get('/v1/kalendarz/zrodla').json()]
    self.assertEqual(ids, ['zajecia', 'akademickie', 'zadania'])

  # --- terminy zadań -----------------------------------------------------

  def test_task_deadlines(self):
    instance = self._instance()
    w_zakresie = datetime(2027, 1, 27, 14, 0, tzinfo=UTC)
    moje = self._task(instance, 'Opinia', w_zakresie, status='claimed', claimed_by=self.user.id)
    grupy = self._task(instance, 'Akceptacja', w_zakresie, candidates=('recenzenci',))
    self._task(instance, 'Obca grupa', w_zakresie, candidates=('ksiegowosc',))
    self._task(instance, 'Bez kandydatów', w_zakresie)
    self._task(instance, 'Wykonane', w_zakresie, status='completed', claimed_by=self.user.id)
    self._task(instance, 'Poza zakresem', datetime(2027, 3, 1, tzinfo=UTC), claimed_by=self.user.id,
               status='claimed')

    self.current_user = CurrentUser(id=self.user.id, sub='sub-1', username='jan',
                                    email='jan@example.com', display_name='jan',
                                    groups=frozenset({'recenzenci'}))
    result = self.get(rola='kandydat', zrodla='zadania')
    ids = {e['id'] for e in result['events']}
    self.assertEqual(ids, {f'zadania:{moje.id}', f'zadania:{grupy.id}'})
    zdarzenie = next(e for e in result['events'] if e['id'] == f'zadania:{moje.id}')
    self.assertEqual(zdarzenie['title'], 'Termin: Opinia')
    self.assertEqual(zdarzenie['start'], '2027-01-27T15:00:00+01:00')
    self.assertEqual(zdarzenie['extendedProps']['ref'], f'/tasks/{moje.id}')
    self.assertEqual(zdarzenie['extendedProps']['opis'], 'Podanie o urlop')
    self.assertTrue(zdarzenie['extendedProps']['szczegoly']['podjete_przeze_mnie'])

  # --- kalendarz uczelni (ICS) --------------------------------------------

  def test_university_ics(self):
    settings = _settings(uczelnia_ics=('https://example.com/u.ics',))
    ics.wyczysc_cache()
    with patch('kalendarz.zrodla.ics.get_settings', return_value=settings), \
         patch('kalendarz.zrodla.ics.pobierz', return_value=ICS) as pobierz:
      result = self.get(rola='kandydat', zrodla='uczelnia')
    pobierz.assert_called_once_with('https://example.com/u.ics', 900)
    tytuly = [(e['title'], e['start']) for e in result['events']]
    self.assertIn(('Dzień rektorski', '2027-01-27'), tytuly)
    # posiedzenie co tydzień od 4.01 - w zakresie 25-31.01 jedno (25.01)
    self.assertIn(('Posiedzenie senatu', '2027-01-25T10:00:00+01:00'), tytuly)
    self.assertNotIn('Odwołane', [t for t, _ in tytuly])
    rektorski = next(e for e in result['events'] if e['title'] == 'Dzień rektorski')
    self.assertTrue(rektorski['allDay'])
    self.assertEqual(rektorski['end'], '2027-01-28')
    senat = next(e for e in result['events'] if e['title'] == 'Posiedzenie senatu')
    self.assertEqual(senat['extendedProps']['miejsce'], 'Aula')

  def test_broken_ics_is_reported(self):
    settings = _settings(uczelnia_ics=('https://example.com/u.ics',))
    with patch('kalendarz.zrodla.ics.get_settings', return_value=settings), \
         patch('kalendarz.zrodla.ics.pobierz', side_effect=ics.IcsError('Nie udało się pobrać kalendarza (ConnectTimeout)')):
      result = self.get(rola='kandydat', zrodla='uczelnia')
    status = self.zrodlo(result, 'uczelnia')
    self.assertFalse(status['ok'])
    self.assertNotIn('example.com', status['komunikat'])

  # --- zgodność: GET /v1/wf/ (dawna zaślepka) ------------------------------

  def test_legacy_wf_endpoint_delegates(self):
    with patch('kalendarz.zrodla.ics.get_settings', return_value=_settings()), \
         patch(f'{ROUTER}.db_get_tozsamosc', return_value=EduTozsamosc(email='jan@example.com')):
      response = self.client.get('/v1/wf/', params={'start': '2027-01-25', 'end': '2027-01-31'})
    self.assertEqual(response.status_code, 200, response.text)
    result = response.json()
    self.assertEqual(result['od'], '2027-01-25')
    self.assertNotIn('Początek reoku akademickiego', response.text)
    self.assertIn('zadania', [z['id'] for z in result['zrodla']])


# subskrypcja kalendarza (kanał ICS z tokenem osobistym).
class KalendarzSubskrypcjaTest(KalendarzTestBase):

  def prepare(self) -> None:
    super().prepare()
    subskrypcje.zapomnij()
    self.addCleanup(subskrypcje.zapomnij)

  def subskrybuj(self, **body) -> str:
    response = self.client.post('/v1/kalendarz/subskrypcja', json=body)
    self.assertEqual(response.status_code, 200, response.text)
    data = response.json()
    self.assertTrue(data['url'].endswith('.ics'))
    self.assertTrue(data['webcal_url'].startswith('webcal://'))
    return urlsplit(data['url']).path

  def kanal(self, path: str, status: int = 200) -> str:
    response = self.client.get(path)
    self.assertEqual(response.status_code, status, response.text)
    return response.text

  def edu(self):
    return (patch('edu.dependencies.db_email2nr_albumu', return_value='12345'),
            patch(f'{EDU}.db_get_plan_studenta', return_value=PLAN),
            patch(f'{EDU}.db_get_kalendarz_studenta', return_value=KALENDARZ))

  def test_token_is_stored_only_as_hash(self):
    path = self.subskrybuj(rola='student')
    token = path.rsplit('/', 1)[1][:-4]
    from kalendarz.models import KalendarzSubskrypcja
    rekord = self.db.query(KalendarzSubskrypcja).one()
    self.assertNotEqual(rekord.token_hash, token)
    self.assertEqual(rekord.token_hash, subskrypcje.skrot(token))
    status = self.client.get('/v1/kalendarz/subskrypcja').json()
    self.assertTrue(status['aktywna'])
    self.assertEqual(status['szczegolowosc'], 'minimalna')
    self.assertNotIn('url', status)

  def test_minimal_feed(self):
    path = self.subskrybuj(rola='student')
    a, b, c = self.edu()
    with a, b, c:
      ics_text = self.kanal(path)
    self.assertIn('BEGIN:VCALENDAR', ics_text)
    self.assertIn('SUMMARY:Matematyka', ics_text)
    self.assertIn('LOCATION:A-1', ics_text)
    self.assertIn('DTSTART:20270126T070000Z', ics_text)  # 8:00 w Warszawie
    self.assertIn('DTSTART;VALUE=DATE:20270125', ics_text)
    self.assertIn('DTEND;VALUE=DATE:20270211', ics_text)
    self.assertIn('SUMMARY:Termin płatności', ics_text)
    self.assertNotIn('1 200', ics_text)
    self.assertNotIn('DESCRIPTION', ics_text)
    self.assertNotIn('Assessment Plan', ics_text)  # UID to skrót

  def test_full_feed_has_details_and_links(self):
    path = self.subskrybuj(rola='student', szczegolowosc='pelna')
    a, b, c = self.edu()
    with a, b, c, patch(f'{ROUTER}.get_settings', return_value=_settings(portal_url='https://portal.example.com')):
      ics_text = self.kanal(path).replace('\r\n ', '')
    self.assertIn('Termin płatności: 1 200\\,00 PLN', ics_text)
    self.assertIn('https://portal.example.com/oplaty', ics_text)
    self.assertIn('Grupa: G1', ics_text)

  def test_feed_is_cached(self):
    path = self.subskrybuj(rola='student')
    a, b, c = self.edu()
    with a, b as plan, c:
      self.kanal(path)
      self.kanal(path)
    plan.assert_called_once()

  def test_failing_source_gives_503(self):
    path = self.subskrybuj(rola='student')
    with patch('edu.dependencies.db_email2nr_albumu', return_value='12345'), \
         patch(f'{EDU}.db_get_plan_studenta', return_value=PLAN), \
         patch(f'{EDU}.db_get_kalendarz_studenta', side_effect=ConnectorError('timeout')):
      response = self.client.get(path)
    self.assertEqual(response.status_code, 503)
    self.assertEqual(response.headers.get('retry-after'), '900')

  def test_new_token_revokes_the_old_one(self):
    stary = self.subskrybuj(rola='kandydat')
    nowy = self.subskrybuj(rola='kandydat')
    self.assertNotEqual(stary, nowy)
    self.kanal(stary, 404)
    self.kanal(nowy, 200)

  def test_delete_revokes(self):
    path = self.subskrybuj(rola='kandydat')
    self.kanal(path, 200)
    self.assertFalse(self.client.delete('/v1/kalendarz/subskrypcja').json()['aktywna'])
    self.kanal(path, 404)  # także z pamięci podręcznej
    self.kanal('/v1/kalendarz/ics/nieznany.ics', 404)

  def test_feed_has_only_claimed_tasks(self):
    instance = self._instance()
    termin = datetime.now(UTC).replace(microsecond=0)
    moje = self._task(instance, 'Opinia', termin, status='claimed', claimed_by=self.user.id)
    self._task(instance, 'Akceptacja grupy', termin, candidates=('recenzenci',))
    self.current_user = CurrentUser(id=self.user.id, sub='sub-1', username='jan', email='jan@example.com',
                                    display_name='jan', groups=frozenset({'recenzenci'}))
    path = self.subskrybuj(rola='kandydat', szczegolowosc='pelna')
    ics_text = self.kanal(path)
    self.assertIn('SUMMARY:Termin: Opinia', ics_text)
    self.assertNotIn('Akceptacja grupy', ics_text)
    self.assertTrue(moje.id)

