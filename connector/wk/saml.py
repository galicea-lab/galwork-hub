# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

# SAML Węzła Krajowego (login.gov.pl) - po stronie dostawcy
# usługi (SP), wiązanie HTTP-Artifact.
#
#   * AuthnRequest i ArtifactResolve podpisane ECDSA-SHA256, exc-c14n,
#     Reference "#ID", InclusiveNamespaces "ds saml2 saml2p xenc"
#     (format sprawdzony przez operatora WK w implementacji e-talar),
#   * podpisy odpowiedzi weryfikowane WYŁĄCZNIE przypiętymi certyfikatami WK
#     (nie certyfikatem z samej wiadomości), z ochroną przed podmianą
#     elementów (unikalne ID, podpisany przodek asercji),
#   * asercja szyfrowana: ECDH-ES (P-256) + ConcatKDF (SHA-256) + AES-KW +
#     AES-GCM,
#   * sprawdzane InResponseTo, Recipient, Audience i terminy ważności.

from __future__ import annotations

import base64
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from xml.sax.saxutils import escape, quoteattr

import xmlsec
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.concatkdf import ConcatKDFHash
from cryptography.hazmat.primitives.keywrap import InvalidUnwrap, aes_key_unwrap
from lxml import etree

NS = {
  'soap': 'http://schemas.xmlsoap.org/soap/envelope/',
  'saml2p': 'urn:oasis:names:tc:SAML:2.0:protocol',
  'saml2': 'urn:oasis:names:tc:SAML:2.0:assertion',
  'ds': 'http://www.w3.org/2000/09/xmldsig#',
  'xenc': 'http://www.w3.org/2001/04/xmlenc#',
  'xenc11': 'http://www.w3.org/2009/xmlenc11#',
  'dsig11': 'http://www.w3.org/2009/xmldsig11#',
  'md': 'urn:oasis:names:tc:SAML:2.0:metadata',
}
STATUS_SUCCESS = 'urn:oasis:names:tc:SAML:2.0:status:Success'
ATTR = 'http://eidas.europa.eu/attributes/naturalperson/'
KEY_WRAP_LENGTH = {
  'http://www.w3.org/2001/04/xmlenc#kw-aes128': 16,
  'http://www.w3.org/2001/04/xmlenc#kw-aes192': 24,
  'http://www.w3.org/2001/04/xmlenc#kw-aes256': 32,
}
DATA_GCM = {
  'http://www.w3.org/2009/xmlenc11#aes128-gcm',
  'http://www.w3.org/2009/xmlenc11#aes192-gcm',
  'http://www.w3.org/2009/xmlenc11#aes256-gcm',
}
ECDH_ES = 'http://www.w3.org/2009/xmlenc11#ECDH-ES'
CLOCK_SKEW = timedelta(seconds=90)


class WkError(Exception):
  """Odpowiedź WK odrzucona. `code` - krótki kod dla portalu, bez danych osobowych."""

  def __init__(self, code: str, detail: str = ''):
    super().__init__(f'{code}: {detail}' if detail else code)
    self.code = code


@dataclass(frozen=True)
class WkIdentity:
  first_name: str
  last_name: str
  birth_date: str
  person_identifier: str
  name_id: str | None = None
  session_index: str | None = None

  def __repr__(self) -> str:
    # bez danych osobowych - repr trafia do logów i śladów stosu
    return 'WkIdentity(<ukryte>)'


def _parser() -> etree.XMLParser:
  # bez remove_blank_text - białe znaki są częścią podpisanej treści
  return etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False,
                         load_dtd=False, dtd_validation=False)


def parse_xml(data: bytes | str) -> etree._Element:
  if isinstance(data, str):
    data = data.encode('utf-8')
  try:
    root = etree.fromstring(data, _parser())
  except etree.XMLSyntaxError as exc:
    raise WkError('niepoprawny_xml') from exc
  if root.getroottree().docinfo.doctype:
    raise WkError('niepoprawny_xml', 'DTD')
  return root


def new_request_id() -> str:
  return f'ID-{uuid.uuid4()}'


def issue_instant(now: datetime | None = None) -> str:
  return (now or datetime.now(timezone.utc)).strftime('%Y-%m-%dT%H:%M:%SZ')


def build_authn_request(*, request_id: str, instant: str, issuer: str, provider_name: str,
                        acs_url: str, destination: str, sp_type: str = 'public',
                        loa: str = 'http://eidas.europa.eu/LoA/substantial',
                        force_authn: bool = True) -> str:
  requested = ''.join(
    f'<eidas:RequestedAttribute FriendlyName="{friendly}" Name="{ATTR}{name}" '
    'NameFormat="urn:oasis:names:tc:SAML:2.0:attrname-format:uri" isRequired="true"/>'
    for friendly, name in (('FamilyName', 'CurrentFamilyName'), ('FirstName', 'CurrentGivenName'),
                           ('DateOfBirth', 'DateOfBirth'), ('PersonIdentifier', 'PersonIdentifier')))
  return (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<saml2p:AuthnRequest xmlns:saml2p="urn:oasis:names:tc:SAML:2.0:protocol" '
    'xmlns:ds="http://www.w3.org/2000/09/xmldsig#" xmlns:eidas="http://eidas.europa.eu/saml-extensions" '
    'xmlns:naturalperson="http://eidas.europa.eu/attributes/naturalperson" '
    'xmlns:saml2="urn:oasis:names:tc:SAML:2.0:assertion" xmlns:xenc="http://www.w3.org/2001/04/xmlenc#" '
    f'AssertionConsumerServiceURL={quoteattr(acs_url)} Destination={quoteattr(destination)} '
    f'ForceAuthn="{"true" if force_authn else "false"}" ID={quoteattr(request_id)} '
    f'IssueInstant={quoteattr(instant)} '
    'ProtocolBinding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Artifact" '
    f'ProviderName={quoteattr(provider_name)} Version="2.0">\n'
    f'<saml2:Issuer>{escape(issuer)}</saml2:Issuer>\n'
    '<saml2p:Extensions>\n'
    f'<eidas:SPType>{escape(sp_type)}</eidas:SPType>\n'
    f'<eidas:RequestedAttributes>{requested}</eidas:RequestedAttributes>\n'
    '</saml2p:Extensions>\n'
    '<saml2p:NameIDPolicy AllowCreate="true" Format="urn:oasis:names:tc:SAML:1.1:nameid-format:unspecified"/>\n'
    '<saml2p:RequestedAuthnContext Comparison="minimum">\n'
    f'<saml2:AuthnContextClassRef>{escape(loa)}</saml2:AuthnContextClassRef>\n'
    '</saml2p:RequestedAuthnContext>\n'
    '</saml2p:AuthnRequest>')


def build_artifact_resolve(*, request_id: str, instant: str, issuer: str, artifact: str) -> str:
  return (
    '<?xml version="1.0" encoding="UTF-8" standalone="no"?>\n'
    '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">\n'
    '<SOAP-ENV:Header xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/"/>\n'
    '<soap:Body>\n'
    '<saml2p:ArtifactResolve xmlns:ds="http://www.w3.org/2000/09/xmldsig#" '
    'xmlns:saml2="urn:oasis:names:tc:SAML:2.0:assertion" '
    'xmlns:saml2p="urn:oasis:names:tc:SAML:2.0:protocol" '
    'xmlns:xenc="http://www.w3.org/2001/04/xmlenc#" '
    f'ID={quoteattr(request_id)} IssueInstant={quoteattr(instant)} Version="2.0">\n'
    f'<saml2:Issuer>{escape(issuer)}</saml2:Issuer>\n'
    f'<saml2p:Artifact>{escape(artifact)}</saml2p:Artifact>\n'
    '</saml2p:ArtifactResolve>\n'
    '</soap:Body>\n'
    '</soap:Envelope>')


def sign(xml: str, key_pem: bytes, cert_pem: bytes,
         prefix_list: str = 'ds saml2 saml2p xenc') -> str:
  """Podpis enveloped elementu nadrzędnego saml2:Issuer (Reference "#ID")."""
  root = parse_xml(xml)
  issuer = root.find('.//saml2:Issuer', NS)
  if issuer is None:
    raise ValueError('Brak saml2:Issuer w podpisywanym dokumencie')
  target = issuer.getparent()
  target_id = target.get('ID')
  if not target_id:
    raise ValueError('Podpisywany element nie ma atrybutu ID')

  signature = xmlsec.template.create(root, xmlsec.Transform.EXCL_C14N,
                                     xmlsec.Transform.ECDSA_SHA256, ns='ds')
  issuer.addnext(signature)
  xmlsec.tree.add_ids(target, ['ID'])
  ref = xmlsec.template.add_reference(signature, xmlsec.Transform.SHA256, uri=f'#{target_id}')
  xmlsec.template.add_transform(ref, xmlsec.Transform.ENVELOPED)
  transform = xmlsec.template.add_transform(ref, xmlsec.Transform.EXCL_C14N)
  inclusive = etree.SubElement(transform, '{http://www.w3.org/2001/10/xml-exc-c14n#}InclusiveNamespaces',
                               nsmap={None: 'http://www.w3.org/2001/10/xml-exc-c14n#'})
  inclusive.set('PrefixList', prefix_list)
  xmlsec.template.add_x509_data(xmlsec.template.ensure_key_info(signature))

  key = xmlsec.Key.from_memory(key_pem, xmlsec.KeyFormat.PEM, None)
  key.load_cert_from_memory(cert_pem, xmlsec.KeyFormat.PEM)
  ctx = xmlsec.SignatureContext()
  ctx.key = key
  ctx.sign(signature)
  # WK nie przyjmuje SignatureValue łamanego na wiersze (xmlsec łamie co 64 znaki)
  value = signature.find('ds:SignatureValue', NS)
  value.text = ''.join(value.text.split())
  return etree.tostring(root).decode('utf-8')


def split_pem_certificates(pem: bytes) -> list[bytes]:
  marker = b'-----END CERTIFICATE-----'
  return [part.strip() + b'\n' + marker + b'\n'
          for part in pem.split(marker) if b'-----BEGIN CERTIFICATE-----' in part]


def der_b64_to_pem(b64: str) -> bytes:
  body = ''.join(b64.split())
  lines = '\n'.join(body[i:i + 64] for i in range(0, len(body), 64))
  return f'-----BEGIN CERTIFICATE-----\n{lines}\n-----END CERTIFICATE-----\n'.encode()


def signing_certificates_from_metadata(metadata: bytes) -> list[bytes]:
  root = parse_xml(metadata)
  nodes = root.xpath('//md:IDPSSODescriptor/md:KeyDescriptor[not(@use) or @use="signing"]'
                     '//ds:X509Certificate', namespaces=NS)
  return [der_b64_to_pem(node.text) for node in nodes if node.text]


def _verify_signature(signature: etree._Element, certs: list[bytes]) -> bool:
  for cert in certs:
    ctx = xmlsec.SignatureContext()
    ctx.key = xmlsec.Key.from_memory(cert, xmlsec.KeyFormat.CERT_PEM, None)
    try:
      ctx.verify(signature)
      return True
    except xmlsec.Error:
      continue
  return False


def _status(element: etree._Element) -> str:
  codes = element.xpath('saml2p:Status/saml2p:StatusCode', namespaces=NS)
  if not codes:
    return ''
  nested = codes[0].xpath('saml2p:StatusCode/@Value', namespaces=NS)
  top = codes[0].get('Value', '')
  return top if top == STATUS_SUCCESS else (nested[0] if nested else top)


def verified_response(root: etree._Element, idp_certs: list[bytes]) -> etree._Element:
  """Sprawdza podpisy ArtifactResponse i zwraca podpisany saml2p:Response."""
  if not idp_certs:
    raise WkError('konfiguracja', 'brak certyfikatów WK')
  signatures = root.xpath('//ds:Signature', namespaces=NS)
  if not signatures:
    raise WkError('brak_podpisu')
  ids = root.xpath('//@ID')
  if len(ids) != len(set(ids)):
    raise WkError('niepoprawny_podpis', 'powtórzone ID')
  xmlsec.tree.add_ids(root, ['ID'])
  signed_ids = set()
  for signature in signatures:
    if not _verify_signature(signature, idp_certs):
      raise WkError('niepoprawny_podpis')
    for uri in signature.xpath('ds:SignedInfo/ds:Reference/@URI', namespaces=NS):
      if uri.startswith('#'):
        signed_ids.add(uri[1:])

  artifact_responses = root.xpath('//saml2p:ArtifactResponse', namespaces=NS)
  if len(artifact_responses) != 1:
    raise WkError('niepoprawna_odpowiedz', 'ArtifactResponse')
  artifact_response = artifact_responses[0]
  responses = artifact_response.xpath('saml2p:Response', namespaces=NS)
  if len(responses) != 1:
    raise WkError('niepoprawna_odpowiedz', 'Response')
  response = responses[0]
  if not ({artifact_response.get('ID'), response.get('ID')} & signed_ids):
    raise WkError('niepoprawny_podpis', 'odpowiedź niepodpisana')
  if _status(artifact_response) != STATUS_SUCCESS:
    raise WkError('blad_wk', _status(artifact_response))
  status = _status(response)
  if status != STATUS_SUCCESS:
    raise WkError('anulowano' if status.endswith(('AuthnFailed', 'RequestDenied')) else 'blad_wk', status)
  return response


def _b64(node: etree._Element | None) -> bytes:
  if node is None or not node.text:
    raise WkError('niepoprawna_asercja', 'brak danych szyfrowania')
  return base64.b64decode(''.join(node.text.split()))


def decrypt_assertion(encrypted_assertion: etree._Element,
                      private_key: ec.EllipticCurvePrivateKey) -> etree._Element:
  data = encrypted_assertion.find('xenc:EncryptedData', NS)
  if data is None:
    raise WkError('niepoprawna_asercja', 'EncryptedData')
  method = data.find('xenc:EncryptionMethod', NS)
  if method is None or method.get('Algorithm') not in DATA_GCM:
    raise WkError('niepoprawna_asercja', 'algorytm danych')
  encrypted_keys = data.xpath('.//xenc:EncryptedKey', namespaces=NS) or \
    encrypted_assertion.xpath('xenc:EncryptedKey', namespaces=NS)
  if not encrypted_keys:
    raise WkError('niepoprawna_asercja', 'EncryptedKey')
  encrypted_key = encrypted_keys[0]
  wrap = encrypted_key.find('xenc:EncryptionMethod', NS)
  length = KEY_WRAP_LENGTH.get(wrap.get('Algorithm') if wrap is not None else '')
  # AgreementMethod w przestrzeni xenc (XML Encryption 1.1), dopuszczalnie xenc11
  agreement = encrypted_key.find('.//xenc:AgreementMethod', NS)
  if agreement is None:
    agreement = encrypted_key.find('.//xenc11:AgreementMethod', NS)
  if length is None or agreement is None or agreement.get('Algorithm') != ECDH_ES:
    raise WkError('niepoprawna_asercja', 'algorytm klucza')
  kdf = agreement.find('.//xenc11:ConcatKDFParams', NS)
  if kdf is None:
    raise WkError('niepoprawna_asercja', 'ConcatKDFParams')
  try:
    other_info = b''.join(bytes.fromhex(kdf.get(name, ''))
                          for name in ('AlgorithmID', 'PartyUInfo', 'PartyVInfo'))
    peer = ec.EllipticCurvePublicKey.from_encoded_point(
      private_key.curve, _b64(agreement.find('.//dsig11:PublicKey', NS)))
    shared = private_key.exchange(ec.ECDH(), peer)
    kek = ConcatKDFHash(algorithm=hashes.SHA256(), length=length, otherinfo=other_info).derive(shared)
    cek = aes_key_unwrap(kek, _b64(encrypted_key.find('xenc:CipherData/xenc:CipherValue', NS)))
    payload = _b64(data.find('xenc:CipherData/xenc:CipherValue', NS))
    plain = AESGCM(cek).decrypt(payload[:12], payload[12:], None)
  except (ValueError, InvalidUnwrap) as exc:
    raise WkError('niepoprawna_asercja', 'deszyfrowanie') from exc
  except Exception as exc:  # cryptography.exceptions.InvalidTag
    raise WkError('niepoprawna_asercja', type(exc).__name__) from exc
  assertion = parse_xml(plain)
  if etree.QName(assertion).localname != 'Assertion':
    raise WkError('niepoprawna_asercja', 'Assertion')
  return assertion


def _time(value: str | None) -> datetime | None:
  if not value:
    return None
  try:
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
  except ValueError as exc:
    raise WkError('niepoprawna_asercja', 'czas') from exc
  return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def identity_from_response(response: etree._Element, private_key: ec.EllipticCurvePrivateKey, *,
                           expected_request_id: str, acs_url: str, issuer: str,
                           now: datetime | None = None) -> WkIdentity:
  now = now or datetime.now(timezone.utc)
  if response.get('InResponseTo') not in (None, expected_request_id):
    raise WkError('niepoprawna_odpowiedz', 'InResponseTo')
  encrypted = response.xpath('saml2:EncryptedAssertion', namespaces=NS)
  if len(encrypted) != 1 or response.xpath('saml2:Assertion', namespaces=NS):
    raise WkError('niepoprawna_asercja', 'liczba asercji')
  assertion = decrypt_assertion(encrypted[0], private_key)

  confirmations = assertion.xpath('saml2:Subject/saml2:SubjectConfirmation/saml2:SubjectConfirmationData',
                                  namespaces=NS)
  if not confirmations or confirmations[0].get('InResponseTo') != expected_request_id:
    raise WkError('niepoprawna_asercja', 'InResponseTo')
  confirmation = confirmations[0]
  if confirmation.get('Recipient') not in (None, acs_url):
    raise WkError('niepoprawna_asercja', 'Recipient')
  not_after = _time(confirmation.get('NotOnOrAfter'))
  if not_after and now >= not_after + CLOCK_SKEW:
    raise WkError('niepoprawna_asercja', 'wygasła')
  conditions = assertion.find('saml2:Conditions', NS)
  if conditions is not None:
    not_before = _time(conditions.get('NotBefore'))
    not_after = _time(conditions.get('NotOnOrAfter'))
    if (not_before and now + CLOCK_SKEW < not_before) or (not_after and now >= not_after + CLOCK_SKEW):
      raise WkError('niepoprawna_asercja', 'Conditions')
    audiences = conditions.xpath('saml2:AudienceRestriction/saml2:Audience/text()', namespaces=NS)
    if audiences and issuer not in [a.strip() for a in audiences]:
      raise WkError('niepoprawna_asercja', 'Audience')

  attributes = {}
  for attribute in assertion.xpath('saml2:AttributeStatement/saml2:Attribute', namespaces=NS):
    values = attribute.xpath('saml2:AttributeValue/text()', namespaces=NS)
    attributes[attribute.get('Name', '')] = values[0].strip() if values else ''
  person = attributes.get(ATTR + 'PersonIdentifier', '')
  if not person:
    raise WkError('niepoprawna_asercja', 'PersonIdentifier')
  name_id = assertion.xpath('saml2:Subject/saml2:NameID/text()', namespaces=NS)
  session = assertion.xpath('saml2:AuthnStatement/@SessionIndex', namespaces=NS)
  return WkIdentity(
    first_name=attributes.get(ATTR + 'CurrentGivenName', ''),
    last_name=attributes.get(ATTR + 'CurrentFamilyName', ''),
    birth_date=attributes.get(ATTR + 'DateOfBirth', ''),
    person_identifier=person,
    name_id=name_id[0] if name_id else None,
    session_index=session[0] if session else None)
