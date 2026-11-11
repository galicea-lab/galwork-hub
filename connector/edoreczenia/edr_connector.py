# coding: utf-8

"""
Klient protokołu e-Doręczenia (`EdrConnector`).

 Potrzebują go dwa miejsca niezależnie od wybranego
backendu:

  * `connector/edoreczenia/ade_search.py` - wyszukiwanie w rejestrze
    e-Delivery (endpointy `/ade*` w `wf/routers/edr_paperless_api.py`).
  * `edr_paperless.py` - owija klient swoją logiką "co zrobić z odebraną/wysyłaną wiadomością" 
    (alternatywą może być np. Webcon ze swoją logiką).
"""
from __future__ import annotations

import json
import logging

from connector.base import BaseConnector, ConnectorError
from connector.edoreczenia.edr_token import post_auth_request, request_token
from connector.edoreczenia.edoreczenia import Edr
from unidecode import unidecode

log = logging.getLogger(__name__)

_edr_config = BaseConnector.load_ini_section('edr')

TEST_ADE = _edr_config.get('test_address', '')

#: nazwa systemu w tokenie RFC7523 (`iss`/`sub` = <ADE>.SYSTEM.<nazwa>)
SYSTEM_NAME = _edr_config.get('system_name', 'TEST123')

# Klucz prywatny trybu testowego czytany z pliku wskazanego
# w [edr] test_key (jak `key` w trybie produkcyjnym).

def unix_name(name):
    return unidecode(str(name).replace(" ", "_"))


def mime(fn):
    if fn[-4:] == '.jpg' or fn[-5:] == 'jpeg':
        return 'image/jpeg'
    elif fn[-4:] == '.png':
        return 'image/png'
    elif fn[-5:] == '.webp':
        return 'image/webp'
    elif fn[-4:] == '.csv':
        return 'text/csv'
    elif fn[-4:] == '.xml':
        return 'text/xml'
    elif fn[-4:] == '.pdf':
        return 'application/pdf'
    elif fn[-4:] == '.txt':
        return 'text/plain'
    elif fn[-4:] == '.doc' or fn[-5:] == '.docx':
        return 'application/msword'
    return 'application/x-binary'


class EdrConnector():

  def __init__(self, test=False, search=False):
    if test:
      try:
        self.addr = TEST_ADE
        with open(_edr_config.get('test_key', '')) as f:
          self.priv_key = f.read()
        qtoken = request_token(
          self.addr, _edr_config.get('test_system_name', SYSTEM_NAME), self.priv_key)
        (dtoken, code, hdr) = post_auth_request(qtoken, self.addr)
        self.token = dtoken['access_token']
      except Exception as e:
        log.warning('connector.edoreczenia: problem z tokenem (tryb testowy): %s', e)
        self.token = None
    else:
      try:
        self.addr = _edr_config.get('address')
        fnpriv = _edr_config.get('key')
        with open(fnpriv) as f:
          self.priv_key = f.read()
        qtoken = request_token(self.addr, SYSTEM_NAME, self.priv_key)
        (dtoken, code, hdr) = post_auth_request(qtoken, self.addr)
        self.token = dtoken['access_token']
      except Exception as e:
        log.warning('connector.edoreczenia: problem z tokenem: %s', e)
        self.token = None
    # Przy braku konfiguracji `self.addr` wyliczenie `cert_subject` kończyło się TypeError zamiast czytelnym błędem.
    if not self.addr:
      raise ConnectorError(
        'Brak konfiguracji EDR: ustaw `address` w sekcji [edr] pliku '
        'config/config.ini (albo zmienną EDR_ADDRESS).')
    self.cert_subject = self.addr + '.SYSTEM.' + SYSTEM_NAME
    self.edr = Edr(self.token, self.addr)

  def search(self, name, surname):
    res = self.edr.Szukaj(imie=name, nazwisko=surname)
    result = []
    jres = json.loads(res.data)
    try:
      for adres in jres['baeSearchData']:
        result.append({
          "name": adres["name"],
          "surname": adres["surname"],
          "professionalTitle": adres["professionalTitle"],
          "legalForm": adres["legalForm"],
          "designatedOperator": adres["designatedOperator"],
          "address": {"ade": adres["address"]["ade"],
                      "validFrom": adres["address"]["validFrom"],
                      "isMainAddress": adres["address"]["isMainAddress"]},
          "correspondenceAddress": adres["correspondenceAddress"]
        })
    except Exception as e:
      log.exception('connector.edoreczenia.search: %s', e)
    return result

  def search_ade(self, ade):
    res = self.edr.SzukajNadawcy(ade, self.cert_subject)
    result = []
    jres = json.loads(res.data)
    try:
      for adres in jres['baeSearchData']:
        result.append({
          "name": adres["name"],
          "surname": adres["surname"],
          "professionalTitle": adres["professionalTitle"],
          "legalForm": adres["legalForm"],
          "designatedOperator": adres["designatedOperator"],
          "address": {"ade": adres["address"]["ade"],
                      "validFrom": adres["address"]["validFrom"],
                      "isMainAddress": adres["address"]["isMainAddress"]},
          "correspondenceAddress": adres["correspondenceAddress"]
        })
    except Exception as e:
      log.exception('connector.edoreczenia.search: %s', e)
    return result

  def send(self, ade, subject, body, atts=[]):
    from connector.edoreczenia.ua_model import AttachmentSend, File, FileMetadata
    a = []
    order = 1
    for att in atts:
      filename = unix_name(att['filename'])
      if filename[-4:] == '.zip':
        contentType = 'application/zip'
      elif filename[-4:] == '.txt':
        contentType = 'text/plain'
      elif filename[-4:] == '.rtf':
        contentType = 'application/rtf'
      elif filename[-4:] == '.pdf':
        contentType = 'application/pdf'
      elif filename[-4:] == '.xps':
        contentType = 'application/oxps, application/vnd.ms-xpsdocument'
      elif filename[-4:] == '.odt':
        contentType = 'application/vnd.oasis.opendocument.text'
      elif filename[-4:] == '.ods':
        contentType = 'application/vnd.oasis.opendocument.spreadsheet'
      elif filename[-4:] == '.odp':
        contentType = 'application/vnd.oasis.opendocument.presentation'
      elif filename[-4:] == '.doc':
        contentType = 'application/msword'
      elif filename[-4:] == '.xls':
        contentType = 'application/vnd.ms-excel'
      elif filename[-4:] == '.ppt':
        contentType = 'application/vnd.ms-powerpoint'
      elif filename[-5:] == '.docx':
        contentType = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
      elif filename[-5:] == '.xlsx':
        contentType = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
      elif filename[-5:] == '.pptx':
        contentType = 'application/vnd.openxmlformats-officedocument.presentationml.presentation'
      else:
        contentType = att['contentType'] if 'contentType' in att else 'application/pdf'
      metadata = FileMetadata(fileId=att['uuid'],
                              filename=filename,
                              contentType=contentType,
                              description=att['description'])
      file = File(fileMetadata=metadata, file=att['file64'])
      a.append(AttachmentSend(AttachmentId=att['uuid'], order=order, file=file))
      order += 1
    try:
      ret = self.edr.WysylanieWiadomosci(ade, subject, body, a)
      msg = json.loads(ret.data)
      try:
        return msg['Messages'][0]
      except Exception:
        pass
      try:
        return {'MessageId': '',
                'AddresseeADE': '',
                'Status': 'error ' + msg['error'] + ' ' + msg["error_description"]}
      except Exception:
        return {'MessageId': '', 'AddresseeADE': '', 'Status': 'error ?'}
    except Exception as e:
      return {'MessageId': '', 'AddresseeADE': '', 'Status': 'error %s' % e}

  def inbox(self):
    result = []
    try:
      messages = self.edr.ListaWiadomosci(label='INBOX')
      for m in messages.messages:
        result.append(m.messageMetadata.messageId)
    except Exception as e:
      log.exception('connector.edoreczenia.search: %s', e)
    return result

  def sent(self):
    result = []
    try:
      messages = self.edr.ListaWiadomosci(label='SENT')
      for m in messages.messages:
        result.append(m.messageMetadata.messageId)
    except Exception as e:
      log.exception('connector.edoreczenia.search: %s', e)
    return result

  def message(self, messageId, format='fullExtended'):
    try:
      return self.edr.PobranieWiadomosciF(messageId, format=format)
    except Exception as e:
      log.exception('connector.edoreczenia.message: %s', e)
      return None
