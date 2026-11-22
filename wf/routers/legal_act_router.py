# coding: utf-8
"""
Akty wewnętrzne.
  * `GET /acts?q=&limit=` - opcje wyboru aktu (pola "Zmienia akty",
    "Uchyla akty" w formularzach; `AsyncSelectWidget`),
  * `GET /acts/{document_id}?on=RRRR-MM-DD` - status prawny na dzień,
    redakcje (wersje z datami obowiązywania) i relacje z innymi aktami,
  * `GET /acts/{document_id}/content?on=&version_no=&download=` - plik redakcji
    (domyślnie obowiązującej w dniu `on`; `version_no` wybiera wersję z historii).

Akty z "Ograniczeniami dostępu" widzą tylko uczestnicy sprawy repozytorium
i administratorzy procesów; pozostałe - każdy zalogowany użytkownik.
"""
from __future__ import annotations

from datetime import date
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from api.dependencies import get_db
from wf.dependencies import get_current_wf_user
from wf.models import Document
from wf.schemas.wf_schemas import ActStateRead, CurrentUser, OptionRead
from wf.services import instance_documents, instance_print, legal_acts
from wf.services.instance_documents import DocumentError

router = APIRouter(prefix="/acts", tags=["acts"])


def _can_see(user: CurrentUser):
    return lambda instance: instance_print.can_print(instance, user)


@router.get("", response_model=list[OptionRead])
def list_act_options(q: str | None = Query(default=None, max_length=200),
                     limit: int = Query(default=50, ge=1, le=500),
                     db: Session = Depends(get_db),
                     current_user: CurrentUser = Depends(get_current_wf_user)):
    return legal_acts.act_options(db, q, limit, can_see_restricted=_can_see(current_user))


@router.get("/{document_id}", response_model=ActStateRead)
def get_act_state(document_id: UUID, on: date | None = None,
                  db: Session = Depends(get_db),
                  current_user: CurrentUser = Depends(get_current_wf_user)):
    document = db.get(Document, document_id)
    instance = legal_acts.repository_instance(db, document_id) if document is not None else None
    if instance is None:
        raise HTTPException(status_code=404, detail="Akt nie jest zarejestrowany w repozytorium")
    data = legal_acts.latest_form_data(db, instance)
    if legal_acts.is_restricted(data) and not instance_print.can_print(instance, current_user):
        raise HTTPException(status_code=404, detail="Akt nie jest zarejestrowany w repozytorium")
    return legal_acts.act_state(db, document, on)


# treść aktu do podglądu/pobrania w modalu statusu (bez instanceId).
@router.get("/{document_id}/content")
def download_act_content(document_id: UUID, on: date | None = None,
                         version_no: int | None = Query(default=None, ge=1),
                         download: bool = False,
                         db: Session = Depends(get_db),
                         current_user: CurrentUser = Depends(get_current_wf_user)):
    document = db.get(Document, document_id)
    instance = legal_acts.repository_instance(db, document_id) if document is not None else None
    if instance is None:
        raise HTTPException(status_code=404, detail="Akt nie jest zarejestrowany w repozytorium")
    data = legal_acts.latest_form_data(db, instance)
    if legal_acts.is_restricted(data) and not instance_print.can_print(instance, current_user):
        raise HTTPException(status_code=404, detail="Akt nie jest zarejestrowany w repozytorium")

    state = legal_acts.act_state(db, document, on)
    if version_no is None:
        version_no = state.valid_version_no
        if version_no is None and state.versions:
            version_no = max(v.version_no for v in state.versions)

    try:
        result = instance_documents.version_content(db, document, version_no)
    except DocumentError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc

    resolved_version_no = version_no or document.current_version_no
    effective_from = next((v.effective_from for v in state.versions
                           if v.version_no == resolved_version_no), None)
    ascii_name = result.file_name.encode('ascii', 'ignore').decode() or 'dokument'
    disposition = 'attachment' if download else 'inline'
    headers = {
      'Content-Disposition': (f"{disposition}; filename=\"{ascii_name}\"; "
                              f"filename*=UTF-8''{quote(result.file_name)}"),
      'X-Content-SHA256': result.sha256,
      'X-Content-Type-Options': 'nosniff',
      'Cache-Control': 'no-store',
      'X-Version-No': str(resolved_version_no),
    }
    if effective_from:
        headers['X-Effective-From'] = str(effective_from)
    return Response(content=result.content, media_type=result.mime_type, headers=headers)
