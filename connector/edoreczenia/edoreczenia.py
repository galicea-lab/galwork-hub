#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#

import json
import logging

log = logging.getLogger(__name__)

from connector.edoreczenia.api  import EvidencesApi
from connector.edoreczenia.api  import MessagesApi
from connector.edoreczenia.api  import SubscriptionsApi
from connector.edoreczenia.api.search_api import SearchEngineApi
from connector.edoreczenia.se_model import BaeSearch, AddressData
from connector.edoreczenia.ua_model import Message, MessagesWrapper
from connector.edoreczenia.api.attachments_api import AttachmentsApi

from pydantic import BaseModel, ValidationError

class Edr():
  def __init__(self, access_token, myDeliveryAddress):
    self.access_token=access_token
    self.myDeliveryAddress=myDeliveryAddress

  def ListaWiadomosci(self,
                    label='.', # Folder do pobrania listy wiadomości
                    submissionDateFrom=None, # Data od dla pola submissionDate
                    submissionDateTo=None, # Data do dla pola submissionDate.
                    receiptDateFrom=None, # Data otrzymania od
                    receiptDateTo=None, #Data otrzymania do
                    ) -> MessagesWrapper:
    # -> BaseModel
    """
    Przykład treści poprawnej odpowiedzi (200 - OK)
        {
        "messages": [
        {
        "messageControlData": {
        "status": "INBOX",
        "MessageType": "Message",
        "labels": [
        {
        "label": "INBOX"
        }
        ]
        },
        "messageMetadata": {
        "from": {
        "myDeliveryAddress": "AE:PL-00000-00013-AAAAA-06"
        },
        "to": [
        {
        "myDeliveryAddress": "AE:PL-00000-00014-AAAAA-05"
        }
        ],
        "subject": "Przykładowy temat wiadomości",
        "messageId": "ed28de2d-2b93-40ce-be55-ff260475f08e",
        "threadId": "ed28de2d-2b93-40ce-be55-ff260475f08e",
        "receiptDate": {},
        "submissionDate": {},
        "shippingService": "electronic"
        },
        "textBody": "Przykładowa treść wiadomości"
        }
        ]
        }
    """
    api=MessagesApi(self.access_token)
    response=api.getMessages(self.myDeliveryAddress,
                           label=label,
                           submission_date_from=submissionDateFrom,
                           submission_date_to=submissionDateTo,
                           receipt_date_from=receiptDateFrom,
                           receipt_date_to=receiptDateTo,
                           _preload_content=False, # !!!! próba rozpakowania -> błąd
                             )

    model: type[BaseModel] = MessagesWrapper
    try:
      data = json.loads(response.data.decode('utf8'))
      if model:
        return model(**data)
      else:
        return None
    except json.JSONDecodeError as e:
      raise ValueError(f"Błąd dekodowania JSON: {e}")
    except ValidationError as e:
      raise ValueError(f"Błąd walidacji modelu: {e}")


  def PobranieWiadomosci(self,
                         messageId,
                         format='fullExtended' # lub 'full': z załącznikami / z listą załączników (bez treści)
                         ) -> Message:
    """
  Przykład treści poprawnej odpowiedzi (200 - OK)
  {
  "messageControlData": {
  "status": "INBOX",
  "MessageType": "Message",
  "labels": [
  {
  "label": "INBOX"
  }
  ]
  },
  "messageMetadata": {
  "from": {
  "myDeliveryAddress": "AE:PL-00000-00013-AAAAA-06"
  },
  "to": [
  {
  "myDeliveryAddress": "AE:PL-00000-00014-AAAAA-05"
  }
  ],
  "subject": "Przykładowy temat wiadomości",
  "messageId": "ed28de2d-2b93-40ce-be55-ff260475f08e",
  "threadId": "ed28de2d-2b93-40ce-be55-ff260475f08e",
  "receiptDate": {},
  "submissionDate": {},
  "shippingService": "electronic"
  },
  "textBody": "Przykładowa treść wiadomości"
  }
    """
    api = MessagesApi(self.access_token)
    response=api.getMessage(self.myDeliveryAddress, messageId, format=format,
                          _preload_content=False  # !!!! próba rozpakowania -> błąd
                        )
    try:
      data = json.loads(response.data.decode('utf8'))
      # API w wersji 3 zwraca listę wiadomości (parametr messageId w ścieżce
      # jest tablicą, do 50 elementów na żądanie) - przy pobieraniu jednej
      # wiadomości lista zawiera zawsze jeden element.
      if isinstance(data, list):
        data = data[0]
      return Message(**data)
    except json.JSONDecodeError as e:
      raise ValueError(f"Błąd dekodowania JSON: {e}")
    except ValidationError as e:
      raise ValueError(f"Błąd walidacji modelu: {e}")


  def PobranieDowodow(self, messageId, **kwargs):
      """
      Przykład treści poprawnej odpowiedzi (200 - OK)
  {
  "evidences": [
  {
  "evidenceId": "830f6d4c-5e77-4e2e-a305-f8678acb49e0",
  "messageId": "ed28de2d-2b93-40ce-be55-ff260475f08e",
  "createDate": "2021-06-30T13:01:22.268462Z",
  "externalData": "http://poczta-polska.pl/edor/AE:PL-13447-72418-ECVIU-
  30/evidences/qerds/34e09d5b-4965-4943-970c-c26549d7f623",
  "type": "A.1"
  }
  ]
  }   """
      api=EvidencesApi(self.access_token)
      return api.getEvidencesForMessage(self.myDeliveryAddress, messageId,
                                        _preload_content=False, # !!!! próba rozpakowania -> błąd (models w api_client w.269
                                         **kwargs)

  def PobranieDowodu(self, evidenceId, **kwargs):
      """
      Przykład treści poprawnej odpowiedzi (200 - OK)
  {
  "evidenceId": "830f6d4c-5e77-4e2e-a305-f8678acb49e0",
  "messageId": "ed28de2d-2b93-40ce-be55-ff260475f08e",
  "createDate": "2021-06-30T13:01:22.268462Z",
  "externalData": "http://poczta-polska.pl/edor/AE:PL-13447-72418-ECVIU-
  30/evidences/qerds/34e09d5b-4965-4943-970c-c26549d7f623",
  "type": "A.1"
  }
      """
      api=EvidencesApi(self.access_token)
      return api.getEvidence(self.myDeliveryAddress, evidenceId,
                             _preload_content=False,  # !!!! próba rozpakowania -> błąd (models w api_client w.269
                             **kwargs)


  def PobranieDowodowZip(self, messageId, **kwargs):
    api = EvidencesApi(self.access_token)
    return api.getZipEvidences(self.myDeliveryAddress,messageId,
                           _preload_content=False,  # !!!! próba rozpakowania -> błąd (models w api_client w.269
                           **kwargs)


  def UsuniecieDowodu(self, evidenceId, **kwargs):
      # API w wersji 3 (uaapi_3.0.8.2.yaml) nie udostępnia żadnego endpointu
      # usuwającego dowód (sekcja Evidences zawiera wyłącznie operacje GET) -
      # w v1 taki endpoint istniał. Funkcja nie ma już odpowiednika w API.
      raise NotImplementedError(
        'Usunięcie dowodu nie jest wspierane przez API e-Doręczeń w wersji 3 '
        '(brak endpointu DELETE dla /evidences w uaapi_3.0.8.2.yaml).'
      )

  #
  def WysylanieWiadomosci(self, eDeliveryAddress, subject, textBody, attachments, recipient=''):
    """
    Przykład treści poprawnego żądania:
  {
  "messageControlData": {
  "MessageType": "Message"
  },
  "messageMetadata": {
  "to": [
  {
  "eDeliveryAddress": "AE:PL-00000-00014-AAAAA-05"
  }
  ],
  "subject": "Przykładowy temat wiadomości",
  "shippingService": "electronic"
  },
  "textBody": "Przykładowa treść wiadomości",
  "attachments": [
  {
  "order": 0,
  "attachmentId": "D41568F4-7175-42BB-9503-DAA282180D70",
  "file": {
  "fileMetadata": {
  "fileId": "D41568F4-7175-42BB-9503-DAA282180D70",
  "filename": "document.txt",
  "contentType": "text/plain",
  "size": 16732,
  "alg": "SHA-3",
  "hash":
  "MmJiMzE0ZjUxODU0NGIyZTk4MGQ5MGRjMWIyMDU1YjJlMzJlODAxNjQ3MzA0YzMxZjQxZDM4NW
  NlNGU2ODc3YjZkMDcxZjE4NGEwMGUyZGI3N2ZiZjMyYzA0OTAwYjE4ZWU2OTI3NjIyNDRjYmZhY2Ew
  NjMxNzQ2M2Y2Y2U5M2I=",
  "description": "Text document"
  },
  "file": "{base64Encodeded}"
  }
  }
  ]
  }

  =========================
  Przykład treści poprawnej odpowiedzi (200 - OK)
  {
  "Messages": [
  {
  "messageId": "ed28de2d-2b93-40ce-be55-ff260475f08e",
  "AddresseeADE": "AE:PL-13447-72418-ECVIU-30",
  "Status": "SUCCESS"
  }
  ]
  }
    """
    body={
         "messageControlData": { "MessageType": "Message"  },
          "messageMetadata": {
            "from": {"eDeliveryAddress":self.myDeliveryAddress
              #, "contributor": {"companyName": 'Państwowa Kademia Nauk Stosowanych w Przemyślu'}
            },
            "to": [
             { "eDeliveryAddress": eDeliveryAddress,
               "contributor":{"companyName":recipient}}
                #"contributor":{"firstName": "YMER", "lastName": "ZGODOWSKI"}},
             ],
          "subject": subject,
          "shippingService": "electronic"
         },
        "textBody": textBody,
        "attachments": []
    }
    for a in attachments:
        aa=json.loads(a.model_dump_json())
        body['attachments'].append(aa)
    api = MessagesApi(self.access_token)
    result = api.postMessage(body, self.myDeliveryAddress,
                             _preload_content=False,  # !!!! próba rozpakowania -> błąd (models w api_client w.269
                             )
    return result


  def PobranieWiadomosciF(self,
                         messageId,
                         format='fullExtended' # lub 'full': z załącznikami / z listą załączników (bez treści)
                         )->Message:
    """
  Przykład treści poprawnej odpowiedzi (200 - OK)
  {
  "messageControlData": {
  "status": "INBOX",
  "MessageType": "Message",
  "labels": [
  {
  "label": "INBOX"
  }
  ]
  },
  "messageMetadata": {
  "from": {
  "myDeliveryAddress": "AE:PL-00000-00013-AAAAA-06"
  },
  "to": [
  {
  "myDeliveryAddress": "AE:PL-00000-00014-AAAAA-05"
  }
  ],
  "subject": "Przykładowy temat wiadomości",
  "messageId": "ed28de2d-2b93-40ce-be55-ff260475f08e",
  "threadId": "ed28de2d-2b93-40ce-be55-ff260475f08e",
  "receiptDate": {},
  "submissionDate": {},
  "shippingService": "electronic"
  },
  "textBody": "Przykładowa treść wiadomości"
  }
    """
    # Identyczna logika jak PobranieWiadomosci - zachowana jako alias
    # nazwowy dla kompatybilności z istniejącymi wywołaniami (edr_connector.py).
    return self.PobranieWiadomosci(messageId, format=format)



  def PobranieDowoduF(self, evidenceId, **kwargs):
      api=EvidencesApi(self.access_token)
      return api.getEvidence(self.myDeliveryAddress, evidenceId,
                             _preload_content=False,  # !!!! próba rozpakowania -> błąd (models w api_client w.269
                             **kwargs)

  def DefiniujSubskrypcje(self, eDeliveryAddress, messageCallback,evidenceCallback,inboxCallback):
    api = SubscriptionsApi(self.access_token)
    body={
    #    "eDeliveryAddress": eDeliveryAddress,
        "messageCallbackUrl": messageCallback,
        "evidenceCallbackUrl": evidenceCallback,
        "inboxCallbackUrl": inboxCallback
    }
    result = api.putSubscriptions(eDeliveryAddress, body=body)
    return result

  def UsunWiadomosc( self, messageId ):
    api=MessagesApi(self.access_token)
    result=api.deleteMessage(self.myDeliveryAddress,messageId)
    return result


  def PobierzZalacznik(self,
                         messageId,
                         att_id
                         ):

    api = AttachmentsApi(self.access_token)
    # Metoda publiczna (jak getMessage/getMessages) wymusza _return_http_data_only,
    # dlatego zwraca tylko jedną wartość - przy _preload_content=False jest to
    # surowy obiekt odpowiedzi urllib3 (ma .data i .status), a nie trójka (data, code, headers).
    response=api.getMessageAttachment(self.myDeliveryAddress, messageId, att_id,
                                       _preload_content=False)  # !!!! próba rozpakowania -> błąd
    if response.status==200:
        try:
            return (response.data,200)
        except Exception:
            log.exception('Edr.PobierzZalacznik: błąd odczytu załącznika %s/%s', messageId, att_id)
            return (None,200)
    else:
        return (response.data, response.status)

############## szukanie #####################

  def SzukajEDA(self, reda, eda=None ):
    # domyślnym nadawcą jest adres własny connectora
    # (self.myDeliveryAddress z sekcji [edr]), nie zaszyty adres instancji.
    eda = eda or self.myDeliveryAddress
    bs={"recipientEdas":[reda],
        "senderEda":eda,"offset":0, "limit":2}
    api = SearchEngineApi(self.access_token)
    result = api.SearchBAE2(bs)
    return result

  def SzukajOsoby(self, name, surname, pesel='', city='', buildingNumber='',\
                  eda=None ):
    eda = eda or self.myDeliveryAddress
    if pesel != '':
        bs={"name":name,"senderEda":eda,
              "searchCategory":["INDIVIDUAL"],
              "surname":surname,
              "officialIds":[{"id":pesel,"referenceRegistry":"pesel"}],
              "limit":20}
    else:
        bs=  {"name":name,"senderEda":eda,
              "searchCategory":["INDIVIDUAL"],
              "surname":surname,
              "address":[{"addressType":["correspondence","headquarters"],
              "countryCode":"PL","city":city,"buildingNumber":buildingNumber,
              "country":{"id":"PL","label":"POLSKA","name":"POLSKA"}}],"limit":20}
    api = SearchEngineApi(self.access_token)
    result = api.SearchBAE1(bs)
    return result

  def SzukajInstytucji(self, pesel='', nip='', regon='', krs='', name='', address={},\
                  eda=None ):
    eda = eda or self.myDeliveryAddress
    if pesel != '':
      bs = {"senderEda": eda,
            "searchCategory": ["COMPANY", "ORGANISATION", "PUBLIC_INSTITUTION"],
            "officialIds": [{"id": pesel, "referenceRegistry": "pesel"}],
            "limit": 20}
    elif regon != '':
        bs = {"senderEda": eda,
              "searchCategory": ["COMPANY", "ORGANISATION", "PUBLIC_INSTITUTION"],
              "officialIds": [{"id": regon, "referenceRegistry": "regon"}],
              "limit": 20}
    elif nip != '':
        bs = {"senderEda": eda,
              "searchCategory": ["COMPANY", "ORGANISATION", "PUBLIC_INSTITUTION"],
              "officialIds": [{"id": nip, "referenceRegistry": "nip"}],
              "limit": 20}
    elif krs != '':
        bs = {"senderEda": eda,
              "searchCategory": ["COMPANY", "ORGANISATION", "PUBLIC_INSTITUTION"],
              "officialIds": [{"id": krs, "referenceRegistry": "krs"}],
              "limit": 20}
    else:
        bs= {"senderEda": eda,
             "searchCategory": ["COMPANY", "ORGANISATION", "PUBLIC_INSTITUTION"],
             "entityName":name,
             "address":[address,],
             "limit": 20}
        bs['address'][0]['addressType']=["correspondence","headquarters"]
        bs['address'][0]['country']={"id":"PL","label":"POLSKA","name":"POLSKA"}
    api = SearchEngineApi(self.access_token)
    result = api.SearchBAE1(bs)
    return result

####### old
  def Szukaj(self, eda=None, category=["INDIVIDUAL"],\
             name='', surname='', pesel='', city='', building=''):
    eda = eda or self.myDeliveryAddress
    bs =BaeSearch()
# !!!    bs.searchCategory='legalPersonNCRCertified'
    bs.surname=''
    bs.entityName=''
    bs.name=''
    bs.surname=''
    bs.professionalTitle=''
    bs.recipientEdas=[]
    bs.senderEda=eda
    bs.officialIds=[]
    bs.correspondenceAddress=None #AddressData(addressType='correspondence')
    bs.headquartersAddress=None #AddressData(addressType='correspondence')

    client_cert_subject=''
    # openssl x509 -in certificate.crt -text -noout
    # https://www.sslshopper.com/certificate-decoder.html
    api = SearchEngineApi(self.access_token)

    bs={
"searchCategory":category, #["ADVOCATE"],
"name": name,
"surname": surname,
"senderEda": eda,
"offset": 0,
"limit": 20,
}
    result = api.SearchBAE(client_cert_subject, bs)
    return result


  def SzukajNadawcy(self, eda='', cert_subject=''):
    bs = BaeSearch()
    bs.surname = ''
    bs.entityName = ''
    bs.name = ''
    bs.surname = ''
    bs.professionalTitle = ''
    bs.recipientEdas = []
    bs.senderEda = eda
    bs.officialIds = []
    bs.correspondenceAddress = None  # AddressData(addressType='correspondence')
    bs.headquartersAddress = None  # AddressData(addressType='correspondence')
    # openssl x509 -in certificate.crt -text -noout
    # https://www.sslshopper.com/certificate-decoder.html
    api = SearchEngineApi(self.access_token)
    result = api.SearchBAE(cert_subject, bs)
    return result

# GET /api/beuis/v2/{externalAddress}/evidences/purde/{evidenceId}

