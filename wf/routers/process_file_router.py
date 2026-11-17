# coding: utf-8
# 2026-11-15: API plików obiegów dla edytorów areny (BPMN, formularze,
# szablony wydruków) - zastępuje galwork-arena/server/bpmn-files.mjs.
"""
Odczyt i zapis plików w katalogu obiegów (wf/services/process_files.py).

Wszystkie endpointy wymagają grupy administracyjnej (require_process_admin) -
także odczyt, jak dotąd przez hasło nginx przed serwerem plików areny.

Zapis nie tworzy wersji obiegu w bazie: wersję `draft` tworzy jak dotąd
`POST /process-models/sync-from-disk` albo `POST .../versions`.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from wf.dependencies import require_process_admin
from wf.schemas.wf_schemas import (
    CurrentUser,
    ProcessFileContentRead,
    ProcessFileKind,
    ProcessFileList,
    ProcessFileSaved,
    ProcessFileWrite,
    ProcessFormPairList,
)
from wf.services import process_files
from wf.services.process_files import ProcessFileError

router = APIRouter(prefix="/process-files", tags=["process-files"])

# Nazwa katalogu zwracana klientowi zamiast ścieżki na serwerze.
_ROOT_LABEL = "spiffworkflow"


def _http(exc: ProcessFileError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=str(exc))


@router.get("", response_model=ProcessFileList)
def list_process_files(kind: ProcessFileKind | None = None,
                       current_user: CurrentUser = Depends(require_process_admin)):
    try:
        files = process_files.list_files(kind.value if kind else None)
    except ProcessFileError as exc:
        raise _http(exc) from exc
    return ProcessFileList(root=_ROOT_LABEL, files=files)


@router.get("/forms", response_model=ProcessFormPairList)
def list_form_pairs(current_user: CurrentUser = Depends(require_process_admin)):
    return ProcessFormPairList(root=_ROOT_LABEL, forms=process_files.list_form_pairs())


@router.get("/content", response_model=ProcessFileContentRead)
def read_process_file(path: str, current_user: CurrentUser = Depends(require_process_admin)):
    try:
        file = process_files.read_file(path)
    except ProcessFileError as exc:
        raise _http(exc) from exc
    return ProcessFileContentRead(path=file.path, kind=file.kind, content=file.content,
                                  sha256=file.sha256)


@router.put("/content", response_model=ProcessFileSaved)
def write_process_file(payload: ProcessFileWrite,
                       current_user: CurrentUser = Depends(require_process_admin)):
    try:
        result = process_files.write_file(payload.path, payload.content, payload.base_sha256,
                                          current_user)
    except ProcessFileError as exc:
        raise _http(exc) from exc
    return ProcessFileSaved(path=result.path, kind=result.kind, sha256=result.sha256,
                            created=result.created, warnings=result.warnings,
                            committed=result.committed, commit=result.commit,
                            git_error=result.git_error)
