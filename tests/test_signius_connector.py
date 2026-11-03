# tests/test_signius_connector.py
# testy konektora Signius (refaktoring connector/signius -
# zob. connector/signius/connector.py). Nie wymagają żywego certyfikatu
# PKCS#12 ani serwera Signius - podmieniamy moduł `requests_pkcs12` na
# atrapę, konfigurację przez `ConnectorConfig` w pamięci (jak
# tests/test_frappe_education_connector.py dla `requests`, tylko tu
# transportem są funkcje modułu `requests_pkcs12`, nie `requests.Session`).
from __future__ import annotations

import json
import sys
import types
import unittest

from connector.base import ConnectorConfig, ConnectorConfigError, ConnectorError
from connector.signius import connector as signius_connector


class FakeResponse:
  def __init__(self, status_code=200, json_data=None, content=b''):
    self.status_code = status_code
    self.ok = 200 <= status_code < 300
    self._json = json_data if json_data is not None else {}
    self.content = content
    self.text = json.dumps(self._json)

  def json(self):
    return self._json


class FakePkcs12Module(types.ModuleType):
  """Atrapa modułu `requests_pkcs12` - zapisuje wywołania (razem z
  `pkcs12_filename`/`pkcs12_password`/`verify`), oddaje zaprogramowane
  odpowiedzi po dopasowaniu końcówki URL."""

  def __init__(self):
    super().__init__('requests_pkcs12')
    self.calls: list[dict] = []
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


def _install_fake_pkcs12():
  fake_module = FakePkcs12Module()
  sys.modules['requests_pkcs12'] = fake_module
  return fake_module


class SigniusConnectorTest(unittest.TestCase):

  def setUp(self):
    signius_connector.reset_connector()
    self.addCleanup(signius_connector.reset_connector)
    sys.modules.pop('requests_pkcs12', None)
    self.addCleanup(sys.modules.pop, 'requests_pkcs12', None)

  def _config(self, **overrides):
    values = {'p12_file': '/secret/signius.p12', 'p12_password': 'pw', 'timeout': '5'}
    values.update(overrides)
    return ConnectorConfig(section='signius', values=values)

  # ------------------------------------------------------------------
  def test_missing_config_raises_config_error(self):
    connector = signius_connector.SigniusConnector(
      ConnectorConfig(section='signius', values={}))
    with self.assertRaises(ConnectorConfigError):
      connector.connect()

  def test_connect_reads_default_base_url_and_verify_false(self):
    _install_fake_pkcs12()
    connector = signius_connector.SigniusConnector(self._config())
    connector.connect()
    self.assertEqual(connector.base_url, 'https://professional.signius.eu:8900')
    self.assertFalse(connector._verify_tls)

  def test_verify_tls_can_be_enabled_via_config(self):
    _install_fake_pkcs12()
    connector = signius_connector.SigniusConnector(self._config(verify_tls='true'))
    connector.connect()
    self.assertTrue(connector._verify_tls)

  def test_connect_is_idempotent(self):
    _install_fake_pkcs12()
    connector = signius_connector.SigniusConnector(self._config())
    first, second = connector.connect(), connector.connect()
    self.assertIs(first, second)

  def test_non_numeric_timeout_is_config_error(self):
    connector = signius_connector.SigniusConnector(self._config(timeout='nie-liczba'))
    _install_fake_pkcs12()
    with self.assertRaises(ConnectorConfigError):
      connector.connect()

  def test_create_folder_posts_name_and_returns_json(self):
    module = _install_fake_pkcs12()
    connector = signius_connector.SigniusConnector(self._config())
    module.program(
      '/services/document-folders',
      FakeResponse(json_data={'documentFolderId': 'f-1'}),
    )
    result = connector.create_folder('korespondencja')
    self.assertEqual(result, {'documentFolderId': 'f-1'})
    call = module.calls[-1]
    self.assertEqual(call['method'], 'POST')
    self.assertEqual(call['json'], {'name': 'korespondencja'})
    self.assertEqual(call['pkcs12_filename'], '/secret/signius.p12')
    self.assertEqual(call['pkcs12_password'], 'pw')

  def test_upload_document_sends_raw_bytes_not_base64(self):
    """Regresja na błąd starego `upload_request_file`: base64-kodowanie
    treści plików przed wysłaniem w `files=` dawało 500 po stronie
    Signius - multipart ma przenosić surowe bajty."""
    module = _install_fake_pkcs12()
    connector = signius_connector.SigniusConnector(self._config())
    module.program(
      '/services/document-folders/f-1/documents',
      FakeResponse(json_data={'documentId': 'd-1'}),
    )
    pdf_bytes = b'%PDF-1.4 raw bytes not base64'
    result = connector.upload_document('f-1', 'wniosek.pdf', pdf_bytes)
    self.assertEqual(result, {'documentId': 'd-1'})
    call = module.calls[-1]
    self.assertEqual(call['files']['file'], ('wniosek.pdf', pdf_bytes, 'application/pdf'))

  def test_request_signature_sends_qualified_signee(self):
    module = _install_fake_pkcs12()
    connector = signius_connector.SigniusConnector(self._config())
    module.program(
      '/services/documents/d-1',
      FakeResponse(json_data={'id': 'd-1', 'status': 'SENT'}),
    )
    result = connector.request_signature('d-1', 'jan@example.com', '+48600000000', 'Wniosek')
    self.assertEqual(result['status'], 'SENT')
    call = module.calls[-1]
    self.assertEqual(call['method'], 'PUT')
    self.assertEqual(call['json']['signeeSignatureType'], 'QUALIFIED')
    self.assertEqual(
      call['json']['signeesUsers'],
      [{'signeeEmail': 'jan@example.com', 'signeePhone': '+48600000000', 'signingOrder': 1}],
    )

  def test_get_document_status_finds_matching_document_in_folder(self):
    module = _install_fake_pkcs12()
    connector = signius_connector.SigniusConnector(self._config())
    module.program(
      '/services/documents/document-folders/f-1',
      FakeResponse(json_data=[
        {'id': 'd-1', 'status': 'SIGNED'},
        {'id': 'd-2', 'status': 'SENT'},
      ]),
    )
    self.assertEqual(connector.get_document_status('d-1', 'f-1'), 'SIGNED')
    self.assertEqual(connector.get_document_status('d-2', 'f-1'), 'SENT')
    self.assertIsNone(connector.get_document_status('d-nieznane', 'f-1'))

  def test_get_signed_document_returns_bytes(self):
    module = _install_fake_pkcs12()
    connector = signius_connector.SigniusConnector(self._config())
    module.program(
      '/services/documents/d-1/signed',
      FakeResponse(content=b'binarna-zawartosc-pdf'),
    )
    self.assertEqual(connector.get_signed_document('d-1'), b'binarna-zawartosc-pdf')

  def test_request_raises_connector_error_on_bad_status(self):
    module = _install_fake_pkcs12()
    connector = signius_connector.SigniusConnector(self._config())
    module.program('/services/document-folders', FakeResponse(status_code=500))
    with self.assertRaises(ConnectorError):
      connector.create_folder('x')

  def test_test_connection_true_even_on_404(self):
    """404 na nieistniejący folder = certyfikat zaakceptowany, serwer
    odpowiedział - to jest "zdrowe" połączenie, nie błąd."""
    module = _install_fake_pkcs12()
    connector = signius_connector.SigniusConnector(self._config())
    # nic nie programujemy -> FakePkcs12Module odda domyślne 404
    self.assertTrue(connector.test_connection())

  def test_test_connection_false_on_missing_config(self):
    connector = signius_connector.SigniusConnector(
      ConnectorConfig(section='signius', values={}))
    self.assertFalse(connector.test_connection())

  def test_get_connector_is_singleton(self):
    self.assertIs(signius_connector.get_connector(), signius_connector.get_connector())


if __name__ == '__main__':
  unittest.main()
