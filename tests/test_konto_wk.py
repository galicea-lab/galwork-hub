# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

# login.gov.pl w usłudze kont - podpis żądań, weryfikacja i
# deszyfrowanie odpowiedzi WK (atrapa WK z własnymi kluczami testowymi),
# przepływy założenia konta i nowego hasła.

import base64
import importlib.util
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

import requests
import xmlsec
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.concatkdf import ConcatKDFHash
from cryptography.hazmat.primitives.keywrap import aes_key_wrap
from cryptography.x509.oid import NameOID
from lxml import etree
from requests.adapters import BaseAdapter

from connector.base import ConnectorConfig
from connector.wk import saml
from connector.wk.connector import WkConnector
from connector.wk.saml import NS, WkError, WkIdentity
from konto.models import KontoWkSession
from konto.wk import WkService, identity_key
from tests.test_konto import ServiceTestCase

HAS_FASTAPI = importlib.util.find_spec('fastapi') is not None
ISSUER = 'galwork.test'
ACS = 'https://konto.galwork.test/v1/konto/wk/acs'
PESEL = '44051401359'


def key_and_cert(cn):
  key = ec.generate_private_key(ec.SECP256R1())
  name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
  now = datetime.now(timezone.utc)
  cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
          .public_key(key.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=30))
          .sign(key, hashes.SHA256()))
  key_pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                              serialization.NoEncryption())
  return key, key_pem, cert.public_bytes(serialization.Encoding.PEM)


SP_SIGN = key_and_cert('sp-sign')
SP_ENC = key_and_cert('sp-enc')
IDP = key_and_cert('wk-idp')
OTHER = key_and_cert('intruz')


def assertion_xml(request_id, not_after, person=PESEL):
  attrs = ''.join(
    f'<saml2:Attribute Name="{saml.ATTR}{name}"><saml2:AttributeValue>{value}</saml2:AttributeValue>'
    '</saml2:Attribute>'
    for name, value in (('CurrentGivenName', 'ANNA'), ('CurrentFamilyName', 'NOWAK'),
                        ('DateOfBirth', '1944-05-14'), ('PersonIdentifier', person)))
  return (
    f'<saml2:Assertion xmlns:saml2="{NS["saml2"]}" ID="A-1" Version="2.0">'
    '<saml2:Subject><saml2:NameID>nameid-1</saml2:NameID><saml2:SubjectConfirmation>'
    f'<saml2:SubjectConfirmationData InResponseTo="{request_id}" Recipient="{ACS}" '
    f'NotOnOrAfter="{not_after}"/></saml2:SubjectConfirmation></saml2:Subject>'
    f'<saml2:Conditions><saml2:AudienceRestriction><saml2:Audience>{ISSUER}</saml2:Audience>'
    '</saml2:AudienceRestriction></saml2:Conditions>'
    '<saml2:AuthnStatement SessionIndex="sess-1"/>'
    f'<saml2:AttributeStatement>{attrs}</saml2:AttributeStatement></saml2:Assertion>').encode()


def encrypted_assertion(plain, recipient_public_key):
  ephemeral = ec.generate_private_key(ec.SECP256R1())
  shared = ephemeral.exchange(ec.ECDH(), recipient_public_key)
  kek = ConcatKDFHash(algorithm=hashes.SHA256(), length=32,
                      otherinfo=bytes.fromhex('0a0b0c')).derive(shared)
  cek = os.urandom(32)
  nonce = os.urandom(12)
  payload = nonce + AESGCM(cek).encrypt(nonce, plain, None)
  public = ephemeral.public_key().public_bytes(serialization.Encoding.X962,
                                               serialization.PublicFormat.UncompressedPoint)
  b64 = lambda data: base64.b64encode(data).decode()  # noqa: E731
  return (
    f'<saml2:EncryptedAssertion><xenc:EncryptedData xmlns:xenc="{NS["xenc"]}" xmlns:ds="{NS["ds"]}">'
    '<xenc:EncryptionMethod Algorithm="http://www.w3.org/2009/xmlenc11#aes256-gcm"/>'
    '<ds:KeyInfo><xenc:EncryptedKey>'
    '<xenc:EncryptionMethod Algorithm="http://www.w3.org/2001/04/xmlenc#kw-aes256"/>'
    '<ds:KeyInfo><xenc:AgreementMethod Algorithm="http://www.w3.org/2009/xmlenc11#ECDH-ES">'
    f'<xenc11:KeyDerivationMethod xmlns:xenc11="{NS["xenc11"]}" '
    'Algorithm="http://www.w3.org/2009/xmlenc11#ConcatKDF">'
    '<xenc11:ConcatKDFParams AlgorithmID="0a" PartyUInfo="0b" PartyVInfo="0c"/>'
    '</xenc11:KeyDerivationMethod><xenc:OriginatorKeyInfo><ds:KeyValue>'
    f'<dsig11:ECKeyValue xmlns:dsig11="{NS["dsig11"]}"><dsig11:PublicKey>{b64(public)}</dsig11:PublicKey>'
    '</dsig11:ECKeyValue></ds:KeyValue></xenc:OriginatorKeyInfo></xenc:AgreementMethod></ds:KeyInfo>'
    f'<xenc:CipherData><xenc:CipherValue>{b64(aes_key_wrap(kek, cek))}</xenc:CipherValue></xenc:CipherData>'
    '</xenc:EncryptedKey></ds:KeyInfo>'
    f'<xenc:CipherData><xenc:CipherValue>{b64(payload)}</xenc:CipherValue></xenc:CipherData>'
    '</xenc:EncryptedData></saml2:EncryptedAssertion>')


def artifact_response(request_id, signer=IDP, status=saml.STATUS_SUCCESS, minutes=5, person=PESEL,
                      assertion_request_id=None):
  not_after = (datetime.now(timezone.utc) + timedelta(minutes=minutes)).strftime('%Y-%m-%dT%H:%M:%SZ')
  enc = encrypted_assertion(assertion_xml(assertion_request_id or request_id, not_after, person),
                            SP_ENC[0].public_key())
  xml = (
    f'<soap:Envelope xmlns:soap="{NS["soap"]}"><soap:Body>'
    f'<saml2p:ArtifactResponse xmlns:saml2p="{NS["saml2p"]}" xmlns:saml2="{NS["saml2"]}" '
    'ID="AR-1" Version="2.0" IssueInstant="2026-11-09T10:00:00Z">'
    '<saml2:Issuer>https://login.gov.pl</saml2:Issuer>'
    f'<saml2p:Status><saml2p:StatusCode Value="{saml.STATUS_SUCCESS}"/></saml2p:Status>'
    f'<saml2p:Response ID="R-1" InResponseTo="{request_id}" Version="2.0">'
    f'<saml2p:Status><saml2p:StatusCode Value="{status}"/></saml2p:Status>{enc}</saml2p:Response>'
    '</saml2p:ArtifactResponse></soap:Body></soap:Envelope>')
  return saml.sign(xml, signer[1], signer[2]).encode()


class RawAdapter(BaseAdapter):
  """Atrapa usługi ArtifactResolve WK - zwraca przygotowaną odpowiedź."""

  def __init__(self, body):
    super().__init__()
    self.body = body
    self.sent = []

  def send(self, request, **kwargs):
    self.sent.append(request)
    response = requests.Response()
    response.status_code = 200
    response._content = self.body
    response.request = request
    return response

  def close(self):
    pass


class SamlTest(unittest.TestCase):

  def setUp(self):
    self.tmp = tempfile.TemporaryDirectory()
    self.addCleanup(self.tmp.cleanup)
    files = {}
    for name, (_, key_pem, cert_pem) in {'sign': SP_SIGN, 'enc': SP_ENC, 'idp': IDP}.items():
      for suffix, data in (('key', key_pem), ('cert', cert_pem)):
        path = os.path.join(self.tmp.name, f'{name}.{suffix}')
        with open(path, 'wb') as handle:
          handle.write(data)
        files[f'{name}_{suffix}'] = path
    self.connector = WkConnector(ConnectorConfig('wk', {
      'enabled': 'true', 'issuer': ISSUER, 'acs_url': ACS, 'provider_name': 'galwork',
      'sign_key': files['sign_key'], 'sign_cert': files['sign_cert'],
      'enc_key': files['enc_key'], 'enc_cert': files['enc_cert'], 'idp_certs': files['idp_cert']}))

  def resolve(self, body, request_id='ID-req'):
    adapter = RawAdapter(body)
    self.connector.connect().mount('https://', adapter)
    return self.connector.resolve_artifact('ART-1', request_id), adapter

  def verify_signed(self, xml, cert_pem):
    root = etree.fromstring(xml.encode())
    xmlsec.tree.add_ids(root, ['ID'])
    ctx = xmlsec.SignatureContext()
    ctx.key = xmlsec.Key.from_memory(cert_pem, xmlsec.KeyFormat.CERT_PEM, None)
    ctx.verify(root.find('.//ds:Signature', NS))
    return root

  def test_authn_request_is_signed_by_sp(self):
    request_id, b64 = self.connector.authn_request()
    xml = base64.b64decode(b64).decode()
    root = self.verify_signed(xml, SP_SIGN[2])
    self.assertEqual(root.get('ID'), request_id)
    self.assertEqual(root.get('AssertionConsumerServiceURL'), ACS)
    self.assertEqual(root.get('ProtocolBinding'), 'urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Artifact')
    self.assertIn('<eidas:SPType>public</eidas:SPType>', xml)
    value = root.find('.//ds:SignatureValue', NS).text
    self.assertNotIn('\n', value)

  def test_artifact_resolved_and_assertion_decrypted(self):
    identity, adapter = self.resolve(artifact_response('ID-req'))
    self.assertEqual((identity.first_name, identity.last_name, identity.person_identifier),
                     ('ANNA', 'NOWAK', PESEL))
    self.assertEqual(identity.session_index, 'sess-1')
    self.assertNotIn(PESEL, repr(identity))
    # ArtifactResolve podpisany kluczem szyfrującym SP
    sent = adapter.sent[0].body.decode()
    self.assertIn('<saml2p:Artifact>ART-1</saml2p:Artifact>', sent)
    self.verify_signed(sent, SP_ENC[2])

  def test_signature_from_other_key_is_rejected(self):
    with self.assertRaises(WkError) as ctx:
      self.resolve(artifact_response('ID-req', signer=OTHER))
    self.assertEqual(ctx.exception.code, 'niepoprawny_podpis')

  def test_tampered_response_is_rejected(self):
    body = artifact_response('ID-req').replace(b'InResponseTo="ID-req" Version', b'InResponseTo="ID-req"  Version')
    body = body.replace(b'Version="2.0" IssueInstant', b'Version="2.0" Consent="x" IssueInstant')
    with self.assertRaises(WkError):
      self.resolve(body)

  def test_wrong_in_response_to(self):
    with self.assertRaises(WkError):
      self.resolve(artifact_response('ID-req'), request_id='ID-other')
    with self.assertRaises(WkError):
      self.resolve(artifact_response('ID-req', assertion_request_id='ID-other'))

  def test_expired_assertion(self):
    with self.assertRaises(WkError):
      self.resolve(artifact_response('ID-req', minutes=-10))

  def test_cancelled_login(self):
    with self.assertRaises(WkError) as ctx:
      self.resolve(artifact_response('ID-req', status='urn:oasis:names:tc:SAML:2.0:status:AuthnFailed'))
    self.assertEqual(ctx.exception.code, 'anulowano')

  def test_duplicate_ids_rejected(self):
    body = artifact_response('ID-req').replace(b'ID="A-1"', b'ID="A-1"')
    root = etree.fromstring(body)
    response = root.find('.//saml2p:Response', NS)
    clone = etree.fromstring(etree.tostring(response))
    root.find('.//soap:Body', NS).append(clone)
    with self.assertRaises(WkError):
      self.resolve(etree.tostring(root))

  def test_metadata_certificates(self):
    der = base64.b64encode(x509.load_pem_x509_certificate(IDP[2]).public_bytes(
      serialization.Encoding.DER)).decode()
    metadata = (f'<md:EntityDescriptor xmlns:md="{NS["md"]}" xmlns:ds="{NS["ds"]}"><md:IDPSSODescriptor>'
                f'<md:KeyDescriptor use="signing"><ds:KeyInfo><ds:X509Data><ds:X509Certificate>{der}'
                '</ds:X509Certificate></ds:X509Data></ds:KeyInfo></md:KeyDescriptor>'
                '<md:KeyDescriptor use="encryption"><ds:KeyInfo><ds:X509Data><ds:X509Certificate>AAAA'
                '</ds:X509Certificate></ds:X509Data></ds:KeyInfo></md:KeyDescriptor>'
                '</md:IDPSSODescriptor></md:EntityDescriptor>').encode()
    certs = saml.signing_certificates_from_metadata(metadata)
    self.assertEqual(len(certs), 1)
    x509.load_pem_x509_certificate(certs[0])

  def test_identity_key(self):
    self.assertEqual(identity_key(PESEL), ('pesel', PESEL))
    self.assertEqual(identity_key('de/pl/abc123'), ('eid', 'DE/PL/ABC123'))


class FakeWk:
  enabled = True
  sso_url = 'https://wk.test/sso'

  def __init__(self):
    self.requests = []
    self.identity = WkIdentity('ANNA', 'NOWAK', '1944-05-14', PESEL)

  def authn_request(self, now=None):
    request_id = f'ID-{len(self.requests)}'
    self.requests.append(request_id)
    return request_id, 'U0FNTA=='

  def resolve_artifact(self, artifact, expected_request_id, now=None):
    if artifact != 'ART' or expected_request_id != self.requests[-1]:
      raise WkError('niepoprawna_odpowiedz')
    return self.identity


class WkFlowTest(ServiceTestCase):

  def setUp(self):
    super().setUp()
    self.wk = FakeWk()
    self.flow = WkService(self.service, self.wk)

  def verified(self, cel='rejestracja'):
    start = self.flow.start(cel, '203.0.113.5')
    ticket, returned = self.flow.acs('ART', start.relay_state)
    self.assertEqual(returned, cel)
    return start, ticket

  def test_register_with_verified_identity(self):
    _, ticket = self.verified()
    info = self.flow.info(ticket)
    self.assertEqual((info['first_name'], info['last_name'], info['account_exists']),
                     ('ANNA', 'NOWAK', False))
    self.flow.register(ticket, 'anna@example.com', 'Haslo-1234')
    user = self.keycloak.find_by_email('anna@example.com')[0]
    self.assertEqual((user['firstName'], user['lastName']), ('ANNA', 'NOWAK'))
    self.assertEqual(user['attributes']['identity_verified'], ['wk'])
    self.assertNotIn(PESEL, user['attributes']['pesel_hmac'][0])
    # ten sam PESEL co przy rejestracji SMS-em (pesel_hmac)
    self.assertEqual(user['attributes']['pesel_hmac'][0], self.service._hash('pesel', PESEL))
    session = self.db.query(KontoWkSession).one()
    self.assertIsNone(session.first_name)
    self.assertIsNone(session.identity_hash)
    with self.assertRaises(Exception):
      self.flow.register(ticket, 'anna2@example.com', 'Haslo-1234')

  def test_second_account_for_same_pesel_refused(self):
    _, ticket = self.verified()
    self.flow.register(ticket, 'anna@example.com', 'Haslo-1234')
    _, ticket = self.verified()
    self.assertTrue(self.flow.info(ticket)['account_exists'])
    with self.assertRaises(Exception) as ctx:
      self.flow.register(ticket, 'inna@example.com', 'Haslo-1234')
    self.assertEqual(ctx.exception.status, 409)

  def test_reset_password_without_quota(self):
    self.service = self.make_service(pesel='optional')
    self.register_sms(pesel=PESEL)
    self.flow = WkService(self.service, self.wk)
    user = self.keycloak.find_by_email('anna@example.com')[0]
    for _ in range(4):  # więcej niż pula kodów SMS
      _, ticket = self.verified('reset')
      self.flow.reset(ticket, 'Nowe-haslo-1')
    self.assertEqual(user['password'], 'Nowe-haslo-1')
    self.assertIn(('logout', user['id']), self.keycloak.calls)

  def test_reset_without_account(self):
    _, ticket = self.verified('reset')
    with self.assertRaises(Exception) as ctx:
      self.flow.reset(ticket, 'Nowe-haslo-1')
    self.assertEqual(ctx.exception.status, 404)

  def test_relay_state_single_use_and_purpose(self):
    start, ticket = self.verified()
    with self.assertRaises(WkError):
      self.flow.acs('ART', start.relay_state)
    with self.assertRaises(Exception):
      self.flow.reset(ticket, 'Nowe-haslo-1')  # bilet rejestracji

  def test_expiry(self):
    start = self.flow.start('rejestracja', None)
    self.tick(601)
    with self.assertRaises(WkError):
      self.flow.acs('ART', start.relay_state)
    _, ticket = self.verified()
    self.tick(901)
    with self.assertRaises(Exception):
      self.flow.info(ticket)

  def test_failed_artifact_kills_relay_state(self):
    start = self.flow.start('rejestracja', None)
    with self.assertRaises(WkError):
      self.flow.acs('ZLY', start.relay_state)
    with self.assertRaises(WkError):
      self.flow.acs('ART', start.relay_state)

  def test_ip_limit(self):
    self.service = self.make_service(tickets_per_ip_per_hour='2')
    self.flow = WkService(self.service, self.wk)
    self.flow.start('reset', '203.0.113.9')
    self.flow.start('reset', '203.0.113.9')
    with self.assertRaises(Exception) as ctx:
      self.flow.start('reset', '203.0.113.9')
    self.assertEqual(ctx.exception.status, 429)


@unittest.skipUnless(HAS_FASTAPI, 'FastAPI niedostępne')
class WkRouterTest(ServiceTestCase):

  def setUp(self):
    super().setUp()
    from unittest.mock import patch

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from konto import router as konto_router

    self.wk = FakeWk()
    app = FastAPI()
    app.include_router(konto_router.router)
    app.dependency_overrides[konto_router.get_wk_service] = lambda: WkService(self.service, self.wk)
    self.client = TestClient(app)
    portal = WkConnector(ConnectorConfig('wk', {'portal_url': 'https://portal.test/rekrutacja/wk'}))
    patcher = patch('connector.wk.connector.get_connector', return_value=portal)
    patcher.start()
    self.addCleanup(patcher.stop)

  def test_start_acs_and_register(self):
    resp = self.client.get('/wk/start', params={'cel': 'rejestracja'})
    self.assertEqual(resp.status_code, 200)
    self.assertIn('action="https://wk.test/sso"', resp.text)
    relay = resp.text.split('name="RelayState" value="')[1].split('"')[0]
    resp = self.client.post('/wk/acs', data={'SAMLart': 'ART', 'RelayState': relay},
                            follow_redirects=False)
    self.assertEqual(resp.status_code, 303)
    location = resp.headers['location']
    self.assertTrue(location.startswith('https://portal.test/rekrutacja/wk#wk='))
    ticket = location.split('#wk=')[1].split('&')[0]
    self.assertEqual(self.client.post('/wk/dane', json={'ticket': ticket}).json()['last_name'], 'NOWAK')
    resp = self.client.post('/wk/rejestracja', json={'ticket': ticket, 'email': 'anna@example.com',
                                                     'password': 'Haslo-1234'})
    self.assertEqual(resp.status_code, 201, resp.text)

  def test_acs_error_redirects_with_code(self):
    resp = self.client.get('/wk/acs', params={'SAMLart': 'ART', 'RelayState': 'nie-ma'},
                           follow_redirects=False)
    self.assertEqual(resp.headers['location'], 'https://portal.test/rekrutacja/wk#wk_blad=sesja_wygasla')


if __name__ == '__main__':
  unittest.main()
