#!/usr/bin/env python3
# coding: utf-8
"""
Zakłada (albo aktualizuje) model procesu "edr-korespondencja" potrzebny
`connector/edoreczenia/edr_paperless.py::sync_inbox` -
`get_service().published_version(db, PROCESS_MODEL_KEY)` rzuca
`ProcessServiceError`, jeśli taki opublikowany model nie istnieje.

W tym hubie modele procesów są normalnie tworzone przez API
(`wf/routers/process_model_router.py`: `POST /process-models`, `.../versions`,
`.../versions/{id}/publish`) - ten skrypt robi to samo przez sesję
SQLAlchemy bezpośrednio (bez HTTP/Keycloak), bo ma działać jednorazowo przy
wdrożeniu, zanim jest sens logować się do API. Liczenie `content_hash`/
`sha256` i budowa `spec_json` są 1:1 skopiowane z
`process_model_router.py::create_process_model_version`, żeby plik
wygenerowany tym skryptem był nieodróżnialny od stworzonego przez API.

Idempotentny: jeśli model o kluczu `--key` już istnieje i jego najnowsza
wersja ma identyczny `content_hash` (te same pliki), nic nie robi. Jeśli
pliki się zmieniły (albo modelu/wersji jeszcze nie ma), tworzy i publikuje
nową wersję, cofając poprzednio opublikowaną (jak
`publish_process_model_version`).

Użycie:
    python3 scripts/seed_edr_process.py
    python3 scripts/seed_edr_process.py --key edr-korespondencja --dry-run
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
from datetime import UTC, datetime

currentdir = os.path.dirname(os.path.abspath(__file__))
parentdir = os.path.dirname(currentdir)
sys.path.insert(0, parentdir)

from sqlalchemy import select  # noqa: E402

from db.session import SessionLocal  # noqa: E402
from wf.models import ProcessModel, ProcessModelFile, ProcessModelVersion  # noqa: E402
from wf.schemas.wf_schemas import ProcessFileType, ProcessModelVersionStatus  # noqa: E402
from wf.services.process_service import build_spec_json  # noqa: E402

DEFINITIONS_DIR = os.path.join(currentdir, 'process_definitions')

#: (nazwa_pliku, typ) - musi być zgodne z PROCESS_MODEL_KEY w
#: connector/edoreczenia/edr_paperless.py (domyślnie `edr-korespondencja`,
#: konfigurowalne przez `[edr] process_model_key`).
FILES = (
    ('edr_korespondencja.bpmn', ProcessFileType.BPMN),
    ('przeglad.json', ProcessFileType.JSON_SCHEMA),
    ('przeglad.uischema.json', ProcessFileType.UI_SCHEMA),
)

BPMN_PROCESS_ID = 'edr_korespondencja'


def _load_files() -> list[tuple[str, ProcessFileType, str]]:
    loaded = []
    for file_name, file_type in FILES:
        path = os.path.join(DEFINITIONS_DIR, file_name)
        with open(path, encoding='utf-8') as f:
            loaded.append((file_name, file_type, f.read()))
    return loaded


def _content_hash(files_content: list[tuple[str, ProcessFileType, str]]) -> tuple[str, list[tuple[str, ProcessFileType, str, str]]]:
    """Jak `create_process_model_version` - sha256 per plik (UTF-8),
    `content_hash` = sha256 konkatenacji tych digestów (w kolejności
    `FILES`, nie alfabetycznie - to nieistotne, bo liczone i porównywane
    tu samo z sobą, konsekwentnie)."""
    combined = hashlib.sha256()
    with_sha: list[tuple[str, ProcessFileType, str, str]] = []
    for file_name, file_type, content in files_content:
        digest = hashlib.sha256(content.encode('utf-8')).hexdigest()
        combined.update(digest.encode('ascii'))
        with_sha.append((file_name, file_type, content, digest))
    return combined.hexdigest(), with_sha


def seed(key: str, name: str, dry_run: bool = False) -> str:
    files_content = _load_files()
    content_hash, with_sha = _content_hash(files_content)

    db = SessionLocal()
    try:
        model = db.execute(select(ProcessModel).where(ProcessModel.key == key)).scalars().first()
        if model is None:
            print(f"[seed_edr_process] model '{key}' nie istnieje - zakladam nowy" +
                  (' (dry-run, bez zapisu)' if dry_run else ''))
            if dry_run:
                return 'would-create-model'
            model = ProcessModel(key=key, name=name,
                                 description='Obsluga korespondencji e-Doreczenia (Paperless-ngx)')
            db.add(model)
            db.flush()

        latest = db.execute(
            select(ProcessModelVersion)
            .where(ProcessModelVersion.process_model_id == model.id)
            .order_by(ProcessModelVersion.version.desc())
        ).scalars().first()

        if latest is not None and latest.content_hash == content_hash:
            if latest.status == ProcessModelVersionStatus.PUBLISHED:
                print(f"[seed_edr_process] model '{key}' juz aktualny "
                      f"(wersja {latest.version}, opublikowana) - nic do zrobienia")
                return 'unchanged'
            # treść się zgadza, ale ta wersja nie jest (jeszcze) opublikowana -
            # publikujemy ją, nie tworzymy duplikatu.
            version = latest
            print(f"[seed_edr_process] model '{key}' wersja {latest.version} "
                  f"istnieje, ale nie jest opublikowana - publikuje" +
                  (' (dry-run)' if dry_run else ''))
        else:
            next_version = (latest.version if latest else 0) + 1
            print(f"[seed_edr_process] model '{key}': tworze wersje {next_version}" +
                  (' (dry-run, bez zapisu)' if dry_run else ''))
            if dry_run:
                return 'would-create-version'
            files = [ProcessModelFile(file_name=fn, file_type=ft, content=content, sha256=sha)
                     for fn, ft, content, sha in with_sha]
            version = ProcessModelVersion(
                process_model_id=model.id, version=next_version,
                status=ProcessModelVersionStatus.DRAFT, bpmn_process_id=BPMN_PROCESS_ID,
                content_hash=content_hash, files=files,
                spec_json=build_spec_json((fn, ft, content) for fn, ft, content, _ in with_sha),
            )
            db.add(version)
            db.flush()

        if dry_run:
            return 'would-publish'

        previous = db.execute(
            select(ProcessModelVersion).where(
                ProcessModelVersion.process_model_id == model.id,
                ProcessModelVersion.status == ProcessModelVersionStatus.PUBLISHED,
                ProcessModelVersion.id != version.id,
            )
        ).scalars().first()
        if previous is not None:
            previous.status = ProcessModelVersionStatus.RETIRED
            # flush przed publikacją nowej wersji - zob.
            # wf/routers/process_model_router.py::publish_process_model_version
            # (uq_pmv_one_published to częściowy indeks unikalny sprawdzany
            # przy każdym UPDATE, nie dopiero przy commicie - bez flusha tu
            # druga publikacja tego samego procesu dostaje IntegrityError).
            db.flush()

        version.status = ProcessModelVersionStatus.PUBLISHED
        version.published_at = datetime.now(UTC)
        db.commit()
        print(f"[seed_edr_process] model '{key}' wersja {version.version} opublikowana.")
        return 'published'
    finally:
        db.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--key', default='edr-korespondencja',
                        help='ProcessModel.key (musi zgadzac sie z [edr] process_model_key '
                             'w config.ini, domyslnie "edr-korespondencja")')
    parser.add_argument('--name', default='Korespondencja e-Doreczenia')
    parser.add_argument('--dry-run', action='store_true',
                        help='Nie zapisuje nic w bazie, tylko wypisuje co by sie stalo.')
    args = parser.parse_args()
    seed(args.key, args.name, dry_run=args.dry_run)
