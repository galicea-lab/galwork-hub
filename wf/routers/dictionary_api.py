# wf/routers/dictionary_api.py
# coding: utf-8

"""
Słowniki wyboru dla pól `AsyncSelectWidget`/`TreeSelectWidget` w
formularzach RJSF wygenerowanych migracją Webcon -> RJSF (zob. rjsf.md,
x-webcon.md i spiffworkflow-core/zaleznosci.md) - jeden generyczny
endpoint zamiast osobnego routera na każdy z ~20 słowników wykrytych w
spiffworkflow-core.

Logika źródeł danych (obieg w hubie / Frappe Education / "jeszcze nie
podłączone") jest w wf/services/dictionary_service.py - ten plik tylko
tłumaczy wyjątki domenowe na kody HTTP, jak reszta modułu wf
(ConnectorConfigError -> 503 w signius_api.py/edr_paperless_api.py;
"jeszcze nie podłączone" -> 501, bo to nie błąd konfiguracji, a brak
konektora w ogóle).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from api.dependencies import get_db
from wf.dependencies import get_current_wf_user
from wf.schemas.wf_schemas import CurrentUser, OptionRead
from wf.services.dictionary_service import (
    DictionaryError,
    DictionaryNotConnectedError,
    DictionaryNotFoundError,
    DictionaryNotSeededError,
    list_options,
)

router = APIRouter(prefix="/dictionaries", tags=["dictionaries"])


@router.get("/{slug}", response_model=list[OptionRead])
def get_dictionary_options(
    slug: str,
    q: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_wf_user),
):
    """`GET /v1/wf/dictionaries/{slug}?q=...` - opcje dla jednego pola
    `AsyncSelectWidget` (`ui:options.endpoint` w `*-uischema.json`)."""
    try:
        return list_options(db, slug, q=q, limit=limit)
    except DictionaryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DictionaryNotConnectedError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    except DictionaryNotSeededError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except DictionaryError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
