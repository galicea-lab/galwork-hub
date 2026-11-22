# wf/services/model_publication.py
# coding: utf-8
"""
2026-11-22: publikacja wersji modelu procesu - jedna implementacja dla
`POST /process-models/{id}/versions/{version_id}/publish` i
`scripts/seed_process_model.py` (`--publish`). Wcześniej skrypt publikował
po swojemu: bez tagu wydania, bez `published_by` i bez dziennika wdrożeń.

Kolejność jak w API:
  1. przypięcie wersji procesów wywoływanych (`callActivity`) - brak
     opublikowanego procesu wywoływanego blokuje publikację,
  2. wycofanie poprzednio opublikowanej wersji (`retired`; ponowna publikacja
     tej samej wersji jej nie wycofuje),
  3. status `published`, `published_at`, `published_by`,
  4. tag wydania w repozytorium modeli (błąd = ostrzeżenie),
  5. zdarzenie w sprawach wdrożeń obiegu (`rollouts.log_publication`).

Funkcja nie commituje - robi to wywołujący (router albo skrypt).
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from wf.models import ProcessModelVersion
from wf.schemas.wf_schemas import CurrentUser, ProcessModelVersionStatus


def latest_version(db: Session, process_model_id) -> ProcessModelVersion | None:
    return db.execute(
        select(ProcessModelVersion)
        .where(ProcessModelVersion.process_model_id == process_model_id)
        .order_by(ProcessModelVersion.version.desc())
    ).scalars().first()


def publish_version(db: Session, version: ProcessModelVersion, actor: CurrentUser) -> list[str]:
    """Publikuje `version`; zwraca ostrzeżenia. `ProcessServiceError` z kroku 1
    przerywa publikację (wywołujący robi rollback)."""
    from wf.services import model_git, process_files, rollouts
    from wf.services.process_service import get_service

    warnings = list(get_service().pin_called_versions(db, version))

    previous = db.execute(
        select(ProcessModelVersion).where(
            ProcessModelVersion.process_model_id == version.process_model_id,
            ProcessModelVersion.status == ProcessModelVersionStatus.PUBLISHED,
        )
    ).scalars().first()
    if previous is not None and previous.id != version.id:
        previous.status = ProcessModelVersionStatus.RETIRED
        # flush PRZED ustawieniem nowej wersji - unikalny indeks
        # uq_pmv_one_published dopuszcza jedną opublikowaną wersję modelu
        db.flush()

    version.status = ProcessModelVersionStatus.PUBLISHED
    version.published_at = datetime.now(UTC)
    version.published_by = actor.id
    tag, tag_warning = model_git.tag_release(
        process_files.files_root(), model_key=version.process_model.key,
        model_name=version.process_model.name, version=version.version,
        version_id=str(version.id), content_hash=version.content_hash,
        commit=version.git_commit, dirty=version.git_dirty, user=actor)
    if tag:
        version.git_tag = tag
    if tag_warning:
        warnings.append(tag_warning)
    rollouts.log_publication(db, version, actor)
    return warnings
