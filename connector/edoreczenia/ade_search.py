# coding: utf-8
# Wyszukiwanie w Bazie Adresów Elektronicznych (BAE),
# przeniesione z dawnego wf/routers/edr_api.py (endpointy /ade*). Parsowanie
# odpowiedzi jest tu, w jednym miejscu; router wf/routers/edr_paperless_api.py
# tylko wywołuje te funkcje.
"""Wyszukiwanie adresów do doręczeń elektronicznych (ADE).

Każda funkcja przyjmuje gotowy `EdrConnector` i zwraca dane w postaci
słowników (jak dotychczasowe endpointy `/ade*`). Brak wyniku -> `AdeNotFound`,
niezrozumiała odpowiedź usługi -> `ConnectorError`.
"""
from __future__ import annotations

import json
from typing import Any

from connector.base import ConnectorError

_ADDRESS_FIELDS = ('countryCode', 'city', 'buildingNumber', 'street', 'flatNumber',
                   'postalCode', 'postalOffice')
_REGISTRIES = ('krs', 'nip', 'regon', 'pesel')


class AdeNotFound(LookupError):
  """Usługa nie zwróciła adresata dla podanych kryteriów."""


def _json(response) -> dict[str, Any]:
  try:
    return json.loads(response.data)
  except (TypeError, ValueError, AttributeError) as exc:
    raise ConnectorError(f'EDR: niepoprawna odpowiedź wyszukiwarki BAE: {exc}') from exc


def _empty_address() -> dict[str, str]:
  return {field: '' for field in _ADDRESS_FIELDS}


def _address(raw: dict[str, Any]) -> dict[str, str]:
  return {field: raw.get(field) or '' for field in _ADDRESS_FIELDS}


def _recipient_ade(entry: dict[str, Any]) -> str:
  recipient = entry.get('recipientEda') or {}
  if isinstance(recipient, dict):
    return recipient.get('recipientEda') or ''
  return str(recipient)


def entity(ade: str, data: dict[str, Any]) -> dict[str, Any]:
  """Podmiot z `baeSearchData`: identyfikatory urzędowe, adres siedziby
  (pola płaskie) i adres do korespondencji (`correspondence`)."""
  ids = {registry: '' for registry in _REGISTRIES}
  for official in data.get('officialIds') or []:
    registry = official.get('referenceRegistry')
    if registry in ids:
      ids[registry] = official.get('id') or ''
  result: dict[str, Any] = {
    'ade': ade, 'name': data.get('name') or '', **ids,
    'legalForm': data.get('legalForm') or '',
    **_empty_address(), 'correspondence': _empty_address(),
  }
  for raw in data.get('address') or []:
    kinds = ','.join(raw.get('addressType') or [])
    if 'correspondence' in kinds:
      result['correspondence'] = _address(raw)
    if 'headquarters' in kinds:
      result.update(_address(raw))
  return result


def _first_response(payload: dict[str, Any]) -> dict[str, Any]:
  responses = payload.get('baeSearchResponses') or []
  if not responses or not _recipient_ade(responses[0]):
    raise AdeNotFound('brak adresata w Bazie Adresów Elektronicznych')
  return responses[0]


def _main_entity(response: dict[str, Any]) -> dict[str, Any]:
  ade = _recipient_ade(response)
  entries = response.get('baeSearchData') or []
  main = next((e for e in entries if e.get('index') == 1), entries[0] if entries else {})
  return entity(ade, main)


def find_by_ade(edrc, ade: str) -> dict[str, Any]:
  """Potwierdzenie adresu (`eda-confirmation`) - dane podmiotu o danym ADE."""
  payload = _json(edrc.edr.SzukajEDA(ade))
  responses = payload.get('edaConfirmationResponses') or []
  if not responses or not _recipient_ade(responses[0]):
    raise AdeNotFound(f'adres {ade} nie istnieje w Bazie Adresów Elektronicznych')
  return entity(_recipient_ade(responses[0]), responses[0].get('baeSearchData') or {})


def find_person(edrc, firstname: str, surname: str, pesel: str = '', city: str = '',
                building_number: str = '') -> list[dict[str, Any]]:
  """Osoby fizyczne - może być kilka trafień."""
  payload = _json(edrc.edr.SzukajOsoby(firstname, surname, pesel, city, building_number, {}))
  response = _first_response(payload)
  ade = _recipient_ade(response)
  # Odpowiedź bywa z danymi wewnątrz baeSearchResponses[0] albo na poziomie głównym.
  entries = response.get('baeSearchData') or payload.get('baeSearchData') or []
  result = []
  for data in entries:
    address = data.get('address') or {}
    result.append({
      'ade': (address.get('ade') if isinstance(address, dict) else None) or ade,
      'name': data.get('name') or '',
      'surname': data.get('surname') or '',
      'professionalTitle': data.get('professionalTitle') or '',
      'legalForm': data.get('legalForm') or '',
      'designatedOperator': data.get('designatedOperator') or '',
      'address': {
        'ade': address.get('ade') if isinstance(address, dict) else '',
        'validFrom': address.get('validFrom') if isinstance(address, dict) else None,
        'isMainAddress': address.get('isMainAddress') if isinstance(address, dict) else None,
      },
      'correspondenceAddress': data.get('correspondenceAddress'),
    })
  return result


def find_institution_by_id(edrc, *, pesel: str = '', nip: str = '', regon: str = '',
                           krs: str = '') -> dict[str, Any]:
  if not any((pesel, nip, regon, krs)):
    raise ValueError('podaj co najmniej jeden identyfikator: pesel, nip, regon albo krs')
  payload = _json(edrc.edr.SzukajInstytucji(pesel, nip, regon, krs, '', {}))
  return _main_entity(_first_response(payload))


def find_institution_by_address(edrc, *, name: str, city: str = '', building_number: str = '',
                                street: str = '', flat_number: str = '',
                                postal_code: str = '') -> dict[str, Any]:
  if not name:
    raise ValueError('podaj nazwę instytucji')
  payload = _json(edrc.edr.SzukajInstytucji('', '', '', '', name, {
    'countryCode': 'PL', 'city': city, 'buildingNumber': building_number,
    'street': street, 'flatNumber': flat_number, 'postalCode': postal_code,
  }))
  return _main_entity(_first_response(payload))
