# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

#usługa kodów jednorazowych (konto/) - normalizacja,
# przepływy założenia konta i odzyskania hasła, pula, limity, REST oraz
# konektory Keycloak Admin i SMS (bez sieci).

import importlib.util
import json
import re
import unittest
from datetime import datetime, timedelta

import requests
from requests.adapters import BaseAdapter
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from connector.base import ConnectorConfig
from connector.keycloak_admin.connector import (
  KeycloakAdminConnector,
  KeycloakConflict,
  KeycloakPasswordPolicy,
)
from connector.sms.connector import SmsConnector, SmsError
from konto.config import KontoConfigError, KontoSettings
from konto.models import KontoOperation, KontoTicket, KontoWkSession
from konto.policy import (
  PolicyError,
  normalize_email,
  normalize_pesel,
  normalize_phone,
  phone_country_allowed,
)
from konto.service import MSG_NEUTRAL_RESET, KontoError, KontoService

HAS_FASTAPI = importlib.util.find_spec('fastapi') is not None
SECRET = 'x' * 40
PHONE = '+48500100200'
EMAIL = 'anna@example.com'
VALID_PESEL = '44051401359'


class FakeKeycloak:

  def __init__(self):
    self.users = {}
    self.calls = []

  def add(self, **user):
    user.setdefault('id', f'u{len(self.users) + 1}')
    user.setdefault('enabled', True)
    user.setdefault('attributes', {})
    self.users[user['id']] = user
    return user

  def find_by_attribute(self, name, value):
    return [u for u in self.users.values() if value in u['attributes'].get(name, [])]

  def find_by_email(self, email):
    return [u for u in self.users.values() if u.get('email') == email]

  def find_by_login(self, login):
    for u in self.users.values():
      if u.get('username') == login:
        return u
    found = self.find_by_email(login)
    return found[0] if len(found) == 1 else None

  def get_user(self, user_id):
    return self.users[user_id]

  def create_user(self, payload):
    self.calls.append(('create_user', payload['username']))
    if len(payload['credentials'][0]['value']) < 8:
      raise KeycloakPasswordPolicy('create_user', 400, 'invalidPasswordMinLengthMessage')
    if self.find_by_email(payload['email']) or self.find_by_login(payload['username']):
      raise KeycloakConflict('create_user', 409, 'User exists with same username')
    user = self.add(**{k: v for k, v in payload.items() if k != 'credentials'})
    user['password'] = payload['credentials'][0]['value']
    return user['id']

  def reset_password(self, user_id, password):
    self.calls.append(('reset_password', user_id))
    if len(password) < 8:
      raise KeycloakPasswordPolicy('reset_password', 400, 'invalidPasswordMinLengthMessage')
    self.users[user_id]['password'] = password

  def update_attributes(self, user_id, attributes):
    self.calls.append(('update_attributes', user_id))
    self.users[user_id]['attributes'].update(attributes)

  def logout(self, user_id):
    self.calls.append(('logout', user_id))


class FakeSms:

  def __init__(self):
    self.sent = []

  def send(self, phone, text):
    self.sent.append((phone, text))


class FakeMail:

  def __init__(self):
    self.sent = []

  def send(self, to, subject, text):
    self.sent.append((to, subject, text))


class FakeCaptcha:

  def __init__(self, ok=True):
    self.ok = ok

  def verify(self, token, client_ip):
    return self.ok


def last_code(sender):
  return re.search(r'\d{6}', sender.sent[-1][-1]).group(0)


class PolicyTest(unittest.TestCase):

  def test_phone(self):
    self.assertEqual(normalize_phone('500 100 200'), PHONE)
    self.assertEqual(normalize_phone('+48 500-100-200'), PHONE)
    self.assertEqual(normalize_phone('0048500100200'), PHONE)
    self.assertEqual(normalize_phone('+380 67 123 4567'), '+380671234567')
    for bad in ('', '12345', '+0123456789', 'abc'):
      with self.assertRaises(PolicyError):
        normalize_phone(bad)

  def test_country(self):
    self.assertTrue(phone_country_allowed(PHONE, ('48',)))
    self.assertFalse(phone_country_allowed('+380671234567', ('48',)))

  def test_email_and_pesel(self):
    self.assertEqual(normalize_email(' Anna@Example.COM '), EMAIL)
    with self.assertRaises(PolicyError):
      normalize_email('anna@')
    self.assertEqual(normalize_pesel(VALID_PESEL), VALID_PESEL)
    with self.assertRaises(PolicyError):
      normalize_pesel('44051401358')

  def test_settings(self):
    s = KontoSettings.from_values({'channels': 'email', 'operations_quota': '2',
                                   'mount_in_hub': 'tak', 'sms_country_codes': '48, 420'})
    self.assertEqual((s.channels, s.operations_quota, s.mount_in_hub, s.sms_country_codes),
                     (('email',), 2, True, ('48', '420')))
    with self.assertRaises(KontoConfigError):
      KontoSettings.from_values({'channels': 'fax'})
    with self.assertRaises(KontoConfigError):
      KontoSettings().require_secret()


class ServiceTestCase(unittest.TestCase):

  def setUp(self):
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False},
                           poolclass=StaticPool)
    KontoTicket.__table__.create(engine)
    KontoOperation.__table__.create(engine)
    KontoWkSession.__table__.create(engine)
    self.db = sessionmaker(bind=engine)()
    self.addCleanup(engine.dispose)
    self.addCleanup(self.db.close)
    self.clock = datetime(2026, 11, 9, 10, 0, 0)
    self.keycloak = FakeKeycloak()
    self.sms = FakeSms()
    self.mail = FakeMail()
    self.captcha = FakeCaptcha()
    self.service = self.make_service()

  def make_service(self, **overrides):
    settings = KontoSettings.from_values({'secret': SECRET, **overrides})
    return KontoService(settings, self.db, self.keycloak, sms=self.sms, mail=self.mail,
                        captcha=self.captcha, now=lambda: self.clock)

  def tick(self, seconds):
    self.clock += timedelta(seconds=seconds)

  def register_sms(self, phone=PHONE, email=EMAIL, password='Haslo-1234', **extra):
    info = self.service.start_registration('sms', phone, '203.0.113.5', 'ok')
    return self.service.complete_registration(
      info.ticket, last_code(self.sms), email=email, phone=phone, first_name='Anna',
      last_name='Nowak', password=password, **extra)


class RegistrationTest(ServiceTestCase):

  def test_sms_registration_creates_account_without_session(self):
    result = self.register_sms()
    self.assertEqual(result['username'], EMAIL)
    user = self.keycloak.find_by_email(EMAIL)[0]
    self.assertEqual(user['attributes'], {'phone_number': [PHONE],
                                          'phone_number_verified': ['true']})
    self.assertFalse(user['emailVerified'])
    self.assertEqual(user['groups'], ['/kandydaci'])
    self.assertEqual(self.sms.sent[0][0], PHONE)
    # usługa tylko tworzy konto - żadnych innych operacji w Keycloak
    self.assertEqual([c[0] for c in self.keycloak.calls], ['create_user'])
    self.assertNotIn('access_token', result)

  def test_database_holds_no_plain_data(self):
    self.register_sms()
    dump = json.dumps([list(map(str, row)) for table in ('konto_ticket', 'konto_operation')
                       for row in self.db.execute(text(f'SELECT * FROM {table}'))])
    for plain in (PHONE, PHONE.lstrip('+'), EMAIL, '203.0.113.5', last_code(self.sms)):
      self.assertNotIn(plain, dump)

  def test_email_registration_marks_email_verified(self):
    info = self.service.start_registration('email', EMAIL, None, None)
    self.service.complete_registration(info.ticket, last_code(self.mail), email=EMAIL,
                                       first_name='Jan', last_name='Kowalski',
                                       password='Haslo-1234')
    user = self.keycloak.find_by_email(EMAIL)[0]
    self.assertTrue(user['emailVerified'])
    self.assertEqual(user['attributes'], {})

  def test_wrong_code_and_attempt_limit(self):
    info = self.service.start_registration('sms', PHONE, None, None)
    code = last_code(self.sms)
    for _ in range(5):
      with self.assertRaises(KontoError):
        self.service.complete_registration(info.ticket, '000000' if code != '000000' else '111111',
                                           email=EMAIL, phone=PHONE, first_name='A',
                                           last_name='B', password='Haslo-1234')
    with self.assertRaises(KontoError):
      self.service.complete_registration(info.ticket, code, email=EMAIL, phone=PHONE,
                                         first_name='A', last_name='B', password='Haslo-1234')
    self.assertEqual(self.keycloak.users, {})

  def test_expired_code(self):
    info = self.service.start_registration('sms', PHONE, None, None)
    self.tick(601)
    with self.assertRaises(KontoError):
      self.service.complete_registration(info.ticket, last_code(self.sms), email=EMAIL,
                                         phone=PHONE, first_name='A', last_name='B',
                                         password='Haslo-1234')

  def test_destination_must_match_ticket(self):
    info = self.service.start_registration('sms', PHONE, None, None)
    with self.assertRaises(KontoError):
      self.service.complete_registration(info.ticket, last_code(self.sms), email=EMAIL,
                                         phone='+48500100201', first_name='A',
                                         last_name='B', password='Haslo-1234')

  def test_password_policy_keeps_ticket_usable(self):
    info = self.service.start_registration('sms', PHONE, None, None)
    code = last_code(self.sms)
    kwargs = dict(email=EMAIL, phone=PHONE, first_name='A', last_name='B')
    with self.assertRaises(KontoError) as ctx:
      self.service.complete_registration(info.ticket, code, password='short', **kwargs)
    self.assertIn('invalidPasswordMinLengthMessage', ctx.exception.message)
    self.service.complete_registration(info.ticket, code, password='Haslo-1234', **kwargs)
    with self.assertRaises(KontoError):  # przepustka jednorazowa
      self.service.complete_registration(info.ticket, code, password='Haslo-1234', **kwargs)

  def test_phone_already_used(self):
    self.register_sms()
    with self.assertRaises(KontoError) as ctx:
      self.service.start_registration('sms', PHONE, None, None)
    self.assertEqual(ctx.exception.status, 409)
    self.service = self.make_service(accounts_per_phone='2')
    self.register_sms(email='brat@example.com')
    self.assertEqual(len(self.keycloak.find_by_attribute('phone_number', PHONE)), 2)

  def test_foreign_number_gets_no_sms(self):
    with self.assertRaises(KontoError):
      self.service.start_registration('sms', '+380671234567', None, None)
    self.assertEqual(self.sms.sent, [])

  def test_channel_disabled(self):
    self.service = self.make_service(channels='email')
    with self.assertRaises(KontoError):
      self.service.start_registration('sms', PHONE, None, None)

  def test_captcha(self):
    self.captcha.ok = False
    with self.assertRaises(KontoError):
      self.service.start_registration('sms', PHONE, None, None)
    self.assertEqual(self.sms.sent, [])

  def test_pesel_unique_as_hmac(self):
    self.service = self.make_service(pesel='required')
    with self.assertRaises(PolicyError):
      self.register_sms()
    self.register_sms(pesel=VALID_PESEL)
    stored = self.keycloak.find_by_email(EMAIL)[0]['attributes']['pesel_hmac'][0]
    self.assertNotIn(VALID_PESEL, stored)
    with self.assertRaises(KontoError) as ctx:
      self.register_sms(phone='+48500100300', email='inna@example.com', pesel=VALID_PESEL)
    self.assertEqual(ctx.exception.status, 409)

  def test_ip_rate_limit(self):
    self.service = self.make_service(tickets_per_ip_per_hour='2')
    self.service.start_registration('sms', PHONE, '203.0.113.5', None)
    self.service.start_registration('sms', '+48500100201', '203.0.113.5', None)
    with self.assertRaises(KontoError) as ctx:
      self.service.start_registration('sms', '+48500100202', '203.0.113.5', None)
    self.assertEqual(ctx.exception.status, 429)
    self.tick(3601)
    self.service.start_registration('sms', '+48500100202', '203.0.113.5', None)

  def test_daily_sms_budget(self):
    self.service = self.make_service(sms_per_day='1')
    self.service.start_registration('sms', PHONE, None, None)
    with self.assertRaises(KontoError) as ctx:
      self.service.start_registration('sms', '+48500100201', None, None)
    self.assertEqual(ctx.exception.status, 503)
    self.service.start_registration('email', EMAIL, None, None)

  def test_resend(self):
    info = self.service.start_registration('sms', PHONE, None, None)
    first = last_code(self.sms)
    with self.assertRaises(KontoError):
      self.service.resend(info.ticket, PHONE)  # za wcześnie
    self.tick(61)
    self.service.resend(info.ticket, PHONE)
    second = last_code(self.sms)
    if first != second:
      with self.assertRaises(KontoError):
        self.service.complete_registration(info.ticket, first, email=EMAIL, phone=PHONE,
                                           first_name='A', last_name='B', password='Haslo-1234')
    self.tick(61)
    self.service.resend(info.ticket, PHONE)
    self.tick(61)
    with self.assertRaises(KontoError):  # max_sends = 3
      self.service.resend(info.ticket, PHONE)
    self.service.complete_registration(info.ticket, last_code(self.sms), email=EMAIL,
                                       phone=PHONE, first_name='A', last_name='B',
                                       password='Haslo-1234')


class ResetTest(ServiceTestCase):

  def setUp(self):
    super().setUp()
    self.register_sms()
    self.user = self.keycloak.find_by_email(EMAIL)[0]
    self.keycloak.calls.clear()

  def reset(self, password='Nowe-haslo-1'):
    sent = len(self.sms.sent)
    info = self.service.start_reset(EMAIL, None, None)
    self.assertEqual(info.message, MSG_NEUTRAL_RESET)
    if len(self.sms.sent) == sent:
      return info, None
    self.service.complete_reset(info.ticket, last_code(self.sms), password)
    return info, last_code(self.sms)

  def test_reset_sets_password_and_logs_out(self):
    self.user['emailVerified'] = True
    self.reset()
    self.assertEqual(self.user['password'], 'Nowe-haslo-1')
    self.assertEqual(self.keycloak.calls, [('reset_password', self.user['id']),
                                           ('logout', self.user['id'])])
    self.assertEqual(self.mail.sent[-1][0], EMAIL)  # powiadomienie na drugi kanał

  def test_unknown_login_is_neutral(self):
    info = self.service.start_reset('nikt@example.com', None, None)
    self.assertEqual(info.message, MSG_NEUTRAL_RESET)
    self.assertEqual(len(self.sms.sent), 1)  # tylko kod rejestracji
    for code in ('000000', '123456'):
      with self.assertRaises(KontoError):
        self.service.complete_reset(info.ticket, code, 'Nowe-haslo-1')
    self.tick(61)
    self.assertEqual(self.service.resend(info.ticket).message, MSG_NEUTRAL_RESET)

  def test_unverified_email_is_not_a_reset_channel(self):
    self.service = self.make_service(channels='email')
    self.service.start_reset(EMAIL, None, None)
    self.assertEqual(self.mail.sent, [])

  def test_quota_registration_plus_two_resets(self):
    self.reset()
    self.reset()
    info, code = self.reset()  # czwarta operacja - pula 3 wyczerpana
    self.assertIsNone(code)
    self.assertEqual(len(self.sms.sent), 3)

  def test_reset_code_cannot_register(self):
    info = self.service.start_reset(EMAIL, None, None)
    with self.assertRaises(KontoError):
      self.service.complete_registration(info.ticket, last_code(self.sms), email='x@example.com',
                                         phone=PHONE, first_name='A', last_name='B',
                                         password='Haslo-1234')

  def test_resend_reset_code(self):
    info = self.service.start_reset(EMAIL, None, None)
    self.tick(61)
    self.service.resend(info.ticket)
    self.assertEqual(self.sms.sent[-1][0], PHONE)
    self.service.complete_reset(info.ticket, last_code(self.sms), 'Nowe-haslo-1')

  def test_disabled_account(self):
    self.user['enabled'] = False
    self.reset()
    self.assertEqual(len(self.sms.sent), 1)


@unittest.skipUnless(HAS_FASTAPI, 'FastAPI niedostępne')
class RouterTest(ServiceTestCase):

  def setUp(self):
    super().setUp()
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from konto.router import get_konto_service, router

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_konto_service] = lambda: self.service
    self.client = TestClient(app)

  def test_flow(self):
    self.assertEqual(self.client.get('/konfiguracja').json()['channels'], ['sms', 'email'])
    resp = self.client.post('/rejestracja/kod', json={'channel': 'sms', 'destination': '500100200'})
    self.assertEqual(resp.status_code, 200, resp.text)
    ticket = resp.json()['ticket']
    resp = self.client.post('/rejestracja', json={
      'ticket': ticket, 'code': last_code(self.sms), 'email': EMAIL, 'phone': '500 100 200',
      'first_name': 'Anna', 'last_name': 'Nowak', 'password': 'Haslo-1234'})
    self.assertEqual(resp.status_code, 201, resp.text)
    self.assertEqual(resp.json()['username'], EMAIL)

    resp = self.client.post('/reset/kod', json={'login': EMAIL})
    self.assertEqual(resp.status_code, 202)
    resp = self.client.post('/reset', json={'ticket': resp.json()['ticket'],
                                            'code': last_code(self.sms), 'password': 'Nowe-haslo-1'})
    self.assertEqual(resp.status_code, 204, resp.text)

  def test_errors(self):
    resp = self.client.post('/rejestracja/kod', json={'channel': 'sms', 'destination': '123'})
    self.assertEqual(resp.status_code, 400)
    resp = self.client.post('/reset', json={'ticket': 'x', 'code': '1', 'password': 'p'})
    self.assertEqual(resp.status_code, 400)


class CaptureAdapter(BaseAdapter):

  def __init__(self, responses):
    super().__init__()
    self.responses = list(responses)
    self.sent = []

  def send(self, request, **kwargs):
    self.sent.append(request)
    status, body, headers = self.responses.pop(0)
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps(body).encode() if body is not None else b''
    response.headers.update(headers)
    response.url = request.url
    response.request = request
    return response

  def close(self):
    pass


class KeycloakAdminConnectorTest(unittest.TestCase):

  def connector(self, *responses):
    c = KeycloakAdminConnector(ConnectorConfig('keycloak_admin', {
      'server_url': 'https://login.test', 'realm': 'kandydaci',
      'client_id': 'hub-konto', 'client_secret': 's'}))
    adapter = CaptureAdapter([(200, {'access_token': 'AT', 'expires_in': 300}, {}), *responses])
    c.connect().mount('https://', adapter)
    return c, adapter

  def test_create_user(self):
    c, adapter = self.connector(
      (201, None, {'Location': 'https://login.test/admin/realms/kandydaci/users/abc-123'}))
    self.assertEqual(c.create_user({'username': 'a'}), 'abc-123')
    token_request, create_request = adapter.sent
    self.assertIn('grant_type=client_credentials', token_request.body)
    self.assertEqual(create_request.headers['Authorization'], 'Bearer AT')
    self.assertTrue(create_request.url.endswith('/admin/realms/kandydaci/users'))

  def test_errors_without_request_body(self):
    c, _ = self.connector((409, {'errorMessage': 'User exists with same email'}, {}),
                          (400, {'error': 'invalidPasswordMinLengthMessage'}, {}))
    with self.assertRaises(KeycloakConflict):
      c.create_user({'username': 'a', 'credentials': [{'value': 'TAJNE-HASLO'}]})
    with self.assertRaises(KeycloakPasswordPolicy) as ctx:
      c.reset_password('abc', 'TAJNE-HASLO')
    self.assertNotIn('TAJNE-HASLO', str(ctx.exception))

  def test_find_by_attribute_filters_exactly(self):
    c, adapter = self.connector((200, [
      {'id': '1', 'attributes': {'phone_number': [PHONE]}},
      {'id': '2', 'attributes': {'phone_number': ['+48500100201']}}], {}))
    self.assertEqual([u['id'] for u in c.find_by_attribute('phone_number', PHONE)], ['1'])
    self.assertIn('q=phone_number', adapter.sent[-1].url)


class KeycloakUpdateAttributesTest(KeycloakAdminConnectorTest):

  def test_update_keeps_other_attributes(self):
    c, adapter = self.connector(
      (200, {'id': 'u1', 'username': 'a', 'email': 'a@example.com', 'firstName': 'A',
             'attributes': {'phone_number': [PHONE]}, 'access': {}}, {}),
      (204, None, {}))
    c.update_attributes('u1', {'identity_verified': ['wk']})
    put = adapter.sent[-1]
    self.assertEqual(put.method, 'PUT')
    body = json.loads(put.body)
    self.assertEqual(body['attributes'], {'phone_number': [PHONE], 'identity_verified': ['wk']})
    self.assertEqual(body['firstName'], 'A')
    self.assertNotIn('access', body)


class IntegracjaSmsTest(unittest.TestCase):

  def connector(self, *responses):
    c = SmsConnector(ConnectorConfig('sms', {'backend': 'integracjasms', 'token': 'T',
                                             'reference': 'galwork'}))
    adapter = CaptureAdapter(responses)
    c.connect().mount('https://', adapter)
    return c, adapter

  def test_send(self):
    c, adapter = self.connector((201, {'ok': True, 'messages': [{'id': 'm1', 'status': 'queued'}],
                                       'credits_left': 99}, {}))
    c.send(PHONE, 'Kod: 123456')
    request = adapter.sent[0]
    self.assertEqual(request.url, 'https://integracjasms.pl/api/v1/sms')
    self.assertEqual(request.headers['Authorization'], 'Bearer T')
    self.assertEqual(json.loads(request.body), {'to': PHONE, 'message': 'Kod: 123456',
                                                'reference': 'galwork'})

  def test_error(self):
    c, _ = self.connector((422, {'ok': False, 'error': {'code': 'invalid_recipient',
                                                        'message': 'Numer 500100200 niepoprawny'}}, {}))
    with self.assertRaises(SmsError) as ctx:
      c.send(PHONE, 'Kod: 123456')
    self.assertIn('invalid_recipient', str(ctx.exception))
    self.assertNotIn('500100200', str(ctx.exception))


class SmsConnectorTest(unittest.TestCase):

  def connector(self, *responses):
    c = SmsConnector(ConnectorConfig('sms', {'backend': 'smsapi', 'token': 'T', 'sender': 'Uczelnia'}))
    adapter = CaptureAdapter(responses)
    c.connect().mount('https://', adapter)
    return c, adapter

  def test_send(self):
    c, adapter = self.connector((200, {'count': 1, 'list': []}, {}))
    c.send(PHONE, 'Kod: 123456')
    request = adapter.sent[0]
    self.assertEqual(request.headers['Authorization'], 'Bearer T')
    self.assertIn('to=48500100200', request.body)
    self.assertIn('from=Uczelnia', request.body)

  def test_error_hides_number_and_text(self):
    c, _ = self.connector((200, {'error': 13, 'message': 'No correct phone numbers'}, {}))
    with self.assertRaises(SmsError) as ctx:
      c.send(PHONE, 'Kod: 123456')
    self.assertNotIn('500100200', str(ctx.exception))
    self.assertNotIn('123456', str(ctx.exception))


if __name__ == '__main__':
  unittest.main()
