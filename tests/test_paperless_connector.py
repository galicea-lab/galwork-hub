# tests/test_paperless_connector.py
# testy konektora Paperless-ngx (connector/paperless). 
# Nie wymagają żywego serwera Paperless - podmieniamy
# moduł `requests` na atrapę, konfigurację przez `ConnectorConfig` w
# pamięci (jak tests/test_frappe_education_connector.py), tylko tu klient
# woła `session.request(method, url, ...)` (jedna metoda), nie
# `session.get/post/...` osobno.
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import types
import unittest

from connector.base import ConnectorConfig, ConnectorConfigError, ConnectorError
from connector.paperless import connector as paperless_connector


class FakeResponse:
  def __init__(self, status_code=200, json_data=None, text=None, content=b''):
    self.status_code = status_code
    self.ok = 200 <= status_code < 300
    self._json = json_data
    if text is not None:
      self.text = text
    elif json_data is not None:
      self.text = json.dumps(json_data)
    else:
      self.text = ''
    self.content = content

  def json(self):
    return self._json


class FakeSession:
  """Atrapa `requests.Session` - zapisuje wywołania, oddaje zaprogramowane
  odpowiedzi po dopasowaniu końcówki URL (jak FakeSession w
  tests/test_frappe_education_connector.py, tylko tu jedna metoda
  `request()`, zgodnie z connector/paperless/connector.py::_request)."""

  def __init__(self):
    self.headers = {}
    self.verify = True
    self.calls: list[dict] = []
    self.responses: dict[tuple[str | None, str], FakeResponse] = {}
    self.closed = False

  def program(self, path: str, response: FakeResponse, method: str | None = None):
    """`method=None` odpowiada na każdą metodę pod tą ścieżką, chyba że
    dla tej (metoda, ścieżka) zaprogramowano coś bardziej szczegółowego -
    patrz `request()`."""
    self.responses[(method and method.lower(), path)] = response

  def request(self, method, url, **kwargs):
    self.calls.append({'method': method, 'url': url, **kwargs})
    method = method.lower()
    for (want_method, path), response in self.responses.items():
      if url.endswith(path) and want_method == method:
        return response
    for (want_method, path), response in self.responses.items():
      if url.endswith(path) and want_method is None:
        return response
    return FakeResponse(status_code=404, json_data={'detail': 'not programmed: %s' % url})

  def close(self):
    self.closed = True


def _install_fake_requests():
  fake_session = FakeSession()
  module = types.ModuleType('requests')
  module.Session = lambda: fake_session
  sys.modules['requests'] = module
  return fake_session


class PaperlessConnectorTest(unittest.TestCase):

  def setUp(self):
    paperless_connector.reset_connector()
    self.addCleanup(paperless_connector.reset_connector)
    sys.modules.pop('requests', None)
    self.addCleanup(sys.modules.pop, 'requests', None)
    self._staging_dir = tempfile.mkdtemp(prefix='paperless-staging-test-')
    self.addCleanup(shutil.rmtree, self._staging_dir, True)

  def _config(self, **overrides):
    values = {
      'base_url': 'https://paperless.test.local', 'token': 'tok', 'timeout': '5',
      'staging_dir': self._staging_dir,
    }
    values.update(overrides)
    return ConnectorConfig(section='paperless', values=values)

  # ------------------------------------------------------------------
  def test_missing_config_raises_config_error(self):
    connector = paperless_connector.PaperlessConnector(
      ConnectorConfig(section='paperless', values={}))
    with self.assertRaises(ConnectorConfigError):
      connector.connect()

  def test_connect_sets_auth_header_and_base_url(self):
    _install_fake_requests()
    connector = paperless_connector.PaperlessConnector(self._config())
    session = connector.connect()
    self.assertEqual(session.headers['Authorization'], 'Token tok')
    self.assertEqual(connector.base_url, 'https://paperless.test.local')

  def test_connect_is_idempotent(self):
    _install_fake_requests()
    connector = paperless_connector.PaperlessConnector(self._config())
    first, second = connector.connect(), connector.connect()
    self.assertIs(first, second)

  def test_non_numeric_timeout_is_config_error(self):
    connector = paperless_connector.PaperlessConnector(self._config(timeout='nie-liczba'))
    _install_fake_requests()
    with self.assertRaises(ConnectorConfigError):
      connector.connect()

  # --- staging -------------------------------------------------------
  def test_stage_bytes_round_trip(self):
    connector = paperless_connector.PaperlessConnector(self._config())
    path = connector.stage_bytes(b'tresc pliku', 'pismo.txt')
    self.assertTrue(path.endswith('pismo.txt'))
    self.assertEqual(connector.read_staged(path), b'tresc pliku')
    connector.discard_staged(path)
    with self.assertRaises(FileNotFoundError):
      connector.read_staged(path)

  def test_sha256_of_is_stable(self):
    self.assertEqual(
      paperless_connector.PaperlessConnector.sha256_of(b'abc'),
      'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad',
    )

  # --- ingest ----------------------------------------------------------
  def test_upload_document_returns_task_id_from_plain_text_body(self):
    session = _install_fake_requests()
    connector = paperless_connector.PaperlessConnector(self._config())
    session.program('/api/documents/post_document/',
                     FakeResponse(text='"3fa85f64-5717-4562-b3fc-2c963f66afa6"'))
    task_id = connector.upload_document(b'%PDF-1.4...', 'pismo.pdf', title='Pismo')
    self.assertEqual(task_id, '3fa85f64-5717-4562-b3fc-2c963f66afa6')
    call = session.calls[-1]
    self.assertEqual(call['data']['title'], 'Pismo')
    self.assertEqual(call['files']['document'], ('pismo.pdf', b'%PDF-1.4...'))

  def test_upload_document_raises_on_bad_status(self):
    session = _install_fake_requests()
    connector = paperless_connector.PaperlessConnector(self._config())
    session.program('/api/documents/post_document/', FakeResponse(status_code=400, text='bad'))
    with self.assertRaises(ConnectorError):
      connector.upload_document(b'x', 'f.pdf')

  def test_get_task_returns_first_result(self):
    session = _install_fake_requests()
    connector = paperless_connector.PaperlessConnector(self._config())
    session.program('/api/tasks/', FakeResponse(json_data=[
      {'task_id': 't1', 'status': 'SUCCESS', 'related_document': 42},
    ]))
    task = connector.get_task('t1')
    self.assertEqual(task['status'], 'SUCCESS')

  def test_get_task_returns_none_when_not_found(self):
    session = _install_fake_requests()
    connector = paperless_connector.PaperlessConnector(self._config())
    session.program('/api/tasks/', FakeResponse(json_data=[]))
    self.assertIsNone(connector.get_task('unknown'))

  def test_task_document_id_prefers_related_document_field(self):
    self.assertEqual(
      paperless_connector.PaperlessConnector.task_document_id(
        {'related_document': 42, 'result': 'ignored'}), 42)

  def test_task_document_id_falls_back_to_result_text(self):
    self.assertEqual(
      paperless_connector.PaperlessConnector.task_document_id(
        {'result': 'New document 123 created'}), 123)

  def test_is_duplicate_task_detects_known_markers(self):
    self.assertTrue(paperless_connector.PaperlessConnector.is_duplicate_task(
      {'result': 'Not consuming X: it is a duplicate of existing document Y'}))
    self.assertFalse(paperless_connector.PaperlessConnector.is_duplicate_task(
      {'result': 'some other failure'}))

  # --- dokumenty -------------------------------------------------------
  def test_get_document(self):
    session = _install_fake_requests()
    connector = paperless_connector.PaperlessConnector(self._config())
    session.program('/api/documents/42/', FakeResponse(json_data={'id': 42, 'title': 'Pismo'}))
    self.assertEqual(connector.get_document(42)['title'], 'Pismo')

  def test_download_document_returns_content_bytes(self):
    session = _install_fake_requests()
    connector = paperless_connector.PaperlessConnector(self._config())
    session.program('/api/documents/42/download/', FakeResponse(content=b'%PDF-bytes'))
    self.assertEqual(connector.download_document(42), b'%PDF-bytes')

  # --- taksonomia: get-or-create z cache ------------------------------
  def test_get_or_create_tag_creates_when_missing_and_caches(self):
    session = _install_fake_requests()
    connector = paperless_connector.PaperlessConnector(self._config())
    session.program('/api/tags/', FakeResponse(json_data={'results': []}), method='get')
    session.program('/api/tags/', FakeResponse(json_data={'id': 9, 'name': 'EDR'}), method='post')
    tag_id = connector.get_or_create_tag('EDR')
    self.assertEqual(tag_id, 9)
    calls = [c for c in session.calls if c['url'].endswith('/api/tags/')]
    self.assertEqual(len(calls), 2)  # GET (lookup) + POST (create)
    self.assertEqual(calls[0]['method'], 'get')
    self.assertEqual(calls[1]['method'], 'post')
    self.assertEqual(calls[1]['json']['name'], 'EDR')
    # drugie wywołanie trafia w cache - brak kolejnych zapytań HTTP
    calls_before = len(session.calls)
    self.assertEqual(connector.get_or_create_tag('EDR'), 9)
    self.assertEqual(len(session.calls), calls_before)

  def test_get_or_create_tag_reuses_existing_by_name(self):
    session = _install_fake_requests()
    connector = paperless_connector.PaperlessConnector(self._config())
    session.program('/api/tags/', FakeResponse(json_data={'results': [{'id': 7, 'name': 'EDR'}]}))
    tag_id = connector.get_or_create_tag('EDR')
    self.assertEqual(tag_id, 7)
    # drugie wywołanie z cache - brak kolejnego zapytania HTTP
    calls_before = len(session.calls)
    self.assertEqual(connector.get_or_create_tag('EDR'), 7)
    self.assertEqual(len(session.calls), calls_before)


if __name__ == '__main__':
  unittest.main()
