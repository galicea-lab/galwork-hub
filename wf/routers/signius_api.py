# coding: utf-8
"""
Integracja podpisu elektronicznego (Signius) z korespondencją studencką
przechowywaną w Frappe Education (`Student Correspondence`), zaczepiona
o instancje procesów BPMN w tym hubie.

Kontekst architektoniczny (2026-10-09):
  * Frappe Education jest systemem rekordu dla treści/dokumentów sprawy
    (`connector/frappe_education`) - hub trzyma tylko identyfikator
    korelacyjny (`ProcessInstance.business_key` = nazwa dokumentu Frappe,
    np. "EDU-COR-2026-00001"; odwrotnie, Frappe ma pole
    `process_instance_id` = `ProcessInstance.id`).
  * Signius (`connector/signius`) jest oddzielnym systemem podpisu - ani
    Frappe, ani Spiff nie przechowują treści certyfikatu/klucza, tylko
    identyfikatory `signius_folder_id`/`signius_document_id` (zapisywane
    we Frappe przez whitelisted `record_signius_document`/`update_status`
    w `.../student_correspondence/student_correspondence.py`).

WAŻNE OGRANICZENIE: `wf/engine.py` (zob. komentarz na początku tego pliku)
NIE wykonuje `serviceTask`/`scriptTask` - `script_engine` jest wyłączony,
silnik rozumie tylko `userTask`/`manualTask`. W BPMN nie da się więc dziś
wymodelować "Service Task: poproś o podpis", który silnik sam by wywołał.
Te endpointy są więc - jak `wf/routers/edr_api.py` dla e-Doręczeń - akcjami
wywoływanymi z zewnątrz (np. przyciskiem w UI po zakończeniu zadania
człowieka "Zatwierdź i wyślij do podpisu", albo z n8n), a nie krokiem,
który silnik wykonuje automatycznie. Rozszerzenie silnika o prawdziwe
service taski to osobne zadanie inżynieryjne, nie zrobione tutaj.

Signius w tym kliencie nie ma webhooka (zob. `connector/signius/connector.py`)
- `check_signature_status` trzeba więc odpytywać (przycisk "Odśwież status"
w UI, albo cron/n8n - `connector/n8n` już istnieje w hubie).

Endpointy wymagają tokenu Keycloak (`get_current_wf_user`) - tak jak reszta
modułu wf; nie ma tu żadnego anonimowego callbacku (w przeciwieństwie do
edr_api.py), więc router jest od razu montowany w `router.py`.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.dependencies import get_db
from connector.base import ConnectorConfigError, ConnectorError
from connector.frappe_education.connector import get_connector as get_frappe_connector
from connector.signius.connector import get_connector as get_signius_connector
from wf.dependencies import get_current_wf_user
from wf.models import ProcessInstance
from wf.schemas.wf_schemas import (
    CurrentUser,
    EventType,
    SigniusRequestSignatureRequest,
    SigniusStatusRead,
)
from wf.services.process_service import get_service

router = APIRouter(prefix="/signius", tags=["signius"])

CORRESPONDENCE_DOCTYPE = "Student Correspondence"


def _get_instance_or_404(db: Session, instance_id: UUID) -> ProcessInstance:
    instance = db.execute(
        select(ProcessInstance).where(ProcessInstance.id == instance_id)
    ).scalars().first()
    if instance is None:
        raise HTTPException(status_code=404, detail="Instancja procesu nie znaleziona")
    return instance


def _business_key_or_409(instance: ProcessInstance) -> str:
    if not instance.business_key:
        raise HTTPException(
            status_code=409,
            detail="Instancja procesu nie ma ustawionego business_key - nie można "
                   "zlokalizować sprawy (Student Correspondence) we Frappe",
        )
    return instance.business_key


def _get_correspondence_or_404(business_key: str) -> dict:
    try:
        return get_frappe_connector().get_doc(CORRESPONDENCE_DOCTYPE, business_key)
    except ConnectorConfigError as exc:
        raise HTTPException(status_code=503, detail=f"Frappe Education nie skonfigurowane: {exc}")
    except ConnectorError as exc:
        raise HTTPException(status_code=404,
                            detail=f"Nie znaleziono sprawy {business_key} we Frappe: {exc}")


def _newest_pdf_attachment(business_key: str) -> tuple[str, bytes]:
    """Nazwa i treść najnowszego PDF-a dołączonego do sprawy - dokument do
    podpisania. Nie generujemy PDF-a z `description`/`form_data` - sprawa
    musi mieć wgrany załącznik (Print Format -> PDF to osobne zadanie,
    niezrobione tutaj; na razie dziekanat wgrywa PDF ręcznie przed
    kliknięciem "wyślij do podpisu")."""
    connector = get_frappe_connector()
    try:
        files = connector.get_file_list(CORRESPONDENCE_DOCTYPE, business_key)
    except ConnectorError as exc:
        raise HTTPException(status_code=502, detail=f"Frappe Education: {exc}")
    pdf_files = [f for f in files if f.get("file_name", "").lower().endswith(".pdf")]
    if not pdf_files:
        raise HTTPException(
            status_code=409,
            detail=f"Sprawa {business_key} nie ma załączonego PDF-a do podpisania - "
                   "wgraj dokument we Frappe przed wysłaniem do podpisu",
        )
    newest = pdf_files[0]  # get_file_list zwraca już order_by=creation desc
    try:
        content = connector.get_file_content(newest["file_url"])
    except ConnectorError as exc:
        raise HTTPException(status_code=502, detail=f"Frappe Education: {exc}")
    return newest["file_name"], content


def _status_read(instance: ProcessInstance, doc: dict) -> SigniusStatusRead:
    return SigniusStatusRead(
        process_instance_id=instance.id,
        business_key=instance.business_key,
        signature_status=doc.get("signature_status") or "Not Required",
        signius_folder_id=doc.get("signius_folder_id") or None,
        signius_document_id=doc.get("signius_document_id") or None,
        signed_document_url=doc.get("signed_document") or None,
    )


@router.post("/process-instances/{instance_id}/request-signature", response_model=SigniusStatusRead)
def request_signature(
    instance_id: UUID,
    payload: SigniusRequestSignatureRequest,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_wf_user),
):
    """Wgrywa PDF sprawy do Signius i wysyła prośbę o podpis.

    Jeden folder Signius per sprawa (tworzony przy pierwszym wywołaniu,
    identyfikator trzymany we Frappe jako `signius_folder_id`) - prostsze
    niż współdzielenie folderu między sprawami, kosztem większej liczby
    folderów po stronie Signius."""
    instance = _get_instance_or_404(db, instance_id)
    business_key = _business_key_or_409(instance)
    doc = _get_correspondence_or_404(business_key)

    if doc.get("signature_status") == "Signed":
        raise HTTPException(status_code=409, detail="Sprawa jest już podpisana")
    if doc.get("signature_status") == "Pending":
        raise HTTPException(
            status_code=409,
            detail="Podpis już zażądany dla tej sprawy (status Pending) - sprawdź "
                   "/status zamiast wysyłać kolejne żądanie (uniknięcie duplikatu "
                   "dokumentu w Signius)",
        )

    filename, pdf_bytes = _newest_pdf_attachment(business_key)

    signius = get_signius_connector()
    try:
        folder_id = doc.get("signius_folder_id")
        if not folder_id:
            folder = signius.create_folder(business_key)
            folder_id = folder["documentFolderId"]
        uploaded = signius.upload_document(folder_id, filename, pdf_bytes)
        document_id = uploaded["documentId"]
        signius.request_signature(
            document_id, payload.signee_email, payload.signee_phone,
            title=doc.get("subject") or business_key, qualified=payload.qualified,
        )
    except ConnectorConfigError as exc:
        raise HTTPException(status_code=503, detail=f"Signius nie skonfigurowany: {exc}")
    except ConnectorError as exc:
        raise HTTPException(status_code=502, detail=f"Signius: {exc}")

    frappe = get_frappe_connector()
    try:
        frappe.call_method(
            "education.education.doctype.student_correspondence."
            "student_correspondence.record_signius_document",
            {"name": business_key, "folder_id": folder_id, "document_id": document_id},
            http_method="POST",
        )
    except ConnectorError as exc:
        raise HTTPException(status_code=502, detail=f"Frappe Education: {exc}")

    get_service().record_event(
        db, instance, EventType.SIGNATURE_REQUESTED, actor=current_user,
        payload={"business_key": business_key, "signius_document_id": document_id,
                 "signee_email": payload.signee_email},
    )

    doc.update({
        "signius_folder_id": folder_id,
        "signius_document_id": document_id,
        "signature_status": "Pending",
    })
    return _status_read(instance, doc)


@router.get("/process-instances/{instance_id}/status", response_model=SigniusStatusRead)
def check_signature_status(
    instance_id: UUID,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_wf_user),
):
    """Odpytuje Signius o status (brak webhooka w tym API) i - jeśli dokument
    jest już podpisany - ściąga podpisany PDF i odkłada go we Frappe
    (`signed_document`), zamykając pętlę podpisu."""
    instance = _get_instance_or_404(db, instance_id)
    business_key = _business_key_or_409(instance)
    doc = _get_correspondence_or_404(business_key)

    folder_id = doc.get("signius_folder_id")
    document_id = doc.get("signius_document_id")
    if doc.get("signature_status") != "Pending" or not folder_id or not document_id:
        return _status_read(instance, doc)

    signius = get_signius_connector()
    frappe = get_frappe_connector()
    try:
        signius_status = signius.get_document_status(document_id, folder_id)
    except ConnectorConfigError as exc:
        raise HTTPException(status_code=503, detail=f"Signius nie skonfigurowany: {exc}")
    except ConnectorError as exc:
        raise HTTPException(status_code=502, detail=f"Signius: {exc}")

    if signius_status != "SIGNED":
        return _status_read(instance, doc)

    try:
        signed_bytes = signius.get_signed_document(document_id)
        upload = frappe.upload_file(
            CORRESPONDENCE_DOCTYPE, business_key, f"{business_key}-podpisano.pdf",
            signed_bytes, is_private=True, fieldname="signed_document",
            content_type="application/pdf",
        )
        frappe.call_method(
            "education.education.doctype.student_correspondence."
            "student_correspondence.update_status",
            {"name": business_key, "status": "Signed", "signature_status": "Signed"},
            http_method="POST",
        )
    except ConnectorError as exc:
        raise HTTPException(status_code=502, detail=f"Frappe Education/Signius: {exc}")

    get_service().record_event(
        db, instance, EventType.SIGNATURE_COMPLETED, actor=current_user,
        payload={"business_key": business_key, "signius_document_id": document_id},
    )

    doc.update({
        "signature_status": "Signed",
        "signed_document": upload.get("file_url"),
    })
    return _status_read(instance, doc)
