# coding: utf-8
#   * `/ade*` (wyszukiwanie w rejestrze e-Delivery po PESEL/NIP/KRS) oraz
#     `/webcon_*`, `/evidences/webcon` (wywoluja akcje w Webcon, w tym
#     wyslanie wiadomosci eDR!) wymagaja tokenu Keycloak, a część akcji 
#     dodatkowo grupy administracyjnej (`wf.dependencies.require_process_admin`).
#   * endpointy `*Callback` celowo pozostaja bez autoryzacji: wywoluje je
#     zewnetrzna usluga e-Delivery, nie klient huba. Nie przetwarzaja jeszcze
#     zadnych danych (zwracaja `status: OK`), wiec na razie nie ma czego
#     chronic - przed wdrozeniem produkcyjnym trzeba dodac weryfikacje
#     podpisu/allowlistę IP.
import asyncio
import time

from fastapi import Request, Response, APIRouter, Depends, BackgroundTasks
from pydantic import BaseModel, Field
from typing import List, Optional, Dict

from connector.edoreczenia.edr_webcon import update_webcon, send1_from_webcon, EdrConnector, fetch_evidences_att
from wf.dependencies import get_current_wf_user, require_process_admin
from wf.schemas.wf_schemas import CurrentUser

import logging
import json

from connector.edoreczenia.edr_token import request_token, post_auth_request
from connector.edoreczenia.edoreczenia import Edr

log = logging.getLogger(__name__)

# dane rejestracyjne systemu i ścieżki certyfikatów w sekcji [edr] config/config.ini
from config.config import section as _config_section

_edr_config = _config_section('edr')


def _read_secret(path):
  """Zawartość pliku (klucz/certyfikat) z configu albo '' gdy brak."""
  if not path:
    return ''
  try:
    with open(path, encoding='utf-8') as f:
      return f.read()
  except OSError as exc:
    log.warning('edr: nie można odczytać %s: %s', path, exc)
    return ''


adres_edr = _edr_config.get('address', '')
aplikacja = _edr_config.get('application_name', 'GALWORK')
priv_key = _read_secret(_edr_config.get('key'))
cert_z_edr = _read_secret(_edr_config.get('cert'))

def get_access_token():
  # System tworzy token zgodny z https://datatracker.ietf.org/doc/html/rfc7523
  # i podpisuje go kluczem prywatnym certyfikatu.
  qtoken=request_token(adres_edr, aplikacja, priv_key)
  ### sprawdź:
  # https://jwt.io/
  # zob. też. https://github.com/ticarpi/jwt_tool
  try:
    (dtoken, code, hdr)=post_auth_request(qtoken, adres_edr)
    token=dtoken['access_token']
  except Exception as e:
    log.exception('Nie udało się pobrać tokenu EDR: %s', e)
    return None
  return token


def define_subscriptions(edr):
  #  bazowy adres webhooków z sekcji [edr] config/config.ini.
  base = (_edr_config.get('callback_base_url', '') or '').rstrip('/')
  messageCallback = f'{base}/messageCallback' if base else ''
  evidenceCallback = f'{base}/evidenceCallback' if base else ''
  inboxCallback = f'{base}/inboxCallback' if base else ''
  edr.DefiniujSubskrypcje(adres_edr, messageCallback,evidenceCallback,inboxCallback)

router_edr = APIRouter(
  prefix="",
  tags=['edr'],
  responses={404: {"description": "Not found"}},
)

class SuccessInfo(BaseModel):
  status: str = Field(None, description='status')


class NotificationInboxCallback(BaseModel):
  id: str = Field(None, description='id')
  threadId:str = Field(None, description='threadId')
  typestr :str = Field(None, description='typestr')

class MessageStatement(BaseModel):
  eDeliveryAddress: str = Field(None,description='')
  notificationType: str = Field(None,description='')
  electronicCount:int = Field(None,description='')
  items: List[NotificationInboxCallback] = []

@router_edr.post('/messageCallback', response_model=SuccessInfo)
async def messageCallback(
                    request: Request,\
                    body: MessageStatement, \
                    ) -> SuccessInfo:
  try:
    #logger.info('edr messageCallback: [%s]%s {%s} ' % (request,request.headers,body))
    return SuccessInfo(
        status = 'OK',
      )
  except Exception as e:
    #logger.info('edr error %s ' % e)
    return {
        "status" : 'internal error',
      }

@router_edr.post('/evidenceCallback', response_model=SuccessInfo)
def evidenceCallback(request: Request, \
                     body: MessageStatement, \
                    ) -> SuccessInfo:
  try:
    #logger.info('edr evidenceCallback: [%s]%s {%s} ' % (request, request.headers, body))
    return SuccessInfo(
        status = 'OK',
    )
  except Exception as e:
    #logger.info('edr error %s ' % e)
    return {
        "status" : 'internal error',
      }

@router_edr.post('/inboxCallback', response_model=SuccessInfo)
def inboxCallback(request: Request, \
                  body: MessageStatement, \
                    ) -> SuccessInfo:
  try:
    #logger.info('edr inboxCallback: [%s]%s {%s} ' % (request, request.headers, body))
    return SuccessInfo(
        status = 'OK',
    )
  except Exception as e:
    #logger.info('edr error %s ' % e)
    return {
        "status" : 'internal error',
      }


@router_edr.get('/messageCallback', response_model=SuccessInfo)
async def get_messageCallback(
                    request: Request,\
                    ) -> SuccessInfo:
  try:
    #l#ogger.info('edr messageCallback: [%s]%s ' % (request,request.headers))
    return SuccessInfo(
        status = 'OK',
      )
  except Exception as e:
    #logger.info('edr error %s ' % e)
    return {
        "status" : 'internal error',
      }

@router_edr.get('/evidenceCallback', response_model=SuccessInfo)
def get_evidenceCallback(request: Request, \
                    ) -> SuccessInfo:
  try:
    #logger.info('edr evidenceCallback: [%s]%s ' % (request,request.headers))
    return SuccessInfo(
        status = 'OK',
    )
  except Exception as e:
    #logger.info('edr error %s ' % e)
    return {
        "status" : 'internal error',
      }

@router_edr.get('/inboxCallback', response_model=SuccessInfo)
def get_inbox_callback(request: Request, \
                    ) -> SuccessInfo:
  try:
    #logger.info('edr inboxCallback: [%s]%s ' % (request,request.headers))
    return SuccessInfo(
        status = 'OK',
    )
  except Exception as e:
    #logger.info('edr error %s ' % e)
    return {
        "status" : 'internal error',
      }


@router_edr.get('/ade', response_model=Dict)
def find_eda(request: Request, \
             ade: str, \
             current_user: CurrentUser = Depends(get_current_wf_user), \
             ) -> Dict:
  #connector/edoreczenia/api_client.py:107 'https://ow.edoreczenia.gov.pl/api/se/v3/search/bae_search'
  #/api/se/v3/search/eda-confirmation
  try:
    edrc = EdrConnector(False)
    res=edrc.edr.SzukajEDA(ade)
    jres=json.loads(res.data)
    ade=jres['edaConfirmationResponses'][0]['recipientEda']['recipientEda']
    adres=jres['edaConfirmationResponses'][0]['baeSearchData']
    krs=nip=regon=pesel=''
    for o in adres['officialIds']:
      if o['referenceRegistry']=='krs':
        krs=o['id']
      elif o['referenceRegistry']=='nip':
        nip=o['id']
      elif o['referenceRegistry']=='regon':
        regon=o['id']
      elif o['referenceRegistry'] == 'pesel':
        pesel = o['id']
    wynik={
      "ade": ade,
      "name": adres["name"],
      "regon": regon,
      "pesel": pesel,
      "nip": nip,
      "krs": krs,
      "legalForm": adres["legalForm"],
    }
    for a in adres["address"]:
      addressType=','.join(a["addressType"])
      if 'correspondence' in addressType:#  | headquarters
        wynik['correspondence']={
          "countryCode": a["countryCode"],
          "city": a["city"],
          "buildingNumber": a["buildingNumber"],
          "street": a["street"],
          "flatNumber": a["flatNumber"],
          "postalCode": a["postalCode"],
          "postalOffice": a["postalOffice"],
        }
      if 'headquarters' in addressType:
        wynik=wynik | {
          "countryCode": a["countryCode"],
          "city": a["city"],
          "buildingNumber": a["buildingNumber"],
          "street": a["street"],
          "flatNumber": a["flatNumber"],
          "postalCode": a["postalCode"],
          "postalOffice": a["postalOffice"],
        }
  except Exception as e:
    log.exception('edr find_eda: %s', e)
    return {
      'message': 'error',
      'code': -1,
      'address': []
    }
  return {
    'message':'OK',
    'code':0,
    'address' : wynik
  }


@router_edr.get('/ade/person', response_model=Dict)
def find_person(request: Request, \
                firstname: str, \
                surname: str, \
                pesel: str = '', \
                city: str = '', \
                buildingNumber: str = '', \
                current_user: CurrentUser = Depends(get_current_wf_user), \
                ) -> Dict:
  try:
    edrc = EdrConnector(False)
    res=edrc.edr.SzukajOsoby(firstname,
                  surname,
                  pesel,
                  city,
                  buildingNumber,
                             {})
    wynik=[]
    jres=json.loads(res.data)
    ade = jres['baeSearchResponses'][0]['recipientEda']['recipientEda']
    for adres in jres['baeSearchData']:
      wynik.append({
        "ade": ade,
        "name": adres["name"],
        "surname":  adres["surname"],
        "professionalTitle":  adres["professionalTitle"],
        "legalForm": adres["legalForm"],
        "designatedOperator": adres["designatedOperator"],
        "address": {"ade": adres["address"]["ade"],
                    "validFrom":  adres["address"]["validFrom"],
                    "isMainAddress":  adres["address"]["isMainAddress"]},
        "correspondenceAddress": adres["correspondenceAddress"]
       })
  except Exception as e:
    log.exception('edr find_person: %s', e)
    return {
      'message': 'error',
      'code': -1,
      'address': {}
    }
  return {
    'message':'OK',
    'code':0,
    'address' : wynik
  }

@router_edr.get('/ade/inst', response_model=Dict)
def find_institution_by_id(request: Request, \
                           pesel: str = '', \
                           nip: str = '', \
                           regon: str = '', \
                           krs: str = '', \
                           current_user: CurrentUser = Depends(get_current_wf_user), \
                           ) -> Dict:
  try:
    wynik = {"ade": '',
             "name": '',
             "regon": regon,
             "pesel": pesel,
             "nip": nip,
             "krs": krs,
             "legalForm": '',
             "countryCode": '',
             "city": '',
             "buildingNumber": '',
             "street": '',
             "flatNumber": '',
             "postalCode": '',
             "postalOffice": '',
             "correspondence": {
               "countryCode": '',
               "city": '',
               "buildingNumber": '',
               "street": '',
               "flatNumber": '',
               "postalCode": '',
               "postalOffice": ''
             }
             }
    edrc = EdrConnector(False)
    res=edrc.edr.SzukajInstytucji(pesel, nip, regon, krs, '', {})
    jres=json.loads(res.data)
    ade=jres['baeSearchResponses'][0]['recipientEda']['recipientEda']
    wynik['ade']=ade
    for adres in jres['baeSearchResponses'][0]['baeSearchData']:
      if adres["index"]== 1:
        krs=nip=regon=pesel=''
        for o in adres['officialIds']:
          if o['referenceRegistry']=='krs':
            krs=o['id']
          elif o['referenceRegistry']=='nip':
            nip=o['id']
          elif o['referenceRegistry']=='regon':
            regon=o['id']
          elif o['referenceRegistry'] == 'pesel':
            pesel = o['id']
        wynik = {
          "ade": ade,
          "name": adres["name"],
          "regon": regon,
          "pesel": pesel,
          "nip": nip,
          "krs": krs,
          "legalForm": adres["legalForm"],
        }
        for a in adres["address"]:
          addressType = ','.join(a["addressType"])
          if 'correspondence' in addressType:  # | headquarters
            wynik['correspondence'] = {
              "countryCode": a["countryCode"],
              "city": a["city"],
              "buildingNumber": a["buildingNumber"],
              "street": a["street"],
              "flatNumber": a["flatNumber"],
              "postalCode": a["postalCode"],
              "postalOffice": a["postalOffice"],
            }
          if 'headquarters' in addressType:
            wynik = wynik | {
              "countryCode": a["countryCode"],
              "city": a["city"],
              "buildingNumber": a["buildingNumber"],
              "street": a["street"],
              "flatNumber": a["flatNumber"],
              "postalCode": a["postalCode"],
              "postalOffice": a["postalOffice"],
            }
  except Exception as e:
    log.exception('edr wyszukiwanie instytucji: %s', e)
    return {
      'message': 'error',
      'code': -1,
      'address': wynik
    }
  return {
    'message':'OK',
    'code':0,
    'address' : wynik
  }

@router_edr.get('/ade/insta', response_model=Dict)
def find_institution_by_address(request: Request, \
                                name: str = '', \
                                city: str = '', \
                                buildingNumber: str = '', \
                                street: str = '', \
                                flatNumber: str = '', \
                                postalCode: str = '', \
                                current_user: CurrentUser = Depends(get_current_wf_user), \
                                ) -> Dict:
  try:
    wynik = {"ade": '',
             "name": name,
             "regon": '',
             "pesel": '',
             "nip": '',
             "krs": '',
             "legalForm": '',
             "countryCode": '',
             "city": city,
             "buildingNumber": buildingNumber,
             "street": street,
             "flatNumber": flatNumber,
             "postalCode": postalCode,
             "postalOffice": '',
             "correspondence":{
                "countryCode": '',
                "city": '',
                "buildingNumber": '',
                "street": '',
                "flatNumber": '',
                "postalCode": '',
                "postalOffice": ''
                }
             }
    edrc = EdrConnector(False)
    #token=get_access_token()
    res=edrc.edr.SzukajInstytucji('', '', '', '', name,
                                  {
                                "countryCode":"PL",
                                "city":city,
                                "buildingNumber":buildingNumber,
                                "street":street,
                                "flatNumber":flatNumber,
                                "postalCode":postalCode,})
    jres=json.loads(res.data)
    ade=jres['baeSearchResponses'][0]['recipientEda']['recipientEda']
    wynik["ade"]= ade
    for adres in jres['baeSearchResponses'][0]['baeSearchData']:
      if adres["index"]== 1:
        krs=nip=regon=pesel=''
        for o in adres['officialIds']:
          if o['referenceRegistry']=='krs':
            krs=o['id']
          elif o['referenceRegistry']=='nip':
            nip=o['id']
          elif o['referenceRegistry']=='regon':
            regon=o['id']
          elif o['referenceRegistry'] == 'pesel':
            pesel = o['id']
        wynik = {
          "ade": ade,
          "name": adres["name"],
          "regon": regon,
          "pesel": pesel,
          "nip": nip,
          "krs": krs,
          "legalForm": adres["legalForm"],
        }
        for a in adres["address"]:
          addressType = ','.join(a["addressType"])
          if 'correspondence' in addressType:  # | headquarters
            wynik['correspondence'] = {
              "countryCode": a["countryCode"],
              "city": a["city"],
              "buildingNumber": a["buildingNumber"],
              "street": a["street"],
              "flatNumber": a["flatNumber"],
              "postalCode": a["postalCode"],
              "postalOffice": a["postalOffice"],
            }
          if 'headquarters' in addressType:
            wynik = wynik | {
              "countryCode": a["countryCode"],
              "city": a["city"],
              "buildingNumber": a["buildingNumber"],
              "street": a["street"],
              "flatNumber": a["flatNumber"],
              "postalCode": a["postalCode"],
              "postalOffice": a["postalOffice"],
            }
  except Exception as e:
    log.exception('edr wyszukiwanie instytucji: %s', e)
    return {
      'message': 'error',
      'code': -1,
      'address': wynik
    }
  return {
    'message':'OK',
    'code':0,
    'address' : wynik
  }

"""
class Attachment(BaseModel):
  name : str
  content: str

class SendConfig(BaseModel):
  ADE: str = Field(None, description='Adres DE')
  subject: str = Field(None, description='tytuł')
  textBody: str = Field(None, description='treść')
  attachments: Optional[List[Attachment]] = None

@router_edr.post('/send', response_model=Dict)
def sendEDR(request: Request, \
                  config: SendConfig,

                    ) -> Dict:
  try:
    token=get_access_token()
    edr=Edr(token,adres_edr)
    (odpowiedz, kod, tmp)=edr.WysylanieWiadomosci(config.ADE, config.subject, config.textBody, config.attachments)
    return {
      'message':'OK',
      'code': 0,
      'result': odpowiedz.data
    }
  except Exception as e:
    print(e)
    return {
      'message': 'error',
      'code': -1,
      'result': e.__str__()
    }


@router_edr.get('/inbox', response_model=Dict)
def Inbox(request: Request, \
                    ) -> Dict:
  try:
    token=get_access_token()
    edr=Edr(token,adres_edr)
    (odpowiedz, kod, tmp)=edr.ListaWiadomosci(
                    label='INBOX',  # Folder do pobrania listy wiadomości SENT lub INBOX
                    # submissionDateFrom=None,  # Data od dla pola submissionDate
                    # submissionDateTo=None,  # Data do dla pola submissionDate.
                    # receiptDateFrom=None,  # Data otrzymania od
                    # receiptDateTo=None,  # Data otrzymania do
                    )
    print(kod)
    lista=odpowiedz.data
    wynik=json.loads(lista)['messages']
  except Exception as e:
    print(e)
    return {
      'message': 'error',
      'code': -1,
      'messages': []
    }
  return {
    'message':'OK',
    'code':0,
    'messages' : wynik
  }

@router_edr.get('/message', response_model=Dict)
def Inbox(request: Request, \
                  messageId: str,

                    ) -> Dict:
  try:
    token=get_access_token()
    edr=Edr(token,adres_edr)
    #messageId=m['messageMetadata']['messageId'].encode('ascii', 'ignore').decode('unicode_escape')
    (odpowiedz, kod, tmp)=edr.PobranieWiadomosci(messageId)
    wiadomosc=json.loads(odpowiedz.data)
  except Exception as e:
    print(e)
    return {
      'message': 'error',
      'code': -1,
      'message': {}
    }
  return {
    'message':'OK',
    'code':0,
    'message' : wiadomosc
  }
"""
async def save_result(id, messageId):
  try:
    log=logging.getLogger("edr")
    log.info('Zlecenie zapisu wyniku Webcon! %s %s ' % (id,messageId))
    for i in range(3): # czeka aż Webcon przejdzie do następnego stanu
      await asyncio.sleep(5.0)
  except:
    log=None
  instance=None
  t=0
  while (t<3) and (not instance):
    t+=1
    instance=update_webcon(id, messageId, log)
    if not instance:
      await asyncio.sleep(5.0)
      if log:
        log.info(f'... czekam {t}')
    else:
      if log:
        log.info('OK, Instance: %s' % str(instance))


# (2026-10-05): `GET` z efektem ubocznym (zmiana stanu obiegu w Webcon) -
# nie zmieniam metody HTTP dla kompetybilności (TODO?), ale taki
# endpoint musi mieć przynajmniej autoryzację (patrz `require_process_admin`).
@router_edr.get('/webcon_update', response_model=Dict)
async def get_webcon_update(request: Request, \
                  background_tasks: BackgroundTasks,
                  id: int, \
                  messageId: str, \
                  current_user: CurrentUser = Depends(require_process_admin), \
                  ) -> Dict:
  try:
    background_tasks.add_task(save_result, id, messageId)
    return {
        'message':'OK',
        'code': 0,
        'result': messageId
      }
  except Exception as e:
    return {
      'message': 'error',
      'code': -1,
      'result': e.__str__()
    }

async def send_to_webcon(id):
  send1_from_webcon(id)

@router_edr.get('/webcon_send', response_model=Dict)
async def get_webcon_send(request: Request, \
                  background_tasks: BackgroundTasks,
                  id: int, \
                  current_user: CurrentUser = Depends(require_process_admin), \
                  ) -> Dict:
  try:
    background_tasks.add_task(send_to_webcon, id)
    return {
        'message':'OK',
        'code': 0,
        'result': str(id)
      }
  except Exception as e:
    return {
      'message': 'error',
      'code': -1,
      'result': e.__str__()
    }


@router_edr.get('/evidences/webcon', response_model=Dict)
async def evidences_to_webcon(request: Request, \
                              background_tasks: BackgroundTasks,
                              id: int, \
                              messageId: str, \
                              current_user: CurrentUser = Depends(require_process_admin), \
                              ) -> Dict:
  try:
    background_tasks.add_task(fetch_evidences_att, id, messageId, None)
    return {
      'message': 'OK',
      'code': 0,
      'result': messageId
    }
  except Exception as e:
    return {
      'message': 'error',
      'code': -1,
      'result': e.__str__()
    }

