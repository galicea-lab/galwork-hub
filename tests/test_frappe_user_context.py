# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

# Wywołania Frappe w imieniu użytkownika portalu
# (X-User-Token, X-Forwarded-For) - kontekst, nagłówki, sesja requests,
# zależność FastAPI.

import contextvars
import importlib.util
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import requests
from requests.adapters import BaseAdapter

from connector.base import ConnectorConfig
from connector.frappe_education import connector as fe_connector
from connector.frappe_education import user_context
from connector.frappe_education.user_context import (
  UserContext,
  acting_as,
  bearer_token,
  current,
  forward_user_headers,
)

HAS_FASTAPI = importlib.util.find_spec('fastapi') is not None


class CaptureAdapter(BaseAdapter):
  """Transport `requests` bez sieci - zapamiętuje wysłane żądania."""

  def __init__(self):
    super().__init__()
    self.sent = []

  def send(self, request, **kwargs):
    self.sent.append(request)
    response = requests.Response()
    response.status_code = 200
    response._content = json.dumps({'message': 'ok'}).encode()
    response.url = request.url
    response.request = request
    return response

  def close(self):
    pass


class UserContextTest(unittest.TestCase):

  def tearDown(self):
    user_context.set_current(None)

  def test_bearer_token(self):
    self.assertEqual(bearer_token('Bearer abc.def'), 'abc.def')
    self.assertEqual(bearer_token('bearer  abc '), 'abc')
    self.assertIsNone(bearer_token('token key:secret'))
    self.assertIsNone(bearer_token('Basic a2V5'))
    self.assertIsNone(bearer_token('Bearer '))
    self.assertIsNone(bearer_token(None))

  def test_acting_as_restores_previous_context(self):
    self.assertIsNone(current())
    with acting_as('t1', '203.0.113.5'):
      self.assertEqual(current(), UserContext('t1', '203.0.113.5'))
      with acting_as('t2'):
        self.assertEqual(current().token, 't2')
      self.assertEqual(current().token, 't1')
      with acting_as(None):
        self.assertEqual(current().token, 't1')
    self.assertIsNone(current())

  def test_repr_does_not_show_the_token(self):
    self.assertNotIn('secret-token', repr(UserContext('secret-token', '203.0.113.5')))

  def test_headers_only_with_context(self):
    request = requests.Request('GET', 'https://edu.test/api').prepare()
    forward_user_headers(request)
    self.assertNotIn('X-User-Token', request.headers)
    with acting_as('tok', '203.0.113.5'):
      forward_user_headers(request)
    self.assertEqual(request.headers['X-User-Token'], 'tok')
    self.assertEqual(request.headers['X-Forwarded-For'], '203.0.113.5')

  def test_context_reaches_worker_threads_like_starlette(self):
    # Starlette uruchamia synchroniczne endpointy w puli wątków z kopią kontekstu
    with acting_as('tok'):
      ctx = contextvars.copy_context()
    with ThreadPoolExecutor(1) as pool:
      self.assertEqual(pool.submit(ctx.run, lambda: current().token).result(), 'tok')
      self.assertIsNone(pool.submit(current).result())


class ConnectorForwardingTest(unittest.TestCase):

  def setUp(self):
    fe_connector.reset_connector()
    self.addCleanup(fe_connector.reset_connector)
    self.connector = fe_connector.FrappeEducationConnector(ConnectorConfig('frappe_education', {
      'base_url': 'https://edu.test.local', 'api_key': 'k', 'api_secret': 's'}))
    self.adapter = CaptureAdapter()
    self.connector.connect().mount('https://', self.adapter)

  def test_service_call_without_user(self):
    self.connector.call_method('ping')
    headers = self.adapter.sent[-1].headers
    self.assertEqual(headers['Authorization'], 'token k:s')
    self.assertNotIn('X-User-Token', headers)
    self.assertNotIn('X-Forwarded-For', headers)

  def test_call_on_behalf_of_user(self):
    with acting_as('user-token', '203.0.113.5'):
      self.connector.call_method('admissions_poland.portal.api.get_candidate')
    headers = self.adapter.sent[-1].headers
    self.assertEqual(headers['Authorization'], 'token k:s')  # nadal klucz huba
    self.assertEqual(headers['X-User-Token'], 'user-token')
    self.assertEqual(headers['X-Forwarded-For'], '203.0.113.5')

  def test_context_does_not_stick_to_the_shared_session(self):
    with acting_as('user-token'):
      self.connector.call_method('a')
    self.connector.call_method('b')
    self.assertNotIn('X-User-Token', self.adapter.sent[-1].headers)
    self.assertNotIn('X-User-Token', self.connector.connect().headers)


@unittest.skipUnless(HAS_FASTAPI, 'FastAPI niedostępne')
class FastApiDependencyTest(unittest.TestCase):

  def client(self, dependency):
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI(dependencies=[Depends(dependency)])

    @app.get('/sync')
    def sync_endpoint():
      ctx = current()
      return {'token': ctx.token if ctx else None, 'ip': ctx.client_ip if ctx else None}

    @app.get('/async')
    async def async_endpoint():
      ctx = current()
      return {'token': ctx.token if ctx else None}

    return TestClient(app)

  def test_forwarding_enabled(self):
    from edu import dependencies
    with patch.object(dependencies, 'forward_user_token_enabled', return_value=True):
      client = self.client(dependencies.frappe_as_user)
      auth = {'Authorization': 'Bearer user-token'}
      self.assertEqual(client.get('/sync', headers=auth).json()['token'], 'user-token')
      self.assertEqual(client.get('/async', headers=auth).json()['token'], 'user-token')
      self.assertIsNone(client.get('/sync').json()['token'])
    self.assertIsNone(current())

  def test_forwarding_disabled_by_default(self):
    from edu import dependencies
    with patch.object(dependencies, 'forward_user_token_enabled', return_value=False):
      client = self.client(dependencies.frappe_as_user)
      self.assertIsNone(client.get('/sync', headers={'Authorization': 'Bearer t'}).json()['token'])

  def test_always_variant_ignores_the_switch(self):
    from edu import dependencies
    with patch.object(dependencies, 'forward_user_token_enabled', return_value=False):
      client = self.client(dependencies.frappe_as_user_always)
      self.assertEqual(client.get('/sync', headers={'Authorization': 'Bearer t'}).json()['token'], 't')


if __name__ == '__main__':
  unittest.main()
