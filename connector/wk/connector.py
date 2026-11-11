# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

# Konektor Węzła Krajowego (login.gov.pl) - sekcja [wk] w
# config/config.ini. Klucze i certyfikaty dostawcy usługi (EC P-256, osobne
# do podpisu i szyfrowania) z plików; certyfikaty podpisu WK przypięte
# (idp_certs) albo pobrane z metadanych WK (idp_metadata_url).
#
# Logi bez danych osobowych - ani asercji, ani atrybutów, ani NameID.

from __future__ import annotations

import base64
from datetime import datetime, timezone

import requests
from cryptography.hazmat.primitives.serialization import load_pem_private_key

from connector.base import BaseConnector, ConnectorConfigError, ConnectorError
from connector.wk import saml
from connector.wk.saml import WkError, WkIdentity

# środowisko integracyjne WK - adresy produkcyjne: login.gov.pl
INT_SSO_URL = 'https://int-podmiotyzewnetrzne.login.gov.pl/login/SingleSignOnService'
INT_ARTIFACT_URL = 'https://int-podmiotyzewnetrzne.login.gov.pl/login-services/idpArtifactResolutionService'


def _true(value: str | None) -> bool:
  return str(value or '').strip().lower() in ('1', 'true', 'yes', 'on', 'tak')


class WkConnector(BaseConnector):
  name = 'wk'

  def __init__(self, config=None) -> None:
    super().__init__(config)
    self._idp_certs: list[bytes] | None = None

  @property
  def enabled(self) -> bool:
    return _true(self.config.get('enabled'))

  @property
  def sso_url(self) -> str:
    return self.config.get('sso_url', INT_SSO_URL)

  @property
  def acs_url(self) -> str:
    return self.config.require('acs_url')

  @property
  def issuer(self) -> str:
    return self.config.require('issuer')

  @property
  def timeout(self) -> int:
    return int(self.config.get('timeout', '15'))

  def _read(self, key: str) -> bytes:
    path = self.config.require(key)
    try:
      with open(path, 'rb') as handle:
        return handle.read()
    except OSError as exc:
      raise ConnectorConfigError(f'[wk] {key}: nie można odczytać pliku') from exc

  def connect(self) -> requests.Session:
    if self._client is None:
      self._client = requests.Session()
    return self._client

  def test_connection(self) -> bool:
    try:
      return bool(self.idp_certificates()) and bool(self._read('sign_key'))
    except (ConnectorError, WkError) as exc:
      self.logger.warning('wk: test_connection() nieudany: %s', exc)
      return False

  def idp_certificates(self) -> list[bytes]:
    if self._idp_certs is not None:
      return self._idp_certs
    paths = [p.strip() for p in (self.config.get('idp_certs') or '').split(',') if p.strip()]
    certs: list[bytes] = []
    for path in paths:
      try:
        with open(path, 'rb') as handle:
          certs.extend(saml.split_pem_certificates(handle.read()))
      except OSError as exc:
        raise ConnectorConfigError('[wk] idp_certs: nie można odczytać pliku') from exc
    if not certs and self.config.get('idp_metadata_url'):
      try:
        resp = self.connect().get(self.config.get('idp_metadata_url'), timeout=self.timeout)
        resp.raise_for_status()
      except requests.RequestException as exc:
        raise ConnectorError(f'WK: metadane niedostępne ({type(exc).__name__})') from exc
      certs = saml.signing_certificates_from_metadata(resp.content)
    if not certs:
      raise ConnectorConfigError('[wk] brak certyfikatów WK (idp_certs albo idp_metadata_url)')
    self._idp_certs = certs
    return certs

  # --- logowanie ---

  def authn_request(self, now: datetime | None = None) -> tuple[str, str]:
    """(ID żądania, SAMLRequest base64) do formularza POST na sso_url."""
    request_id = saml.new_request_id()
    xml = saml.build_authn_request(
      request_id=request_id, instant=saml.issue_instant(now), issuer=self.issuer,
      provider_name=self.config.get('provider_name', self.issuer), acs_url=self.acs_url,
      destination=self.sso_url, sp_type=self.config.get('sp_type', 'public'),
      loa=self.config.get('loa', 'http://eidas.europa.eu/LoA/substantial'),
      force_authn=_true(self.config.get('force_authn', 'true')))
    signed = saml.sign(xml, self._read('sign_key'), self._read('sign_cert'))
    return request_id, base64.b64encode(signed.encode('utf-8')).decode('ascii')

  def resolve_artifact(self, artifact: str, expected_request_id: str,
                       now: datetime | None = None) -> WkIdentity:
    now = now or datetime.now(timezone.utc)
    xml = saml.build_artifact_resolve(request_id=saml.new_request_id(),
                                      instant=saml.issue_instant(now), issuer=self.issuer,
                                      artifact=artifact)
    # ArtifactResolve podpisywany kluczem szyfrującym - tak przyjął go WK
    # w sprawdzonej implementacji; artifact_sign_key=sign zmienia na klucz podpisu
    prefix = 'sign' if self.config.get('artifact_sign_key', 'enc') == 'sign' else 'enc'
    signed = saml.sign(xml, self._read(f'{prefix}_key'), self._read(f'{prefix}_cert'))
    try:
      resp = self.connect().post(
        self.config.get('artifact_resolve_url', INT_ARTIFACT_URL), data=signed.encode('utf-8'),
        headers={'Content-Type': 'application/soap+xml; charset=utf-8'}, timeout=self.timeout)
    except requests.RequestException as exc:
      raise WkError('wk_niedostepny', type(exc).__name__) from exc
    if not resp.ok:
      raise WkError('wk_niedostepny', f'HTTP {resp.status_code}')
    root = saml.parse_xml(resp.content)
    response = saml.verified_response(root, self.idp_certificates())
    private_key = load_pem_private_key(self._read('enc_key'), password=None)
    return saml.identity_from_response(response, private_key, expected_request_id=expected_request_id,
                                       acs_url=self.acs_url, issuer=self.issuer, now=now)


_connector: WkConnector | None = None


def get_connector() -> WkConnector:
  global _connector
  if _connector is None:
    _connector = WkConnector()
  return _connector
