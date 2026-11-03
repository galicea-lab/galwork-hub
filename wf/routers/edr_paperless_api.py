# coding: utf-8
"""
Router akcji dla e-Doręczeń przez Paperless-ngx - alternatywa dla WEBCON,
zob. `analiza_edr.md` i `connector/edoreczenia/edr_paperless.py` (tam jest
cała logika biznesowa; ten moduł tylko ją wystawia jako HTTP, tak jak
`wf/routers/signius_api.py` dla podpisu elektronicznego).

W przeciwieństwie do starego `wf/routers/edr_api.py` (WEBCON: `/ade*` +
`*Callback` bez autoryzacji, bo wywołuje je e-Delivery) ten router NIE ma
żadnego anonimowego endpointu - wszystko za tokenem Keycloak
(`get_current_wf_user`), jak reszta modułu `wf`. Montowany od razu (jak
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
    `galwork-dashboard`.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.dependencies import get_db
from connector.base import ConnectorConfigError, ConnectorError
from connector.edoreczenia import edr_paperless
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
