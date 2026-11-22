# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Endpoint webhooków Taigi - `POST /v1/integrations/taiga/webhook/{id}`.

Bez uwierzytelniania użytkownika: autentyczność zapewnia podpis
`X-TAIGA-WEBHOOK-SIGNATURE` (HMAC-SHA1 z SUROWEJ treści, klucz per projekt).
Endpoint tylko zapisuje zdarzenie i szybko odpowiada; przetwarzanie jest
asynchroniczne (scripts/pm_taiga.py process).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from api.dependencies import get_db
from pm.integrations.taiga import service
from pm.integrations.taiga.events import TaigaPayloadError, from_webhook

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/taiga", tags=["integracja Taiga"])


def _taiga():
    from connector.taiga import get_connector
    return get_connector()


def signature_valid(raw_body: bytes, secret: str, signature: str | None) -> bool:
    if not signature:
        return False
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha1).hexdigest()
    return hmac.compare_digest(expected, signature.strip().lower())


@router.post("/webhook/{taiga_project_id}", status_code=202)
async def taiga_webhook(taiga_project_id: int, request: Request,
                        db: Session = Depends(get_db), taiga=Depends(_taiga)):
    link = service.get_link(db, taiga_project_id)
    if link is None or not link.active:
        raise HTTPException(status_code=404, detail="Projekt Taigi nie jest objęty integracją")
    secret = taiga.webhook_secret(taiga_project_id)
    if not secret:
        logger.error("Taiga webhook projektu %s: brak sekretu w konfiguracji", taiga_project_id)
        raise HTTPException(status_code=503, detail="Brak sekretu webhooka w konfiguracji huba")
    raw_body = await request.body()
    if not signature_valid(raw_body, secret, request.headers.get("x-taiga-webhook-signature")):
        logger.warning("Taiga webhook projektu %s: niepoprawny podpis", taiga_project_id)
        raise HTTPException(status_code=403, detail="Niepoprawny podpis")
    try:
        payload = json.loads(raw_body)
    except ValueError:
        raise HTTPException(status_code=400, detail="Treść nie jest poprawnym JSON") from None
    link.last_webhook_at = service._now()
    if isinstance(payload, dict) and payload.get("type") == "test":
        # 2026-11-21: przycisk „testuj” w ustawieniach webhooka Taigi
        db.commit()
        return {"status": "test"}
    try:
        change = from_webhook(payload, hours_name=service._hours_name(link))
    except TaigaPayloadError as exc:
        db.commit()
        raise HTTPException(status_code=400, detail=str(exc)) from None
    if change.project_id is not None and change.project_id != taiga_project_id:
        db.commit()
        raise HTTPException(status_code=400, detail="Projekt w treści różni się od projektu w adresie")
    event, created = service.enqueue(db, taiga_project_id=taiga_project_id, source="webhook",
                                     dedupe_key=service.webhook_dedupe_key(raw_body),
                                     payload=payload, change=change)
    if not created:
        # 2026-11-21: enqueue cofnęło transakcję - zapis czasu ostatniego webhooka osobno
        link = service.get_link(db, taiga_project_id)
        link.last_webhook_at = service._now()
        db.commit()
        return {"status": "duplicate"}
    return {"status": "queued", "event_id": event.id}
