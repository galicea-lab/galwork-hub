# coding: utf-8

"""
Router API dla definicji procesów (ProcessModel / ProcessModelVersion).

Realizuje uzupełnienie api poprzez zbudowanie odpowiednich
routerów na podstawie wf/schemas i wf/models. Zbudowany na wzór
pm/routers/projects.py (sesja synchroniczna SQLAlchemy przez api.dependencies.get_db).

2026-10-05: modele w wf/models/wf.py przestawiono na typy przenośne
(Uuid, JSON z wariantem JSONB) i zarejestrowano w alembic/env.py, więc tabele
da się utworzyć zarówno na PostgreSQL, jak i na SQLite (TODO.md pkt 1/7).
Publikowanie wersji liczy `content_hash` z plików BPMN - ten sam plik jest
potem parsowany przez wf/engine.py przy starcie instancji.

2026-10-05: wszystkie endpointy wymagają zalogowania (get_current_wf_user),
a zapis definicji dodatkowo grupy administracyjnej z `keycloak.admin_groups`
(require_process_admin) - wcześniej definicje procesów były dostępne bez tokenu.

2026-10-06: `GET /process-models/{key}/start-form` zwraca formularz startowy
(startEvent), a tworzenie wersji zapisuje indeks formularzy w `spec_json`
(patrz wf/services/process_service.py: build_spec_json).

2026-10-17: `POST /process-models/sync-from-disk` - skanuje CAŁE drzewo
spiffworkflow/ i tworzy/aktualizuje wersje `draft` dla wszystkich obiegów
na raz (wf/services/filesystem_sync.py) - odpowiednik CLI
scripts/seed_process_model.py --sync-all, dla przycisku "Synchronizuj z
dysku" w Narzędziach twórcy (galwork-dashboard). Admin-only
(require_process_admin) i NIGDY nie publikuje automatycznie.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.dependencies import get_db
from wf.dependencies import get_current_wf_user, require_process_admin
from wf.models import ProcessModel, ProcessModelFile, ProcessModelVersion
from wf.schemas.wf_schemas import (
    ProcessFormRead,
    ProcessModelCreate,
    ProcessModelFileContent,
    ProcessModelRead,
    ProcessModelSyncRequest,
    ProcessModelSyncResult,
    ProcessModelUpdate,
    ProcessModelVersionCreate,
    ProcessModelVersionRead,
    ProcessModelVersionStatus,
    CurrentUser,
)
from wf.services.filesystem_sync import sync_all as sync_process_models
from wf.services.process_service import (
    FormRenderError,
    ProcessServiceError,
    build_spec_json,
    get_service,
)

router = APIRouter(prefix="/process-models", tags=["process-models"])


def _get_process_model_or_404(db: Session, process_model_id: UUID) -> ProcessModel:
    model = db.get(ProcessModel, process_model_id)
    if model is None:
        raise HTTPException(status_code=404, detail="Definicja procesu nie znaleziona")
    return model


@router.get("", response_model=list[ProcessModelRead])
def list_process_models(active_only: bool = True, db: Session = Depends(get_db),
                         current_user: CurrentUser = Depends(get_current_wf_user)):
    stmt = select(ProcessModel).order_by(ProcessModel.name)
    if active_only:
        stmt = stmt.where(ProcessModel.is_active.is_(True))
    return db.execute(stmt).scalars().all()


@router.post("", response_model=ProcessModelRead, status_code=201)
def create_process_model(payload: ProcessModelCreate, db: Session = Depends(get_db),
                         current_user: CurrentUser = Depends(require_process_admin)):
    if db.execute(select(ProcessModel).where(ProcessModel.key == payload.key)).scalars().first():
        raise HTTPException(status_code=409, detail=f"Proces o kluczu '{payload.key}' już istnieje")
    model = ProcessModel(**payload.model_dump())
    db.add(model)
    db.commit()
    db.refresh(model)
    return model


@router.post("/sync-from-disk", response_model=list[ProcessModelSyncResult])
def sync_process_models_from_disk(payload: ProcessModelSyncRequest | None = None,
                                   db: Session = Depends(get_db),
                                   current_user: CurrentUser = Depends(require_process_admin)):
    """Skanuje CAŁE drzewo spiffworkflow/ i tworzy/aktualizuje wersje
    `draft` dla każdego obiegu, którego pliki się zmieniły od ostatniej
    synchronizacji (po `content_hash`) - zob. docstring
    wf/services/filesystem_sync.py. NIGDY nie publikuje automatycznie;
    publikacja nowo utworzonych wersji draft to osobny, ręczny krok
    (POST .../versions/{id}/publish, jak zawsze)."""
    dry_run = bool(payload and payload.dry_run)
    return sync_process_models(db, dry_run=dry_run)


@router.get("/{process_model_id}", response_model=ProcessModelRead)
def get_process_model(process_model_id: UUID, db: Session = Depends(get_db),
                      current_user: CurrentUser = Depends(get_current_wf_user)):
    return _get_process_model_or_404(db, process_model_id)


@router.patch("/{process_model_id}", response_model=ProcessModelRead)
def update_process_model(process_model_id: UUID, payload: ProcessModelUpdate,
                          db: Session = Depends(get_db),
                          current_user: CurrentUser = Depends(require_process_admin)):
    model = _get_process_model_or_404(db, process_model_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(model, key, value)
    db.commit()
    db.refresh(model)
    return model


@router.get("/{process_model_id}/versions", response_model=list[ProcessModelVersionRead])
def list_process_model_versions(process_model_id: UUID, db: Session = Depends(get_db),
                                current_user: CurrentUser = Depends(get_current_wf_user)):
    _get_process_model_or_404(db, process_model_id)
    stmt = (select(ProcessModelVersion)
            .where(ProcessModelVersion.process_model_id == process_model_id)
            .order_by(ProcessModelVersion.version.desc()))
    return db.execute(stmt).scalars().all()


@router.get("/{process_model_id}/versions/{version_id}/files/{file_id}",
            response_model=ProcessModelFileContent)
def get_process_model_file_content(process_model_id: UUID, version_id: UUID, file_id: UUID,
                                   db: Session = Depends(get_db),
                                   current_user: CurrentUser = Depends(get_current_wf_user)):
    """Treść jednego pliku wersji (BPMN/JSON Schema/UI Schema).

    2026-10-17: `ProcessModelFileRead` (lista plików w `GET .../versions`)
    celowo nie ma `content` - zob. docstring w wf/schemas/wf_schemas.py
    ("Bez treści – do list. Treść pobiera się osobnym endpointem."), ale
    tego "osobnego endpointu" jeszcze nie było. Potrzebny dla podglądu
    formularza w galwork-dashboard (Narzędzia twórcy -> Testuj formularz) -
    pozwala wyciągnąć surowy JSON Schema/UI Schema konkretnej wersji bez
    konieczności zasiewania/startowania procesu."""
    version = db.get(ProcessModelVersion, version_id)
    if version is None or version.process_model_id != process_model_id:
        raise HTTPException(status_code=404, detail="Wersja procesu nie znaleziona")
    file = db.get(ProcessModelFile, file_id)
    if file is None or file.version_id != version_id:
        raise HTTPException(status_code=404, detail="Plik nie znaleziony")
    return file


@router.post("/{process_model_id}/versions", response_model=ProcessModelVersionRead, status_code=201)
def create_process_model_version(process_model_id: UUID, payload: ProcessModelVersionCreate,
                                  db: Session = Depends(get_db),
                                  current_user: CurrentUser = Depends(require_process_admin)):
    """Tworzy nową wersję roboczą (status=draft) z plikami BPMN/DMN/schema.
    sha256 i content_hash liczone po stronie serwera, zgodnie z komentarzem
    w ProcessModelFileRead ("Bez treści ... sha256 liczy serwer")."""
    _get_process_model_or_404(db, process_model_id)
    last_version = db.execute(
        select(ProcessModelVersion.version)
        .where(ProcessModelVersion.process_model_id == process_model_id)
        .order_by(ProcessModelVersion.version.desc())
    ).scalars().first()
    next_version = (last_version or 0) + 1

    files: list[ProcessModelFile] = []
    combined = hashlib.sha256()
    for file_in in payload.files:
        digest = hashlib.sha256(file_in.content.encode("utf-8")).hexdigest()
        combined.update(digest.encode("ascii"))
        files.append(ProcessModelFile(
            file_name=file_in.file_name,
            file_type=file_in.file_type,
            content=file_in.content,
            sha256=digest,
        ))

    version = ProcessModelVersion(
        process_model_id=process_model_id,
        version=next_version,
        status=ProcessModelVersionStatus.DRAFT,
        bpmn_process_id=payload.bpmn_process_id,
        content_hash=combined.hexdigest(),
        files=files,
        # Indeks formularzy (pliki JSON_SCHEMA/UI_SCHEMA + rozszerzenia BPMN):
        # `spec_json` jest później nakładany na wersje tworzone bez plików.
        spec_json=build_spec_json(
            (file.file_name, file.file_type, file.content) for file in files),
    )
    db.add(version)
    db.commit()
    db.refresh(version)
    return version


@router.post("/{process_model_id}/versions/{version_id}/publish",
             response_model=ProcessModelVersionRead)
def publish_process_model_version(process_model_id: UUID, version_id: UUID,
                                   db: Session = Depends(get_db),
                                   current_user: CurrentUser = Depends(require_process_admin)):
    """Publikuje wersję; cofa poprzednio opublikowaną (unikalny indeks
    uq_pmv_one_published w wf/models/wf.py dopuszcza tylko jedną na raz)."""
    version = db.get(ProcessModelVersion, version_id)
    if version is None or version.process_model_id != process_model_id:
        raise HTTPException(status_code=404, detail="Wersja procesu nie znaleziona")

    previous = db.execute(
        select(ProcessModelVersion).where(
            ProcessModelVersion.process_model_id == process_model_id,
            ProcessModelVersion.status == ProcessModelVersionStatus.PUBLISHED,
        )
    ).scalars().first()
    if previous is not None:
        previous.status = ProcessModelVersionStatus.RETIRED
        # 2026-10-15: flush PRZED ustawieniem nowej wersji na published -
        db.flush()

    version.status = ProcessModelVersionStatus.PUBLISHED
    version.published_at = datetime.now(UTC)
    db.commit()
    db.refresh(version)
    return version


@router.get("/{process_model_key}/start-form", response_model=ProcessFormRead)
def get_start_form(process_model_key: str,
                   db: Session = Depends(get_db),
                   current_user: CurrentUser = Depends(get_current_wf_user)):
    """Formularz startowy procesu (startEvent) - JSON Schema + uiSchema dla
    react-jsonschema-form. Szablony Jinja renderowane są tu danymi startowymi
    (na starcie pustymi); brak formularza = pusty `json_schema`."""
    service = get_service()
    try:
        version = service.published_version(db, process_model_key)
        task_id, schema, ui_schema = service.start_form(version, data={})
    except ProcessServiceError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except FormRenderError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return ProcessFormRead(
        process_model_key=process_model_key,
        bpmn_task_id=task_id,
        json_schema=schema or {},
        ui_schema=ui_schema or {},
        form_data={},
    )
