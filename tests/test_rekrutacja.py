# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

# Portal kandydata (/v1/rekrutacja) - weryfikacja tokenu
# realmu kandydatów i wywołania Frappe w imieniu kandydata.

import importlib.util
import time
import unittest
from unittest.mock import patch

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from connector.frappe_education import user_context
from connector.frappe_education.connector import FrappeRequestError
from rekrutacja import auth as rauth

HAS_FASTAPI = importlib.util.find_spec('fastapi') is not None
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
ISSUER = 'https://login.test/realms/rekrutacja'
SETTINGS = rauth.RekrutacjaSettings(issuer=ISSUER, jwks_url=f'{ISSUER}/certs', allowed_clients=('portal',),
                                    audience=(), candidate_group='kandydaci', leeway=0)


def token(key=KEY, **overrides):
  now = int(time.time())
  claims = {'iss': ISSUER, 'sub': 'sub-1', 'azp': 'portal', 'typ': 'Bearer', 'iat': now,
            'exp': now + 300, 'email': 'Anna@Example.com', 'groups': ['/kandydaci']}
  claims.update(overrides)
  claims = {k: v for k, v in claims.items() if v is not None}
  return jwt.encode(claims, key, algorithm='RS256')


class VerifyTest(unittest.TestCase):

  def verify(self, value):
    return rauth.verify_candidate_token(value, SETTINGS, key=KEY.public_key())

  def test_valid(self):
    candidate = self.verify(token())
    self.assertEqual((candidate.sub, candidate.email), ('sub-1', 'anna@example.com'))
    self.assertNotIn('ey', repr(candidate))

  def test_rejected(self):
    cases = {
      'inny klucz': (token(key=OTHER_KEY), 401),
      'wygasły': (token(exp=int(time.time()) - 10), 401),
      'inny realm': (token(iss='https://login.test/realms/transmem'), 401),
      'inny klient': (token(azp='transmem'), 401),
      'token ID': (token(typ='ID'), 401),
      'bez grupy': (token(groups=['/pracownicy']), 403),
      'bez sub': (token(sub=None), 401),
    }
    for name, (value, code) in cases.items():
      with self.subTest(name), self.assertRaises(Exception) as ctx:
        self.verify(value)
      self.assertEqual(getattr(ctx.exception, 'status_code', None), code, name)

  def test_not_configured(self):
    with self.assertRaises(Exception) as ctx:
      rauth.verify_candidate_token(token(), rauth.RekrutacjaSettings('', '', (), (), 'kandydaci', 0))
    self.assertEqual(ctx.exception.status_code, 503)


class FakeFrappe:

  def __init__(self):
    self.calls = []
    self.error = None

  def call_method(self, method, params=None, http_method='GET'):
    context = user_context.current()
    self.calls.append((method, params, http_method, context.token if context else None,
                       context.client_ip if context else None))
    if self.error:
      raise self.error
    if method.endswith(('list_programmes', 'list_applications', 'list_decisions', 'get_schedule')):
      return [{'name': 'INF-S1'}]
    return {'name': 'KND-1', 'first_name': 'Anna'}


@unittest.skipUnless(HAS_FASTAPI, 'FastAPI niedostępne')
class RouterTest(unittest.TestCase):

  def setUp(self):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from rekrutacja import router as rrouter

    self.frappe = FakeFrappe()
    for patcher in (patch.object(rauth, 'rekrutacja_settings', return_value=SETTINGS),
                    patch.object(rrouter, 'get_connector', return_value=self.frappe)):
      patcher.start()
      self.addCleanup(patcher.stop)
    jwk = patch.object(rauth, '_jwk_client').start()
    self.addCleanup(patch.stopall)
    jwk.return_value.get_signing_key_from_jwt.return_value.key = KEY.public_key()
    app = FastAPI()
    app.include_router(rrouter.router)
    self.client = TestClient(app)

  def auth(self, value=None):
    return {'Authorization': f'Bearer {value or token()}'}

  def test_profile_on_behalf_of_candidate(self):
    value = token()
    resp = self.client.get('/profil', headers=self.auth(value))
    self.assertEqual(resp.status_code, 200, resp.text)
    method, params, http_method, forwarded, ip = self.frappe.calls[-1]
    self.assertEqual((method, http_method, forwarded), ('admissions_poland.portal.api.get_profile', 'GET', value))
    self.assertIsNone(user_context.current())

  def test_save_profile(self):
    data = {'first_name': 'Anna', 'pesel': '44051401359'}
    resp = self.client.put('/profil', headers=self.auth(), json=data)
    self.assertEqual(resp.status_code, 200, resp.text)
    self.assertEqual(self.frappe.calls[-1][:3], ('admissions_poland.portal.api.update_profile',
                                                 {'data': data}, 'POST'))

  def test_validation_message_from_frappe(self):
    self.frappe.error = FrappeRequestError('call_method', 417, 'ValidationError', 'Niepoprawny PESEL')
    resp = self.client.put('/profil', headers=self.auth(), json={'pesel': '1'})
    self.assertEqual((resp.status_code, resp.json()['detail']), (400, 'Niepoprawny PESEL'))

  def test_requires_candidate_token(self):
    self.assertEqual(self.client.get('/profil').status_code, 401)
    self.assertEqual(self.client.get('/profil', headers=self.auth(token(groups=[]))).status_code, 403)
    self.assertEqual(self.frappe.calls, [])

  def test_public_programmes_without_user(self):
    self.assertEqual(self.client.get('/kierunki').status_code, 200)
    self.assertIsNone(self.frappe.calls[-1][3])

  def test_public_schedule_without_user(self):
    self.assertEqual(self.client.get('/harmonogram').status_code, 200)
    self.assertEqual(self.frappe.calls[-1][:4], ('admissions_poland.portal.api.get_schedule', None, 'GET', None))

  def test_apply_for_programme(self):
    value = token()
    resp = self.client.post('/zgloszenia', headers=self.auth(value), json={'kierunek': '2026-INF-S1'})
    self.assertEqual(resp.status_code, 200, resp.text)
    self.assertEqual(self.frappe.calls[-1][:4], ('admissions_poland.portal.api.apply_for_programme',
                                                 {'admission_programme': '2026-INF-S1'}, 'POST', value))
    self.client.post('/zgloszenia', headers=self.auth(), json={'kierunek': '2026-INF-S1', 'priorytet': 2})
    self.assertEqual(self.frappe.calls[-1][1], {'admission_programme': '2026-INF-S1', 'priority': 2})

  def test_apply_closed_programme_message(self):
    self.frappe.error = FrappeRequestError('call_method', 417, 'ValidationError',
                                           'Rejestracja na ten kierunek jest zamknięta')
    resp = self.client.post('/zgloszenia', headers=self.auth(), json={'kierunek': 'X'})
    self.assertEqual((resp.status_code, resp.json()['detail']), (400, 'Rejestracja na ten kierunek jest zamknięta'))

  def test_applications_withdrawal_and_decisions(self):
    self.assertEqual(self.client.get('/zgloszenia', headers=self.auth()).status_code, 200)
    self.assertEqual(self.frappe.calls[-1][0], 'admissions_poland.portal.api.list_applications')
    resp = self.client.post('/zgloszenia/ZGL-2026-000001/wycofanie', headers=self.auth())
    self.assertEqual(resp.status_code, 200, resp.text)
    self.assertEqual(self.frappe.calls[-1][:3], ('admissions_poland.portal.api.withdraw_application',
                                                 {'application': 'ZGL-2026-000001'}, 'POST'))
    self.assertEqual(self.client.get('/decyzje', headers=self.auth()).status_code, 200)
    self.assertEqual(self.frappe.calls[-1][0], 'admissions_poland.portal.api.list_decisions')

  def test_applications_require_candidate_token(self):
    for method, path in (('get', '/zgloszenia'), ('post', '/zgloszenia/Z/wycofanie'), ('get', '/decyzje')):
      self.assertEqual(getattr(self.client, method)(path).status_code, 401, path)
    self.assertEqual(self.client.post('/zgloszenia', json={'kierunek': 'X'}).status_code, 401)
    self.assertEqual(self.frappe.calls, [])


if __name__ == '__main__':
  unittest.main()
