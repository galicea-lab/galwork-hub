# tests/test_edr_ade_search.py
# Wyszukiwarka adresów do doręczeń (connector/edoreczenia/ade_search.py)
# i endpointy /v1/wf/edr/ade* (wf/routers/edr_paperless_api.py). EDR mockowany.
from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock, patch

from tests.base import ApiTestCase

from connector.edoreczenia import ade_search
from wf.schemas.wf_schemas import CurrentUser

ROUTER_EDR = 'wf.routers.edr_paperless_api.EdrConnector'

INSTITUTION = {
  'index': 1, 'name': 'Urząd Miasta', 'legalForm': 'JST',
  'officialIds': [{'referenceRegistry': 'nip', 'id': '1234567890'},
                  {'referenceRegistry': 'regon', 'id': '123456789'}],
  'address': [
    {'addressType': ['headquarters'], 'countryCode': 'PL', 'city': 'Jarosław',
     'buildingNumber': '2', 'street': 'Rynek', 'flatNumber': '', 'postalCode': '37-500',
     'postalOffice': 'Jarosław'},
    {'addressType': ['correspondence'], 'countryCode': 'PL', 'city': 'Rzeszów',
     'buildingNumber': '1', 'street': 'Długa', 'flatNumber': '3', 'postalCode': '35-001',
     'postalOffice': 'Rzeszów'},
  ],
}


def _edrc(method: str, payload: dict) -> MagicMock:
  edrc = MagicMock()
  getattr(edrc.edr, method).return_value = MagicMock(data=json.dumps(payload))
  return edrc


def _bae(data: list) -> dict:
  return {'baeSearchResponses': [{'recipientEda': {'recipientEda': 'AE:PL-1'},
                                  'baeSearchData': data}]}


class AdeSearchParsingTest(unittest.TestCase):

  def test_institution_by_id_maps_ids_and_addresses(self):
    result = ade_search.find_institution_by_id(
      _edrc('SzukajInstytucji', _bae([INSTITUTION])), nip='1234567890')
    self.assertEqual(result['ade'], 'AE:PL-1')
    self.assertEqual((result['nip'], result['regon'], result['krs']), ('1234567890', '123456789', ''))
    self.assertEqual(result['city'], 'Jarosław')
    self.assertEqual(result['correspondence']['city'], 'Rzeszów')

  def test_institution_requires_identifier(self):
    with self.assertRaises(ValueError):
      ade_search.find_institution_by_id(MagicMock())

  def test_empty_response_is_not_found(self):
    with self.assertRaises(ade_search.AdeNotFound):
      ade_search.find_institution_by_address(
        _edrc('SzukajInstytucji', {'baeSearchResponses': []}), name='Nikt')

  def test_find_by_ade(self):
    payload = {'edaConfirmationResponses': [
      {'recipientEda': {'recipientEda': 'AE:PL-1'}, 'baeSearchData': INSTITUTION}]}
    result = ade_search.find_by_ade(_edrc('SzukajEDA', payload), 'AE:PL-1')
    self.assertEqual(result['name'], 'Urząd Miasta')

  def test_find_person_reads_top_level_data(self):
    payload = {'baeSearchResponses': [{'recipientEda': {'recipientEda': 'AE:PL-2'}}],
               'baeSearchData': [{'name': 'Jan', 'surname': 'Kowalski',
                                  'address': {'ade': 'AE:PL-2', 'isMainAddress': True}}]}
    result = ade_search.find_person(_edrc('SzukajOsoby', payload), 'Jan', 'Kowalski')
    self.assertEqual(result[0]['surname'], 'Kowalski')
    self.assertEqual(result[0]['ade'], 'AE:PL-2')


class AdeSearchApiTest(ApiTestCase):

  def prepare(self) -> None:
    user = self.create_user()
    self.current_user = CurrentUser(id=user.id, sub='sub-1', username='jurek',
                                    email='jurek@example.com', display_name='jurek',
                                    groups=frozenset({'kancelaria'}))

  def test_institution_endpoint(self):
    with patch(ROUTER_EDR, return_value=_edrc('SzukajInstytucji', _bae([INSTITUTION]))):
      result = self.client.get('/v1/wf/edr/ade/inst', params={'nip': '1234567890'})
    self.assertEqual(result.status_code, 200, result.text)
    self.assertEqual(result.json()['ade'], 'AE:PL-1')

  def test_not_found_is_404_and_missing_params_422(self):
    with patch(ROUTER_EDR, return_value=_edrc('SzukajInstytucji', {'baeSearchResponses': []})):
      self.assertEqual(self.client.get('/v1/wf/edr/ade/insta', params={'name': 'x'}).status_code, 404)
      self.assertEqual(self.client.get('/v1/wf/edr/ade/inst').status_code, 422)

  def test_edr_failure_is_502(self):
    edrc = MagicMock()
    edrc.edr.SzukajEDA.side_effect = RuntimeError('timeout')
    with patch(ROUTER_EDR, return_value=edrc):
      self.assertEqual(self.client.get('/v1/wf/edr/ade', params={'ade': 'AE:PL-1'}).status_code, 502)

  def test_requires_token(self):
    for sub_app in self.sub_apps:
      sub_app.dependency_overrides.clear()
    self.assertEqual(self.client.get('/v1/wf/edr/ade', params={'ade': 'AE:PL-1'}).status_code, 401)
