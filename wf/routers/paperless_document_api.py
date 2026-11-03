# wf/routers/paperless_document_api.py
# coding: utf-8
"""
Kolejka dokumentów z Paperless-ngx filtrowana po tagu - widget na pulpicie
galwork-dashboard (2026-10-18, zob. docstring modułu wf/services/paperless_document_service.py).

Tylko odczyt - nic tu nie wgrywa/modyfikuje dokumentów (to robi n8n po
stronie Paperless), więc wystarczy zwykłe `get_current_wf_user`, jak
`/dictionaries`/`/tasks/my`.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from connector.base import ConnectorConfigError, ConnectorError
from wf.dependencies import get_current_wf_user
from wf.schemas.wf_schemas import CurrentUser, PaperlessDocumentPage
from wf.services import paperless_document_service

router = APIRouter(prefix="/paperless", tags=["paperless"])


@router.get("/documents", response_model=PaperlessDocumentPage)
def get_documents_by_tag(
    tag: str = Query(..., min_length=1, max_length=200),
    limit: int = Query(default=25, ge=1, le=100),
    _current_user: CurrentUser = Depends(get_current_wf_user),
):
    """`GET /v1/wf/paperless/documents?tag=...&limit=...`"""
    try:
        return paperless_document_service.list_by_tag(tag, limit=limit)
    except ConnectorConfigError as exc:
        raise HTTPException(status_code=503, detail=f"Paperless nie skonfigurowany: {exc}") from exc
    except ConnectorError as exc:
        raise HTTPException(status_code=502, detail=f"Paperless: {exc}") from exc
