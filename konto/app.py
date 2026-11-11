# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

# Osobne wdrożenie usługi kodów - tylko /v1/konto, bez
# pozostałych modułów huba:
#   uvicorn konto.app:app --host 0.0.0.0 --port 8010 --proxy-headers
# Uprawnienia administracyjne Keycloak trafiają wtedy tylko do tego procesu.

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config.config import settings
from core.log_redact import RedactAccessLogFilter
from konto.router import router


def create_app() -> FastAPI:
  # log dostępu bez wartości parametrów (SAMLart, RelayState)
  access = logging.getLogger('uvicorn.access')
  if not any(isinstance(f, RedactAccessLogFilter) for f in access.filters):
    access.addFilter(RedactAccessLogFilter())
  konto = FastAPI(title='Konto - kody jednorazowe', version='1.0.0')
  konto.include_router(router)
  root = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
  if settings.app.cors_origins:
    root.add_middleware(
      CORSMiddleware, allow_origins=settings.app.cors_origins,
      allow_credentials=False, allow_methods=['GET', 'POST'], allow_headers=['Content-Type'])

  @root.get('/health')
  def health() -> dict:
    return {'status': 'ok'}

  root.mount('/v1/konto', konto)
  return root


app = create_app()
