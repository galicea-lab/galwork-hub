#!/usr/bin/env python3
# coding: utf-8
# authors: Jerzy Wawro
# (C) JW 2026

import os,sys
import inspect
import time

from requests import Request
from starlette.responses import JSONResponse

currentdir = os.path.dirname(os.path.abspath(inspect.getfile(inspect.currentframe())))
sys.path.insert(0, currentdir)

from fastapi.middleware.cors import CORSMiddleware

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from core.config import settings
from core.log import log_init

from router import v1router

import platform
iis=( platform.uname().system=='Windows' )
if iis:
  # pip install a2wsgi
  # pip install wfastcgi
  # wfastcgi-enable - jako admin po aktywacji venv: https://mtuseeq.medium.com/how-to-deploy-flask-app-on-windows-server-using-fastcgi-and-iis-73d8139d5342
  #D:\er\venv\Scripts\python.exe|D:\etr\venv\Lib\site-packages\wfastcgi.py
  from a2wsgi import ASGIMiddleware
ssl=iis
if ssl:
  import ssl

# logger
import logging
logger = logging.getLogger(__name__)
log_init()

app = FastAPI(debug=settings.app.debug,
              title="galwork",
              description="",
              summary=".",
              version="0.0.1",
              terms_of_service="https://github.com/galicea-lab/galwork-hub",
              contact={
                  "url":"https://github.com/galicea-lab"
              },
              license_info={
                  "name": "Apache 2.0",
                  "url": "https://www.apache.org/licenses/LICENSE-2.0.html",
              },
              )

@app.get('/health')
def health():
  return HTMLResponse(f"OK", status_code=200)

# lista dozwolonych źródeł (domen) z config.ini -> sekcja [app], klucz `cors_origins` 
# (lista po przecinku).

# W środowisku deweloperskim możesz wpisać "*" dla wszystkich źródeł,
# ale w produkcji ZAWSZE ograniczaj do konkretnych domen. Uwaga: "*"
# w połączeniu z allow_credentials=True jest odrzucane przez przeglądarki -
# wtedy ustaw cors_allow_credentials=false w config.ini.
origins = settings.app.cors_origins

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,                    # Lista dozwolonych źródeł (z config.ini)
    allow_credentials=settings.app.cors_allow_credentials,  # Ciasteczka/nagłówki auth w cross-origin
    allow_methods=["*"],    # Zezwól na wszystkie metody HTTP (GET, POST, PUT, DELETE, OPTIONS itp.)
    allow_headers=["*"],    # Zezwól na wszystkie nagłówki w żądaniach cross-origin
)

# Middleware do logowania wywołań
@app.middleware("http")
async def log_requests(request: Request, call_next):
  """
  Middleware do logowania czasu trwania żądania oraz jego statusu.

  Przy poziomie DEBUG (core/config.py -> config.ini [logging] level) loguje
  też nagłówki i treść żądania - to był dawniej zakres api/ep.py::log,
  wywoływanego jako zależność FastAPI w poszczególnych endpointach; tu robi
  to jedno miejsce, dla wszystkich żądań.
  """
  if settings.logging.level == 'DEBUG':
    try:
      body = await request.body()
      logger.debug("Body: %s" % body)
      logger.debug("Headers:")
      for name, value in request.headers.items():
        logger.debug(f"\t{name}: {value}")
    except Exception as e:
      logger.debug(e)

  start_time = time.time()
  try:
    response = await call_next(request)
  except Exception as e:
    logger.error(f"Request failed: {request.method} {request.url} - Exception: {e}")
    return JSONResponse(status_code=500, content={"message": "Internal Server Error"})

  process_time = time.time() - start_time
  logger.info(f"Request: {request.method} {request.url} - Status: {response.status_code} - Czas: {process_time:.4f}s")
  return response

#app.add_middleware(CORSMiddleware,)

app.mount("/v1", v1router)



if iis:
  wsgi_app = ASGIMiddleware(app)

@app.get('/', response_model=None)
def get_root():
  return {"message": "galwork w. 001"}

if __name__ == "__main__":
  import uvicorn
  logger.info('Start aplikacji Galwork HUB')
  from core.config import settings
  logger.info(settings)
  if ssl:
    ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    # openssl req -x509 -newkey rsa:4096 -nodes -out cert.pem -keyout key.pem -days 365
    # openssl pkcs12 -export -out self.pfx -inkey  key.pem -in cert.pem
    # dodaj pfx do zaufanych
    ssl_context.load_cert_chain('./cert.pem', keyfile='./key.pem')
    uvicorn.run("main:app", host="0.0.0.0", port=8086, \
                ssl_keyfile='./key.pem',
                ssl_certfile='./cert.pem')
    #ssl=ssl_context
    # )
  else:
    uvicorn.run("main:app", host="0.0.0.0", port=8086) #, reload=True)