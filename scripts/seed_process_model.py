#!/usr/bin/env python3
# coding: utf-8
"""
Zakłada (albo aktualizuje i publikuje) ProcessModel z wszystkich plików
.bpmn/*-schema.json/*-uischema.json jednego katalogu obiegu skopiowanego z
spiffworkflow-core/spiffworkflow-all (zob. rjsf.md, x-webcon.md,
spiffworkflow-core/zaleznosci.md) - ten sam mechanizm jednorazowego importu
"z ręki" co scripts/seed_edr_process.py (POST /process-models + .../publish
to normalna droga przez API, zob. wf/routers/process_model_router.py), ale
ogólny: bierze WSZYSTKIE pliki danego typu z katalogu, nie jedną ustaloną
trójkę nazw, więc działa dla każdego obiegu w spiffworkflow/ bez zmian w
kodzie.

Idempotentny: jeśli model o kluczu `--key` już istnieje i jego najnowsza
wersja ma identyczny `content_hash` (te same pliki), nic nie robi (albo -
jeśli ta wersja nie jest jeszcze opublikowana - tylko ją publikuje, nie
tworzy duplikatu). Publikacja cofa poprzednio opublikowaną wersję (status
`retired`), tak jak `POST .../versions/{id}/publish`.

Użycie (pojedynczy folder, z publikacją):
    python3 scripts/seed_process_model.py spiffworkflow/kancelaria/proces-wysylania \
        --key proces-wysylania --name "Proces wysyłania"
    python3 scripts/seed_process_model.py spiffworkflow/slowniki/struktura/instytuty \
        --key instytuty --name "Instytuty" --dry-run

 `--sync-all` - synchronizuje CAŁE drzewo spiffworkflow/ na
raz (wf/services/filesystem_sync.py), zamiast jednego folderu na wywołanie.
W przeciwieństwie do trybu pojedynczego folderu NIGDY nie publikuje -
tworzy/aktualizuje tylko wersje `draft` (zob. docstring tamtego modułu).
Ten sam kod stoi za przyciskiem "Synchronizuj z dysku" w galwork-dashboard
(wf/routers/process_model_router.py::sync_process_models_from_disk).

    python3 scripts/seed_process_model.py --sync-all
    python3 scripts/seed_process_model.py --sync-all --dry-run
    python3 scripts/seed_process_model.py --sync-all --root spiffworkflow/kancelaria
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from datetime import UTC, datetime

currentdir = os.path.dirname(os.path.abspath(__file__))
parentdir = os.path.dirname(currentdir)
sys.path.insert(0, parentdir)

from sqlalchemy import select  # noqa: E402

from db.session import SessionLocal  # noqa: E402
from wf.models import ProcessModel, ProcessModelFile, ProcessModelVersion  # noqa: E402
from wf.schemas.wf_schemas import ProcessFileType, ProcessModelVersionStatus  # noqa: E402
from wf.services.filesystem_sync import DEFAULT_ROOT, sync_all  # noqa: E402
from wf.services.process_service import build_spec_json  # noqa: E402

_BPMN_PROCESS_ID_RE = re.compile(r'<bpmn:process\b[^>]*\bid="([^"]+)"')


def _file_type(file_name: str) -> ProcessFileType | None:
    if file_name.endswith('.bpmn'):
        return ProcessFileType.BPMN
    if file_name.endswith('-uischema.json'):
        return ProcessFileType.UI_SCHEMA
    if file_name.endswith('-schema.json'):
        return ProcessFileType.JSON_SCHEMA
    return None


def _bpmn_process_id(content: str) -> str:
    match = _BPMN_PROCESS_ID_RE.search(content)
    if not match:
        raise SystemExit('Nie znaleziono <bpmn:process id="..."> w pliku .bpmn')
    return match.group(1)


def _load_files(folder: str) -> list[tuple[str, ProcessFileType, str]]:
    if not os.path.isdir(folder):
        raise SystemExit(f'Katalog nie istnieje: {folder}')
    loaded = []
    for file_name in sorted(os.listdir(folder)):
        file_type = _file_type(file_name)
        if file_type is None:
            continue
        with open(os.path.join(folder, file_name), encoding='utf-8') as f:
            loaded.append((file_name, file_type, f.read()))
    if not any(file_type == ProcessFileType.BPMN for _, file_type, _ in loaded):
        raise SystemExit(f'Brak pliku .bpmn w {folder}')
    return loaded


def _content_hash(
    files_content: list[tuple[str, ProcessFileType, str]]
) -> tuple[str, list[tuple[str, ProcessFileType, str, str]]]:
    """Jak create_process_model_version w process_model_router.py - sha256
    per plik (UTF-8), content_hash = sha256 konkatenacji tych digestów (w
    kolejności plików na dysku - nieistotne, bo liczone i porównywane tu
    samo z sobą, konsekwentnie)."""
    combined = hashlib.sha256()
    with_sha: list[tuple[str, ProcessFileType, str, str]] = []
    for file_name, file_type, content in files_content:
        digest = hashlib.sha256(content.encode('utf-8')).hexdigest()
        combined.update(digest.encode('ascii'))
        with_sha.append((file_name, file_type, content, digest))
    return combined.hexdigest(), with_sha


def seed(folder: str, key: str, name: str, dry_run: bool = False) -> str:
    files_content = _load_files(folder)
    bpmn_content = next(content for _, file_type, content in files_content
                        if file_type == ProcessFileType.BPMN)
    bpmn_process_id = _bpmn_process_id(bpmn_content)
    content_hash, with_sha = _content_hash(files_content)

    db = SessionLocal()
    try:
        model = db.execute(select(ProcessModel).where(ProcessModel.key == key)).scalars().first()
        if model is None:
            print(f"[seed_process_model] model '{key}' nie istnieje - zakladam nowy" +
                  (' (dry-run, bez zapisu)' if dry_run else ''))
            if dry_run:
                return 'would-create-model'
            model = ProcessModel(key=key, name=name, description=f'Zaimportowane z {folder}')
            db.add(model)
            db.flush()

        latest = db.execute(
            select(ProcessModelVersion)
            .where(ProcessModelVersion.process_model_id == model.id)
            .order_by(ProcessModelVersion.version.desc())
        ).scalars().first()

        if latest is not None and latest.content_hash == content_hash:
            if latest.status == ProcessModelVersionStatus.PUBLISHED:
                print(f"[seed_process_model] model '{key}' juz aktualny "
                      f"(wersja {latest.version}, opublikowana) - nic do zrobienia")
                return 'unchanged'
            version = latest
            print(f"[seed_process_model] model '{key}' wersja {latest.version} "
                  f"istnieje, ale nie jest opublikowana - publikuje" +
                  (' (dry-run)' if dry_run else ''))
        else:
            next_version = (latest.version if latest else 0) + 1
            print(f"[seed_process_model] model '{key}': tworze wersje {next_version}" +
                  (' (dry-run, bez zapisu)' if dry_run else ''))
            if dry_run:
                return 'would-create-version'
            files = [ProcessModelFile(file_name=file_name, file_type=file_type,
                                      content=content, sha256=sha)
                     for file_name, file_type, content, sha in with_sha]
            version = ProcessModelVersion(
                process_model_id=model.id, version=next_version,
                status=ProcessModelVersionStatus.DRAFT, bpmn_process_id=bpmn_process_id,
                content_hash=content_hash, files=files,
                spec_json=build_spec_json(
                    (file_name, file_type, content) for file_name, file_type, content, _ in with_sha),
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
            # flush przed publikacją nowej wersji - zob. ten
            # sam komentarz w wf/routers/process_model_router.py
            db.flush()

        version.status = ProcessModelVersionStatus.PUBLISHED
        version.published_at = datetime.now(UTC)
        db.commit()
        print(f"[seed_process_model] model '{key}' wersja {version.version} opublikowana.")
        return 'published'
    finally:
        db.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder', nargs='?', default=None,
                        help='Katalog obiegu (wzgledem repo albo absolutny) - wymagany, '
                             'jesli nie podano --sync-all')
    parser.add_argument('--key', default=None, help='ProcessModel.key (wymagany bez --sync-all)')
    parser.add_argument('--name', default=None, help='ProcessModel.name (domyslnie = --key)')
    parser.add_argument('--sync-all', action='store_true',
                        help='Synchronizuj cale drzewo (tylko draft, nigdy publish) '
                             'zamiast jednego folderu.')
    parser.add_argument('--root', default=DEFAULT_ROOT,
                        help='Katalog startowy dla --sync-all (domyslnie spiffworkflow/).')
    parser.add_argument('--dry-run', action='store_true',
                        help='Nie zapisuje nic w bazie, tylko wypisuje co by sie stalo.')
    args = parser.parse_args()

    if args.sync_all:
        db = SessionLocal()
        try:
            results = sync_all(db, args.root, dry_run=args.dry_run)
        finally:
            db.close()
        for result in results:
            print(f"[sync_all] {result.status:20s} {result.key:30s} {result.folder}"
                 + (f" ({result.detail})" if result.detail else ""))
        changed = sum(1 for r in results if r.status in ('draft-created', 'would-create-draft',
                                                          'would-create-model'))
        print(f"[sync_all] {len(results)} folderow, {changed} ze zmianami"
             + (' (dry-run)' if args.dry_run else ''))
    else:
        if not args.folder or not args.key:
            parser.error('folder i --key sa wymagane, jesli nie podano --sync-all')
        target_folder = args.folder if os.path.isabs(args.folder) else os.path.join(parentdir, args.folder)
        seed(target_folder, args.key, args.name or args.key, dry_run=args.dry_run)
