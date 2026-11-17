# coding: utf-8
# 2026-11-16: wydruki PDF z formularzy obiegów (TODO-wydruki.md).
"""
Wydruki PDF instancji procesów.

  * `GET  /process-instances/{id}/prints[?task_id=]` - lista dostępnych
    wydruków (szablony `x-print` + wydruk standardowy),
  * `GET  /process-instances/{id}/print.pdf[?task_id=&template=]` - PDF,
  * `POST /process-instances/{id}/print` - PDF jako dokument sprawy
    (archiwizacja w Paperless-ngx, na żądanie),
  * `POST /print/preview` - podgląd szablonu z edytora areny (administrator
    procesów; dane przykładowe, bez instancji).
"""

from __future__ import annotations

import os
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from api.dependencies import get_db
from wf.dependencies import get_current_wf_user, require_process_admin
from wf.models import ProcessInstance
from wf.schemas.wf_schemas import (
    CurrentUser,
    DocumentRead,
    PrintArchiveRequest,
    PrintOptionList,
    PrintOptionRead,
    PrintPreviewRequest,
)
from wf.services import instance_print, print_service, process_files
from wf.services.process_files import ProcessFileError
from wf.services.process_service import FormRenderError, render_form_json

router = APIRouter(tags=["print"])

# Podgląd HTML z API: bez skryptów i zasobów zewnętrznych.
_HTML_CSP = "default-src 'none'; img-src data:; style-src 'unsafe-inline'; font-src data:; sandbox"


def _http(exc: Exception) -> HTTPException:
    return HTTPException(status_code=getattr(exc, 'status', 422), detail=str(exc))


def _instance_for(db: Session, instance_id: UUID, user: CurrentUser) -> ProcessInstance:
    instance = instance_print.find_instance(db, instance_id)
    if instance is None:
        raise HTTPException(status_code=404, detail="Instancja procesu nie znaleziona")
    if not instance_print.can_print(instance, user):
        raise HTTPException(status_code=403, detail="Brak uprawnień do wydruku tej sprawy")
    return instance


def _pdf_response(content: bytes, file_name: str, *, download: bool) -> Response:
    disposition = 'attachment' if download else 'inline'
    ascii_name = file_name.encode('ascii', 'ignore').decode() or 'wydruk.pdf'
    return Response(content=content, media_type='application/pdf', headers={
        'Content-Disposition': f"{disposition}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(file_name)}",
        'Cache-Control': 'no-store',
    })


@router.get("/process-instances/{instance_id}/prints", response_model=PrintOptionList)
def list_prints(instance_id: UUID, task_id: UUID | None = None,
                db: Session = Depends(get_db),
                current_user: CurrentUser = Depends(get_current_wf_user)):
    instance = _instance_for(db, instance_id, current_user)
    try:
        options = instance_print.available_prints(instance, task_id)
    except print_service.PrintError as exc:
        raise _http(exc) from exc
    return PrintOptionList(items=[PrintOptionRead(**vars(o)) for o in options])


@router.get("/process-instances/{instance_id}/print.pdf")
def print_pdf(instance_id: UUID, task_id: UUID | None = None, template: str | None = None,
              download: bool = False,
              db: Session = Depends(get_db),
              current_user: CurrentUser = Depends(get_current_wf_user)):
    instance = _instance_for(db, instance_id, current_user)
    try:
        result = instance_print.build_print(db, instance, current_user,
                                            task_id=task_id, template=template)
    except print_service.PrintError as exc:
        raise _http(exc) from exc
    return _pdf_response(result.pdf or b'', result.rendered.file_name, download=download)


@router.post("/process-instances/{instance_id}/print", response_model=DocumentRead, status_code=201)
def archive_print(instance_id: UUID, payload: PrintArchiveRequest,
                  db: Session = Depends(get_db),
                  current_user: CurrentUser = Depends(get_current_wf_user)):
    """Generuje PDF i zapisuje go jako dokument sprawy (nowa wersja, jeśli
    wydruk z tego szablonu już istnieje) - dostępny potem m.in. dla kroku
    `dokumenty.glowny` przed podpisem."""
    instance = _instance_for(db, instance_id, current_user)
    try:
        return instance_print.archive_print(db, instance, current_user, task_id=payload.task_id,
                                            template=payload.template, role=payload.role)
    except print_service.PrintError as exc:
        raise _http(exc) from exc


def _read_json(path: str | None, data: dict) -> dict | None:
    if not path:
        return None
    file = process_files.read_file(path)
    if file.kind != process_files.KIND_FORM:
        raise ProcessFileError(f'{path} nie jest plikiem formularza.')
    try:
        return render_form_json(file.content or '{}', data)
    except FormRenderError as exc:
        raise ProcessFileError(str(exc), status=422) from exc


@router.post("/print/preview")
def preview_print(payload: PrintPreviewRequest,
                  db: Session = Depends(get_db),
                  current_user: CurrentUser = Depends(require_process_admin)):
    """Podgląd szablonu (`.html.j2`) z danymi przykładowymi: PDF albo HTML.
    Bez `path` i `content` - wydruk standardowy formularza `schema_path`."""
    root = process_files.files_root()
    sources = []
    name = None
    content = payload.content
    try:
        if payload.path:
            absolute, rel, kind = process_files.resolve_path(payload.path, root=root)
            if kind != process_files.KIND_PRINT:
                raise ProcessFileError('Podgląd dotyczy tylko szablonów wydruku (*.j2).')
            name = os.path.basename(rel)
            if content is None:
                content = process_files.read_file(rel).content
            # szablony obok edytowanego pliku (extends/import w obrębie obiegu)
            sources.append(print_service.dir_source(os.path.dirname(absolute)))
        schema = _read_json(payload.schema_path, payload.data)
        ui_schema = _read_json(payload.uischema_path, payload.data)
    except ProcessFileError as exc:
        raise _http(exc) from exc
    sources += print_service.template_sources(None, root)
    meta = {**print_service.organization_meta(),
            'data': print_service.polish_date(), 'wygenerowano': 'podgląd',
            'znak': 'PODGLĄD/1/2026', 'tytul': name or print_service.DEFAULT_LABEL,
            'proces': 'Podgląd wydruku', 'instancja_tytul': name or '', 'zadanie': '',
            'uzytkownik': current_user.display_name}
    try:
        rendered = print_service.render_html(
            sources=sources, template=None, data=payload.data, meta=meta,
            schema=schema, ui_schema=ui_schema, db=db, template_source=content)
        if payload.format == 'html':
            return Response(content=rendered.html, media_type='text/html; charset=utf-8',
                            headers={'Content-Security-Policy': _HTML_CSP, 'Cache-Control': 'no-store'})
        pdf = print_service.html_to_pdf(rendered.html)
    except print_service.PrintError as exc:
        raise _http(exc) from exc
    return _pdf_response(pdf, rendered.file_name, download=False)

