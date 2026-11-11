# coding: utf-8
"""
Router akcji dla e-Doręczeń przez Paperless-ngx.

Wszystko za tokenem Keycloak (`get_current_wf_user`), jak reszta modułu `wf`. Montowany od razu (jak
`signius_api.py`), nie czeka na decyzję wdrożeniową.

Co jest tu akcją (endpoint), a co zostaje cronem (zob. docstring
`edr_paperless.py`, sekcja "Czego ten moduł NIE robi"):
  * `POST /sync-inbox` - wygodne "odśwież teraz" z UI; NIE zastępuje crona,
    bo ingest do Paperless-ngx jest asynchroniczny i tak musi być
    dociągany osobno (`submit_staged_documents`/`poll_submitted_documents`
    - te dwa kroki NIE są tu wystawione jako endpointy, to plumbing
    systemowy, nie akcja użytkownika),
  * `POST /process-instances/{id}/refresh-evidences` i
    `.../send` to prawdziwe akcje per sprawa - przyciski w
    `galwork-dashboard`,
  * `GET /ade*` - wyszukiwanie adresata w Bazie Adresów Elektronicznych
    (krok "Przygotowanie przesyłki" procesu `edr-wysylka`).
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.dependencies import get_db
from connector.base import ConnectorConfigError, ConnectorError
from connector.edoreczenia import ade_search, edr_paperless
from connector.edoreczenia.edr_connector import EdrConnector
from connector.paperless.connector import get_connector as get_paperless_connector
from wf.dependencies import get_current_wf_user
from wf.models import ProcessInstance
from wf.schemas.wf_schemas import (
    CurrentUser,
    EdrFetchEvidencesResponse,
    EdrSendRequest,
    EdrSendResponse,
    EdrSyncInboxResponse,
)

router = APIRouter(prefix="/edr", tags=["edr"])


def _get_instance_or_404(db: Session, instance_id: UUID) -> ProcessInstance:
    instance = db.execute(
        select(ProcessInstance).where(ProcessInstance.id == instance_id)
    ).scalars().first()
    if instance is None:
        raise HTTPException(status_code=404, detail="Instancja procesu nie znaleziona")
    return instance


@router.post("/sync-inbox", response_model=EdrSyncInboxResponse)
def sync_inbox(test: bool = False, db: Session = Depends(get_db),
               current_user: CurrentUser = Depends(get_current_wf_user)):
    """Pobiera nowe wiadomości z EDR i startuje dla nich instancje procesu
    `edr-korespondencja` (musi być opublikowana - zob.
    `scripts/seed_edr_process.py`)."""
    try:
        instances = edr_paperless.sync_inbox(
            db, get_paperless_connector(), actor=current_user, test=test)
    except ConnectorConfigError as exc:
        raise HTTPException(status_code=503, detail=f"Paperless-ngx nie skonfigurowany: {exc}")
    except ConnectorError as exc:
        raise HTTPException(status_code=502, detail=f"EDR/Paperless-ngx: {exc}")
    return EdrSyncInboxResponse(
        created=len(instances), process_instance_ids=[instance.id for instance in instances])


@router.post("/process-instances/{instance_id}/refresh-evidences",
             response_model=EdrFetchEvidencesResponse)
def refresh_evidences(instance_id: UUID, test: bool = False, db: Session = Depends(get_db),
                       current_user: CurrentUser = Depends(get_current_wf_user)):
    """Dociąga potwierdzenia (evidences) dla JEDNEJ sprawy na żądanie -
    zob. `edr_paperless.fetch_evidences_for_instance`."""
    instance = _get_instance_or_404(db, instance_id)
    if not instance.business_key:
        raise HTTPException(
            status_code=409, detail="Instancja nie ma messageId (business_key) - nie da "
                                     "się odpytać EDR o potwierdzenia")
    try:
        edrc = EdrConnector(test)
        fetched = edr_paperless.fetch_evidences_for_instance(
            db, get_paperless_connector(), instance, edrc, actor=current_user)
        db.commit()
        # 2026-11-08: przesyłka edr-wysylka z dowodem doręczenia - zamknięcie
        # "Potwierdzenia doręczenia" (jak w cronie scripts/edr.py evidences).
        edr_paperless.complete_delivered(db, actor=current_user, instance_id=instance.id)
    except ConnectorConfigError as exc:
        raise HTTPException(status_code=503, detail=f"Paperless-ngx nie skonfigurowany: {exc}")
    except ConnectorError as exc:
        raise HTTPException(status_code=502, detail=f"EDR/Paperless-ngx: {exc}")
    return EdrFetchEvidencesResponse(fetched=fetched)


@router.post("/process-instances/{instance_id}/send", response_model=EdrSendResponse)
def send_document(instance_id: UUID, payload: EdrSendRequest, db: Session = Depends(get_db),
                   current_user: CurrentUser = Depends(get_current_wf_user)):
    """Wysyła główny dokument sprawy przez EDR - zob.
    `edr_paperless.send_document` (wymaga `ade_to` w `instance.summary` i
    zaingestowanego do Paperless-ngx dokumentu głównego, inaczej 409)."""
    instance = _get_instance_or_404(db, instance_id)
    try:
        message_id = edr_paperless.send_document(
            db, get_paperless_connector(), instance, actor=current_user, test=payload.test)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=f"EDR: {exc}")
    except ConnectorConfigError as exc:
        raise HTTPException(status_code=503, detail=f"Paperless-ngx nie skonfigurowany: {exc}")
    except ConnectorError as exc:
        raise HTTPException(status_code=502, detail=f"EDR/Paperless-ngx: {exc}")
    return EdrSendResponse(process_instance_id=instance.id, message_id=message_id)


# ----------------------------------------------------------------------
# 2026-11-08: wyszukiwanie adresów do doręczeń (Baza Adresów
# Elektronicznych), przeniesione z dawnego wf/routers/edr_api.py. Logika
# w connector/edoreczenia/ade_search.py. Błędy jak w reszcie routera:
# brak adresata -> 404, złe parametry -> 422, EDR -> 502.
# ----------------------------------------------------------------------

def _ade_call(fn, *args, test: bool = False, **kwargs):
    try:
        return fn(EdrConnector(test), *args, **kwargs)
    except ade_search.AdeNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except ConnectorConfigError as exc:
        raise HTTPException(status_code=503, detail=f"EDR nie skonfigurowany: {exc}")
    except Exception as exc:  # klient EDR zgłasza też wyjątki spoza ConnectorError
        raise HTTPException(status_code=502, detail=f"EDR: {exc}")


@router.get("/ade", response_model=dict)
def find_by_ade(ade: str = Query(..., min_length=1), test: bool = False,
                _current_user: CurrentUser = Depends(get_current_wf_user)):
    """Dane podmiotu o podanym adresie do doręczeń elektronicznych."""
    return _ade_call(ade_search.find_by_ade, ade, test=test)


@router.get("/ade/person", response_model=list[dict])
def find_person(firstname: str = Query(..., min_length=1), surname: str = Query(..., min_length=1),
                pesel: str = '', city: str = '', buildingNumber: str = '', test: bool = False,
                _current_user: CurrentUser = Depends(get_current_wf_user)):
    """Osoby fizyczne po imieniu i nazwisku (opcjonalnie PESEL/adres)."""
    return _ade_call(ade_search.find_person, firstname, surname, pesel, city,
                     buildingNumber, test=test)


@router.get("/ade/inst", response_model=dict)
def find_institution_by_id(pesel: str = '', nip: str = '', regon: str = '', krs: str = '',
                           test: bool = False,
                           _current_user: CurrentUser = Depends(get_current_wf_user)):
    """Instytucja po identyfikatorze urzędowym (NIP, REGON, KRS, PESEL)."""
    return _ade_call(ade_search.find_institution_by_id, pesel=pesel, nip=nip, regon=regon,
                     krs=krs, test=test)


@router.get("/ade/insta", response_model=dict)
def find_institution_by_address(name: str = '', city: str = '', buildingNumber: str = '',
                                street: str = '', flatNumber: str = '', postalCode: str = '',
                                test: bool = False,
                                _current_user: CurrentUser = Depends(get_current_wf_user)):
    """Instytucja po nazwie i adresie."""
    return _ade_call(ade_search.find_institution_by_address, name=name, city=city,
                     building_number=buildingNumber, street=street, flat_number=flatNumber,
                     postal_code=postalCode, test=test)
