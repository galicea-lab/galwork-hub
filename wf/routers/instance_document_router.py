# coding: utf-8
# 2026-11-17:  dokumenty sprawy wgrywane przez użytkownika
# (wf/services/instance_documents.py).
"""
Dokumenty instancji procesu.
  * `GET  /process-instances/{id}/documents` - dokumenty sprawy (główny pierwszy),
  * `POST /process-instances/{id}/documents` - wgranie pliku (multipart):
    nowy dokument albo, z `document_id`, kolejna wersja dokumentu sprawy,
  * `GET  /process-instances/{id}/documents/{document_id}` - dokument
    ze wszystkimi wersjami,
  * `GET  /process-instances/{id}/documents/{document_id}/content[?version_no=]`
    - plik po sprawdzeniu skrótu `sha256` (nagłówek `X-Content-SHA256`).

Uprawnienia jak przy wydrukach (instance_print.can_print): uczestnicy
sprawy i administratorzy procesów.
"""
from __future__ import annotations

from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session

from api.dependencies import get_db
from wf.dependencies import get_current_wf_user
from wf.models import ProcessInstance
from wf.schemas.wf_schemas import (
    CurrentUser,
    DocumentDetail,
    DocumentLinkRole,
    DocumentRead,
    ProcessInstanceDocumentRead,
)
from wf.services import instance_documents, instance_print
from wf.services.instance_documents import DocumentError

router = APIRouter(tags=["documents"])


def _http(exc: DocumentError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=str(exc))


def _instance_for(db: Session, instance_id: UUID, user: CurrentUser) -> ProcessInstance:
    instance = instance_print.find_instance(db, instance_id)
    if instance is None:
        raise HTTPException(status_code=404, detail="Instancja procesu nie znaleziona")
    if not instance_print.can_print(instance, user):
        raise HTTPException(status_code=403, detail="Brak uprawnień do dokumentów tej sprawy")
    return instance


@router.get("/process-instances/{instance_id}/documents",
            response_model=list[ProcessInstanceDocumentRead])
def list_instance_documents(instance_id: UUID,
                            db: Session = Depends(get_db),
                            current_user: CurrentUser = Depends(get_current_wf_user)):
    instance = _instance_for(db, instance_id, current_user)
    return instance_documents.list_documents(db, instance)


@router.post("/process-instances/{instance_id}/documents",
             response_model=DocumentRead, status_code=201)
async def upload_instance_document(
        instance_id: UUID,
        file: UploadFile = File(...),
        title: str | None = Form(None),
        role: DocumentLinkRole = Form(DocumentLinkRole.ATTACHMENT),
        document_id: UUID | None = Form(None),
        task_id: UUID | None = Form(None),
        label: str | None = Form(None),
        db: Session = Depends(get_db),
        current_user: CurrentUser = Depends(get_current_wf_user)):
    """Plik jako dokument sprawy. Z `document_id` - nowa wersja istniejącego
    dokumentu (poprzednie wersje zostają); `role` dotyczy tylko nowego."""
    instance = _instance_for(db, instance_id, current_user)
    # odczyt o 1 bajt ponad limit - wykrycie zbyt dużego pliku bez wczytywania całości
    content = await file.read(instance_documents.MAX_SIZE_BYTES + 1)
    try:
        return instance_documents.add_document(
            db, instance, current_user, content=content, file_name=file.filename or '',
            title=title, role=role, document_id=document_id, task_id=task_id, label=label)
    except DocumentError as exc:
        db.rollback()
        raise _http(exc) from exc


@router.get("/process-instances/{instance_id}/documents/{document_id}",
            response_model=DocumentDetail)
def get_instance_document(instance_id: UUID, document_id: UUID,
                          db: Session = Depends(get_db),
                          current_user: CurrentUser = Depends(get_current_wf_user)):
    instance = _instance_for(db, instance_id, current_user)
    try:
        return instance_documents.linked_document(db, instance, document_id)
    except DocumentError as exc:
        raise _http(exc) from exc


@router.get("/process-instances/{instance_id}/documents/{document_id}/content")
def download_instance_document(instance_id: UUID, document_id: UUID,
                               version_no: int | None = None, download: bool = False,
                               db: Session = Depends(get_db),
                               current_user: CurrentUser = Depends(get_current_wf_user)):
    instance = _instance_for(db, instance_id, current_user)
    try:
        document = instance_documents.linked_document(db, instance, document_id)
        result = instance_documents.version_content(db, document, version_no)
    except DocumentError as exc:
        raise _http(exc) from exc
    disposition = 'attachment' if download else 'inline'
    ascii_name = result.file_name.encode('ascii', 'ignore').decode() or 'dokument'
    return Response(content=result.content, media_type=result.mime_type, headers={
        'Content-Disposition': (f"{disposition}; filename=\"{ascii_name}\"; "
                                f"filename*=UTF-8''{quote(result.file_name)}"),
        'X-Content-SHA256': result.sha256,
        'X-Content-Type-Options': 'nosniff',
        'Cache-Control': 'no-store',
    })
