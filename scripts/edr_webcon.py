#!/usr/bin/env python
# coding: utf-8
"""
e-Doręczenia
"""
import argparse
import json
import os
import sys
import inspect


currentdir = os.path.dirname(os.path.abspath(inspect.getfile(inspect.currentframe())))
parentdir = currentdir
parentdir = os.path.dirname(parentdir)
sys.path.insert(0, parentdir)

#from connector.edoreczenia.ua_model import Type
from connector.edoreczenia.edr_webcon import inbox_to_webcon, send_from_webcon, fetch_evidences, EdrConnector, \
  fetch_evidences_att


def test_search(test=False):
  edrc=EdrConnector(test)
  result=edrc.edr.SzukajInstytucji(pesel='', nip='', regon='', krs='0000462569', name='', address={})
  if result.data:
    print(json.loads(result.data))
  #{'baeSearchResponses': [{'recipientEda': {'recipientEda': 'AE:PL-26181-88901-VVBTJ-26', 'isMainEda': None, 'assignmentDegree': '3',
  # 'edaStatus': 'ACTIVE', 'typeWarning': None, 'warning': None, 'designatedOperator': True, 'dateOfEnteringToBAE': None,
  # 'dateOfRemovalFromBAE': None, 'additionalServiceOW': {'activationDate': None, 'resignationDate': None}},
  # 'baeSearchData': [{'index': 1, 'name': None, 'surname': None, 'professionalTitle': None, 'legalForm':
  # 'FUNDACJA', 'isPublic': False, 'entityName': 'PODKARPACKA FUNDACJA WSPIERANIA INNOWACJI "GALICEA"',
  # 'officialIds': [{'id': '180978364', 'referenceRegistry': 'regon'}, {'id': '7922289194', 'referenceRegistry': 'nip'},
  # {'id': '0000462569', 'referenceRegistry': 'krs'}], 'nipStatus': 'ASSIGNED',
  # 'address': [{'addressType': ['correspondence'], 'countryCode': 'PL', 'street': 'OS. OSIEDLE NIEPODLEGŁOŚCI',
  # 'typeOfStreet': None, 'postalCode': '37-500', 'city': 'JAROSŁAW', 'buildingNumber': '2',
  # 'flatNumber': '16', 'postalOffice': 'JAROSŁAW', 'postOfficeBox': None, 'voivodeship': 'PODKARPACKIE',
  # 'district': 'JAROSŁAWSKI', 'community': 'JAROSŁAW (GMINA MIEJSKA)'}, {'addressType': ['headquarters'],
  # 'countryCode': 'PL', 'street': 'OS. OSIEDLE NIEPODLEGŁOŚCI', 'typeOfStreet': None, 'postalCode': '37-500',
  # 'city': 'JAROSŁAW', 'buildingNumber': '2', 'flatNumber': '16', 'postalOffice': 'JAROSŁAW', 'postOfficeBox': None,
  # 'voivodeship': 'PODKARPACKIE', 'district': 'JAROSŁAWSKI', 'community': 'JAROSŁAW (GMINA MIEJSKA)'}]}]}], 'totalResults': 1}

  result=edrc.edr.SzukajInstytucji(pesel='', nip='6762455185', regon='', krs='', name='', address={})
  if result.data:
    print(json.loads(result.data))
  result=edrc.edr.SzukajInstytucji(pesel='', nip='', regon='122557001', krs='', name='', address={})
  if result.data:
    print(json.loads(result.data))
  result=edrc.edr.SzukajInstytucji(pesel='', nip='', regon='', krs='', name='Example sp. z o.o.', address={
    "countryCode":"PL",
    "city":"Jarosław",
    "buildingNumber":"1",
    "street":"ul. ....",
    "flatNumber":"2",
    "postalCode":"00-500",})
  if result.data:
    print(json.loads(result.data))


if __name__ == "__main__":
  test=False
  parser = argparse.ArgumentParser(description='e-Doręczenia')
  parser.add_argument('cmd', help='operacja: send inbox')
  args = parser.parse_args()
  if args.cmd == 'inbox':
    inbox_to_webcon(test)
    exit(0)
  elif args.cmd == 'evidences':
    fetch_evidences(test)
#    fetch_evidences_att(1,'PPSA-E-44444444-3333-2222-1111-000000000000')
    exit(0)
  elif args.cmd == 'send':
    print('send')
    send_from_webcon(test)
    exit(0)
  elif args.cmd == 'test':
    test_search(test)
    exit(0)
  parser.print_help()

