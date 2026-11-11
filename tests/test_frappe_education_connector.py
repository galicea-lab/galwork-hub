# tests/test_frappe_education_connector.py
# testy konektora Frappe Education (zob. ../education). Nie wymagają żywej
# instancji Frappe - podmieniamy moduł `requests` na atrapę, konfigurację
# przez `ConnectorConfig` w pamięci (transportem jest REST).
from __future__ import annotations

import json
import sys
import types
import unittest

from connector.base import ConnectorConfig, ConnectorConfigError, ConnectorError
from connector.frappe_education import connector as fe_connector


class FakeResponse:
  def __init__(self, status_code=200, json_data=None, content=b''):
    self.status_code = status_code
    self.ok = 200 <= status_code < 300
    self._json = json_data if json_data is not None else {}
    self.content = content
    self.text = json.dumps(self._json)

  def json(self):
    return self._json


class FakeSession:
  """Atrapa `requests.Session` - zapisuje wywołania, oddaje zaprogramowane
  odpowiedzi po `base_url + path` (metoda zawsze GET/POST/PUT/DELETE)."""

  def __init__(self):
    self.headers = {}
    self.calls: list[dict] = []
    self.closed = False
    self.responses: dict[str, FakeResponse] = {}

  def program(self, path: str, response: FakeResponse):
    self.responses[path] = response

  def _respond(self, method, url, **kwargs):
    self.calls.append({'method': method, 'url': url, **kwargs})
    for path, response in self.responses.items():
      if url.endswith(path):
        return response
    return FakeResponse(status_code=404, json_data={'exc': 'not programmed: %s' % url})

  def get(self, url, **kwargs):
    return self._respond('GET', url, **kwargs)

  def post(self, url, **kwargs):
    return self._respond('POST', url, **kwargs)

  def put(self, url, **kwargs):
    return self._respond('PUT', url, **kwargs)

  def delete(self, url, **kwargs):
    return self._respond('DELETE', url, **kwargs)

  def close(self):
    self.closed = True


# przywraca moduł sprzed testu. Samo usunięcie `requests` z
# sys.modules psuło kolejne testy w tym samym procesie: następny import tworzył
# nowy obiekt modułu, a podmoduły `requests.*` zostawały z poprzedniego
# ("module 'requests' has no attribute 'auth'").
def _restore_module(name, module):
  if module is None:
    sys.modules.pop(name, None)
  else:
    sys.modules[name] = module


def _install_fake_requests():
  fake_session = FakeSession()
  module = types.ModuleType('requests')
  module.Session = lambda: fake_session
  sys.modules['requests'] = module
  return fake_session


class FrappeEducationConnectorTest(unittest.TestCase):

  def setUp(self):
    fe_connector.reset_connector()
    self.addCleanup(fe_connector.reset_connector)
    # po teście wraca prawdziwy `requests` (zob. _restore_module).
    self.addCleanup(_restore_module, 'requests', sys.modules.get('requests'))
    sys.modules.pop('requests', None)

  def _config(self, **overrides):
    values = {
      'base_url': 'https://edu.test.local', 'api_key': 'k', 'api_secret': 's',
      'timeout': '5',
    }
    values.update(overrides)
    return ConnectorConfig(section='frappe_education', values=values)

  # ------------------------------------------------------------------
  def test_missing_config_raises_config_error(self):
    connector = fe_connector.FrappeEducationConnector(
      ConnectorConfig(section='frappe_education', values={}))
    with self.assertRaises(ConnectorConfigError):
      connector.connect()

  def test_connect_sets_auth_header_and_base_url(self):
    _install_fake_requests()
    connector = fe_connector.FrappeEducationConnector(self._config())
    session = connector.connect()
    self.assertEqual(session.headers['Authorization'], 'token k:s')
    self.assertEqual(connector.base_url, 'https://edu.test.local')

  def test_connect_is_idempotent(self):
    _install_fake_requests()
    connector = fe_connector.FrappeEducationConnector(self._config())
    first, second = connector.connect(), connector.connect()
    self.assertIs(first, second)

  def test_non_numeric_timeout_is_config_error(self):
    connector = fe_connector.FrappeEducationConnector(self._config(timeout='nie-liczba'))
    _install_fake_requests()
    with self.assertRaises(ConnectorConfigError):
      connector.connect()

  def test_get_list_encodes_filters_as_json_not_python_repr(self):
    """Regresja na błąd z connector/erpnext/erpnext.py: `str(filters)` daje
    Pythonowy repr z apostrofami, niepoprawny jako JSON po stronie serwera."""
    session = _install_fake_requests()
    connector = fe_connector.FrappeEducationConnector(self._config())
    session.program(
      '/api/resource/Student',
      FakeResponse(json_data={'data': [{'name': 'EDU-STU-2024-00001'}]}),
    )
    result = connector.get_list(
      "Student", filters=[["album_number", "=", "123"]], fields=["name"])
    self.assertEqual(result, [{'name': 'EDU-STU-2024-00001'}])
    call = session.calls[-1]
    # musi być poprawnym JSON-em, nie Pythonowym str([...])
    decoded = json.loads(call['params']['filters'])
    self.assertEqual(decoded, [["album_number", "=", "123"]])

  def test_get_list_raises_connector_error_on_bad_status(self):
    session = _install_fake_requests()
    connector = fe_connector.FrappeEducationConnector(self._config())
    session.program('/api/resource/Student', FakeResponse(status_code=500))
    with self.assertRaises(ConnectorError):
      connector.get_list("Student")

  def test_get_all_pages_through_results(self):
    session = _install_fake_requests()
    connector = fe_connector.FrappeEducationConnector(self._config())
    pages = [
      FakeResponse(json_data={'data': [{'name': '1'}, {'name': '2'}]}),
      FakeResponse(json_data={'data': [{'name': '3'}]}),
    ]

    call_count = {'n': 0}

    def fake_get(url, **kwargs):
      session.calls.append({'url': url, **kwargs})
      page = pages[min(call_count['n'], len(pages) - 1)]
      call_count['n'] += 1
      return page

    session.get = fake_get
    results = list(connector.get_all("Student", page_size=2))
    self.assertEqual([r['name'] for r in results], ['1', '2', '3'])

  def test_call_method_returns_message(self):
    session = _install_fake_requests()
    connector = fe_connector.FrappeEducationConnector(self._config())
    session.program(
      '/api/method/frappe.client.get_count',
      FakeResponse(json_data={'message': 7}),
    )
    self.assertEqual(connector.get_count("Student"), 7)

  def test_test_connection_false_on_error(self):
    session = _install_fake_requests()
    connector = fe_connector.FrappeEducationConnector(self._config())
    session.program('/api/method/frappe.client.get_count', FakeResponse(status_code=403))
    self.assertFalse(connector.test_connection())

  def test_get_file_content_downloads_from_base_url(self):
    session = _install_fake_requests()
    connector = fe_connector.FrappeEducationConnector(self._config())
    session.program('/private/files/decyzja.docx', FakeResponse(content=b'binarna-zawartosc'))
    content = connector.get_file_content('/private/files/decyzja.docx')
    self.assertEqual(content, b'binarna-zawartosc')

  def test_upload_file_sends_multipart_with_doctype_and_fieldname(self):
    session = _install_fake_requests()
    connector = fe_connector.FrappeEducationConnector(self._config())
    session.program(
      '/api/method/upload_file',
      FakeResponse(json_data={'message': {'file_url': '/private/files/podpisano.pdf'}}),
    )
    result = connector.upload_file(
      "Student Correspondence", "EDU-COR-2026-00001", "podpisano.pdf",
      b'%PDF-raw-bytes', fieldname="signed_document")
    self.assertEqual(result, {'file_url': '/private/files/podpisano.pdf'})
    call = session.calls[-1]
    self.assertEqual(call['method'], 'POST')
    self.assertEqual(call['data']['doctype'], 'Student Correspondence')
    self.assertEqual(call['data']['docname'], 'EDU-COR-2026-00001')
    self.assertEqual(call['data']['fieldname'], 'signed_document')
    self.assertEqual(call['data']['is_private'], '1')
    self.assertEqual(call['files']['file'], ('podpisano.pdf', b'%PDF-raw-bytes', 'application/octet-stream'))

  def test_get_connector_is_singleton(self):
    self.assertIs(fe_connector.get_connector(), fe_connector.get_connector())


if __name__ == '__main__':
  unittest.main()
