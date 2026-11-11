# tests/test_edu_sealing.py
# Pieczęć elektroniczna odpowiedzi w procesie edu-pismo
# (edu/services/sealing.py). Usługę galwork-seal zastępuje FakeSealValkey:
# przyjmuje zlecenie z kolejki tak jak usługa i podpisuje atrybuty CMS
# kluczem testowym. endesive składa prawdziwy PAdES, który weryfikujemy.
from __future__ import annotations

import base64
import datetime
import io
import json
import os
from unittest.mock import patch

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID

from connector.base import ConnectorError
from connector.seal.connector import RemoteSeal, cert_key, reply_key
from tests.test_edu_pismo import KOR, EduPismoBase, make_case

SEAL = 'edu.services.sealing'
CASE = 'EDU-COR-2026-00001'
SIGNED_URL = f'/private/files/{CASE}-pieczec.pdf'


def _cert(subject_key, subject: str, issuer_key, issuer: str, ca: bool) -> x509.Certificate:
  now = datetime.datetime.now(datetime.UTC)
  name = lambda cn: x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])  # noqa: E731
  builder = (x509.CertificateBuilder()
             .subject_name(name(subject)).issuer_name(name(issuer))
             .public_key(subject_key.public_key()).serial_number(x509.random_serial_number())
             .not_valid_before(now - datetime.timedelta(days=1))
             .not_valid_after(now + datetime.timedelta(days=30))
             .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True))
  if not ca:
    builder = builder.add_extension(x509.KeyUsage(
      digital_signature=True, content_commitment=True, key_encipherment=False,
      data_encipherment=False, key_agreement=False, key_cert_sign=False, crl_sign=False,
      encipher_only=False, decipher_only=False), critical=True)
  return builder.sign(issuer_key, hashes.SHA256())


def make_pki() -> dict:
  ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
  seal_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
  ca = _cert(ca_key, 'CA testowe', ca_key, 'CA testowe', ca=True)
  seal = _cert(seal_key, 'Pieczęć testowa', ca_key, 'CA testowe', ca=False)
  pem = lambda c: c.public_bytes(serialization.Encoding.PEM)  # noqa: E731
  return {'seal_key': seal_key, 'chain_pem': pem(seal) + pem(ca), 'ca_pem': pem(ca)}


def make_pdf() -> bytes:
  from pypdf import PdfWriter

  writer = PdfWriter()
  writer.add_blank_page(595, 842)
  buf = io.BytesIO()
  writer.write(buf)
  return buf.getvalue()


class FakeSealValkey:
  """Valkey + usługa pieczęci: GET certyfikatu, XADD zlecenia, BLPOP odpowiedzi."""

  def __init__(self, pki: dict, *, error: tuple[str, str] | None = None):
    self.pki = pki
    self.error = error
    self.store = {cert_key('pieczec'): pki['chain_pem']}
    self.replies: dict[str, str] = {}
    self.requests: list[dict] = []

  def get(self, key):
    return self.store.get(key)

  def xadd(self, stream, fields, maxlen=None, approximate=True):
    assert stream == 'seal:requests'
    self.requests.append(dict(fields))
    body = {'v': '1', 'id': fields['id']}
    if self.error:
      body.update(ok=False, error=self.error[0], message=self.error[1])
    else:
      data = base64.b64decode(fields['data'])
      signature = self.pki['seal_key'].sign(data, padding.PKCS1v15(), hashes.SHA256())
      body.update(ok=True, signature=base64.b64encode(signature).decode())
    self.replies[reply_key(fields['id'])] = json.dumps(body)

  def blpop(self, keys, timeout=0):
    value = self.replies.pop(keys[0], None)
    return (keys[0], value) if value else None


class FakeSealConnector:
  keyid = 'pieczec'

  def __init__(self, client: FakeSealValkey):
    self.client = client

  def remote(self, *, reference='', requested_by=''):
    return RemoteSeal(self.client, self.keyid, source='edu-pismo', reference=reference,
                      requested_by=requested_by, timeout=5)


class EduSealingTest(EduPismoBase):

  def prepare(self) -> None:
    super().prepare()
    self.pki = make_pki()
    self.pdf = make_pdf()
    self.valkey = FakeSealValkey(self.pki)
    env = patch.dict(os.environ, {'SEAL_ENABLED': 'true', 'SEAL_ATTEMPTS': '2',
                                  'SEAL_RETRY_DELAY': '0'})
    env.start()
    self.patches.append(env)
    for target, kwargs in [
      (f'{SEAL}.db_get_correspondence', {'side_effect': lambda _name: self.case}),
      (f'{SEAL}.get_seal_connector', {'side_effect': lambda: FakeSealConnector(self.valkey)}),
      # zadanie w tle uruchamiamy od razu, na testowej bazie
      (f'{SEAL}._submit', {'side_effect': lambda fn, *args: fn(*args)}),
      (f'{SEAL}._session_factory', {'side_effect': lambda: self.session_factory}),
    ]:
      p = patch(target, **kwargs)
      self.patches.append(p)
      p.start()
    self.get_pdf = self.start_patch(f'{SEAL}.db_get_correspondence_pdf')
    self.get_pdf.return_value = self.pdf
    self.upload = self.start_patch(f'{SEAL}.db_upload_correspondence_file')
    self.upload.return_value = {'file_url': SIGNED_URL}
    self.set_signed = self.start_patch(f'{SEAL}.db_set_correspondence_signed')

  def decide(self, chosen_path: str, answer: str = 'Sesja przedłużona.') -> None:
    self.assertEqual(self.submit()['code'], 0)
    self.as_staff()
    done = self.complete_my_task('Rozpatrzenie', {'odpowiedz': answer, 'chosen_path': chosen_path})
    self.assertEqual(done.status_code, 200, done.text)

  def events(self, kind: str) -> list:
    from wf.models import ProcessEvent
    self.db.expire_all()
    return self.db.query(ProcessEvent).filter(ProcessEvent.event_type == kind).all()

  def signed_pdf(self) -> bytes:
    self.upload.assert_called_once()
    args, kwargs = self.upload.call_args
    self.assertEqual(args[0], CASE)
    self.assertEqual(kwargs['fieldname'], 'signed_document')
    self.assertEqual(kwargs['content_type'], 'application/pdf')
    return args[2]

  # --- pieczętowanie po decyzji ---------------------------------------------

  def test_completed_answer_is_sealed_with_pades(self):
    from endesive import pdf

    self.decide('__default__')
    signed = self.signed_pdf()
    self.assertTrue(signed.startswith(self.pdf))
    self.assertIn(b'/ETSI.CAdES.detached', signed)
    result, _rest = pdf.verify(signed, [self.pki['ca_pem']])
    self.assertTrue(result.signatureok and result.hashok and result.certok)

    request, = self.valkey.requests
    self.assertEqual(request['source'], 'edu-pismo')
    self.assertEqual(request['reference'], CASE)
    self.assertEqual(request['keyid'], 'pieczec')
    self.set_signed.assert_called_once_with(CASE, 'Completed')

    completed, = self.events('signature_completed')
    self.assertEqual(completed.payload['file_url'], SIGNED_URL)
    self.assertEqual(completed.payload['mode'], 'advanced_seal')
    self.assertEqual(len(completed.payload['document_sha256']), 64)
    self.assertEqual(len(self.events('signature_requested')), 1)
    self.assertEqual(self.events('instance_error'), [])

  def test_rejection_is_sealed_and_keeps_status(self):
    self.decide('Flow_Odmowa', 'Brak podstaw.')
    self.signed_pdf()
    self.set_signed.assert_called_once_with(CASE, 'Rejected')

  def test_request_for_supplement_is_not_sealed(self):
    self.decide('Flow_Uzupelnienie', 'Proszę o zaświadczenie.')
    self.get_pdf.assert_not_called()
    self.upload.assert_not_called()

  def test_disabled_seal_does_nothing(self):
    with patch.dict(os.environ, {'SEAL_ENABLED': 'false'}):
      self.decide('__default__')
    self.get_pdf.assert_not_called()
    self.assertEqual(self.events('signature_requested'), [])

  def test_other_process_models_are_not_sealed(self):
    with patch.dict(os.environ, {'SEAL_MODELS': 'edu-inny'}):
      self.decide('__default__')
    self.get_pdf.assert_not_called()

  # --- błędy ----------------------------------------------------------------

  def test_refusal_is_not_retried_and_goes_to_instance_log(self):
    self.valkey.error = ('forbidden', "źródło 'edu-pismo' nie może używać klucza 'pieczec'")
    self.decide('__default__')
    self.assertEqual(len(self.valkey.requests), 1)
    self.upload.assert_not_called()
    self.set_signed.assert_not_called()
    error, = self.events('instance_error')
    self.assertEqual(error.payload['source'], 'edu.sealing')
    self.assertIn('forbidden', error.payload['error'])
    # decyzja dziekanatu i tak zapisana, proces zakończony
    self.record_response.assert_called_once_with(CASE, 'Sesja przedłużona.', 'Completed')
    from wf.schemas.wf_schemas import ProcessInstanceStatus
    self.assertEqual(self.instance().status, ProcessInstanceStatus.COMPLETED)

  def test_transient_error_is_retried(self):
    self.get_pdf.side_effect = [ConnectorError('Frappe niedostępne'), self.pdf]
    self.decide('__default__')
    self.assertEqual(self.get_pdf.call_count, 2)
    self.signed_pdf()
    self.assertEqual(self.events('instance_error'), [])

  def test_retries_exhausted_go_to_instance_log(self):
    self.get_pdf.side_effect = ConnectorError('Frappe niedostępne')
    self.decide('__default__')
    self.assertEqual(self.get_pdf.call_count, 2)
    error, = self.events('instance_error')
    self.assertEqual(error.payload['attempts'], 2)

  def test_already_sealed_case_is_not_sealed_again(self):
    from edu.services.sealing import seal_case

    self.decide('__default__')
    self.upload.reset_mock()
    self.case = make_case(status='Completed', signature_status='Signed', signed_document=SIGNED_URL)
    self.assertEqual(seal_case(CASE, self.instance().id), SIGNED_URL)
    self.upload.assert_not_called()
    self.assertEqual(len(self.valkey.requests), 1)

  # --- endpointy ------------------------------------------------------------

  def test_staff_can_seal_again_after_failure(self):
    self.valkey.error = ('backend_error', 'token niedostępny')
    self.decide('__default__')
    self.upload.assert_not_called()

    self.valkey.error = None
    self.case = make_case(status='Completed')
    result = self.client.post('/v1/edu/jform/sprawa/seal', params={'id': CASE}).json()
    self.assertEqual(result, {'code': 0, 'message': 'OK', 'signed_document': SIGNED_URL})
    self.signed_pdf()

  def test_student_cannot_trigger_seal(self):
    self.as_student()
    response = self.client.post('/v1/edu/jform/sprawa/seal', params={'id': CASE})
    self.assertEqual(response.status_code, 403, response.text)

  def test_seal_requires_answer(self):
    self.as_staff()
    result = self.client.post('/v1/edu/jform/sprawa/seal', params={'id': CASE}).json()
    self.assertEqual(result['code'], -4)

  def test_student_downloads_sealed_answer(self):
    self.case = make_case(status='Completed', signature_status='Signed', signed_document=SIGNED_URL)
    with patch(f'{KOR}.db_get_file_content', return_value=b'%PDF-pieczec') as content:
      response = self.client.get('/v1/edu/jform/sprawa/signed', params={'id': CASE})
    self.assertEqual(response.status_code, 200, response.text)
    self.assertEqual(response.content, b'%PDF-pieczec')
    self.assertEqual(response.headers['content-type'], 'application/pdf')
    content.assert_called_once_with(SIGNED_URL)

  def test_download_without_seal_is_404(self):
    response = self.client.get('/v1/edu/jform/sprawa/signed', params={'id': CASE})
    self.assertEqual(response.status_code, 404, response.text)
