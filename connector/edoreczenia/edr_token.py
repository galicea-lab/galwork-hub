import uuid
from datetime import datetime
from urllib.parse import urlparse
# https://pypi.org/project/jwt/
# pip uninstall jwt / pip install jwt

# adres usługi tokenowej (audiopcja JWT i Host) wyliczany z
# sekcji [edr] config/config.ini (`url` + `realm`). Nadpisania: EDR_URL, EDR_REALM.
from connector.base import BaseConnector

_edr_config = BaseConnector.load_ini_section('edr')

try:
  from jwt import (
    JWT,
    #  jwk_from_dict,
    jwk_from_pem,
  )
  ns_pyjwt=False
except:
  ns_pyjwt = True
  import jwt
import json
from .api_client import ApiClient


def _edr_base_url() -> str:
  return (_edr_config.get('url') or '').rstrip('/')


def _edr_realm() -> str:
  return _edr_config.get('realm', 'EDOR')


def _token_path() -> str:
  return f'auth/realms/{_edr_realm()}/protocol/openid-connect/token'


#
#authorizationUrl="https://gov.pl/oauth2/authorize"
#tokenUrl="https://gov.pl/oauth2/token"
#refreshUrl="https://gov.pl/oauth2/refresh"

#scope_read_messages="read messages"
#scope_write_messages="send messages"
#scope_read_settings="read settings"
#write_settings="change settings"
#se_content_filter="content filter"
#NAZWA_SYSTEMU = 'GALWORK'  # nazwa nadana w procesie dodawania systemu w Module uprawnień,

"""
TAK BYŁOBY, GDYBY PODPIS KLUCZEM SYMETRYCZNYM HS512: 
def make_signed_token(secret, payload):
    return jwt.encode(payload, secret, algorithm="HS512")
"""

def request_token(ADRES_ADE, NAZWA_SYSTEMU, key):
  header = {
    'alg': 'RS256',
    #"typ":"JWT"
  }
  #now=int(dt.now().timestamp())
  now=int(datetime.utcnow().timestamp())
  # `aud` zgodny z sekcją [edr] (url + realm); środowisko wskazuje `url`.
  payload = { #  token RFC7523
    "aud": f"{_edr_base_url()}/{_token_path()}",
    "exp": now+60000, # expiration time
    "iat": now, # issued at
    "iss": ADRES_ADE+'.SYSTEM.'+NAZWA_SYSTEMU,
    "jti": str(uuid.uuid4()), # JWT ID: unique identifier for the token.
    "nbf": now,
    "sub": ADRES_ADE+'.SYSTEM.'+NAZWA_SYSTEMU
  }
  if ns_pyjwt:
    token = jwt.encode(
        payload,
        key,
        algorithm='RS256',
        headers=header,
        json_encoder=None,
#        sort_headers=False
    )
  else:
    if isinstance(key, str):
      key = key.encode('utf-8')
    private_key = jwk_from_pem(key)
    instance = JWT()
    token = instance.encode(payload=payload, optional_headers=header, key=private_key, alg='RS256')
  # print(token)
  # sprawdź na https://jwt.io/
  return token

def post_auth_request(signed_token, ADE_ADRES):
  """
  Zapytanie:
    POST /auth/realms/<realm>/protocol/openid-connect/token?login_hint=$ADRES_ADE HTTP/1.1
    Connection: close
    User-Agent: PostmanRuntime/7.28.4
    Accept: */*
    Host: <host_z_sekcji_[edr]>
    Accept-Encoding: gzip, deflate, br
    Content-Type: application/x-www-form-urlencoded
    Content-Length: 830

    client_assertion_type=urn%3Aietf%3Aparams%3Aoauth%3Aclient-assertion-type%3Ajwt-bearer&grant_type=client_credentials&client_assertion=$TOKEN

    gdzie:
        • $ADRES_ADE ‒ adres do e-Doręczeń, np. ADE.AE:PL-00000-00000-AAAAA-00 ,
        • $TOKEN ‒ token JWS przygotowany i podpisany w poprzednich krokach, np.:

  """
  # ścieżka i Host wyliczane z sekcji [edr] config/config.ini.
  token_url=_token_path() + '?login_hint=' + ADE_ADRES
  header_params={
   'Content-Type': 'application/x-www-form-urlencoded',
   'Accept': '*/*',
   'Host': urlparse(_edr_base_url()).netloc,
   'Accept-Encoding': 'gzip, deflate, br'
  }
  # Authentication setting
  # https://datatracker.ietf.org/doc/html/rfc7523#page-10
  form_params={
    'client_assertion_type':'urn:ietf:params:oauth:client-assertion-type:jwt-bearer',
    'grant_type':'client_credentials',
    'client_assertion': signed_token
  }
  params = locals()
  collection_formats = {}
  path_params = {}
  query_params = {} #{'login_hint':ADE_ADRES}
  local_var_files = {}
  body_params = None
  api_client = ApiClient(None)
  result=api_client.call_api(
    token_url,
    'POST',
    path_params,
    query_params,
    header_params,
    body=body_params,
    post_params=form_params,
    response_type=dict
  )
  response=api_client.last_response
  return  (json.loads(response.data), response.status, response.urllib3_response.headers)
