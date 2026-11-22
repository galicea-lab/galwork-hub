# wf/services/filesystem_sync.py
# coding: utf-8
"""
Synchronizacja `ProcessModel`/`ProcessModelVersion` z plikami w katalogu
`spiffworkflow/` (wyselekcjonowane obiegi, 'core')

Kontekst: dotąd każdy obieg trzeba było zaseedować RĘCZNIE, jednym
wywołaniem `scripts/seed_process_model.py <folder> --key <key>` - łatwo
zapomnieć po edycji pliku na dysku (odtworzone 2026-10-17: zmiana w BPMN
nie była widoczna w testerze formularzy, dopóki ktoś nie przypomniał sobie
o ponownym zaseedowaniu). Ten moduł skanuje CAŁE drzewo i synchronizuje
wszystko na raz - używany przez CLI (scripts/seed_process_model.py
--sync-all) i przez endpoint administracyjny
(wf/routers/process_model_router.py::sync_process_models_from_disk).

WAŻNE: synchronizacja tworzy/aktualizuje WYŁĄCZNIE wersje `draft`. Nigdy nie
publikuje automatycznie - `require_process_admin` + ręczne "Publikuj" w UI
to świadoma bariera przed tym, żeby edycja pliku na dysku bez ostrzeżenia
zmieniała to, co widzą użytkownicy na żywych instancjach (2026-10-17).

Klucz `ProcessModel.key` dla danego folderu to (w kolejności
pierwszeństwa):
  1. pole `"key"` w `process_model.json` tego folderu (jawny override -
     potrzebny, gdy nazwa folderu nie jest unikalna albo gdy obieg był już
     wcześniej zaseedowany pod innym kluczem niż nazwa folderu - np.
     folder `<grupa>/adres` zaseedowany jako `adres-e-doreczen`),
  2. inaczej nazwa samego folderu (musi być unikalna w całym drzewie -
     `discover_process_folders` nie sprawdza tego explicite, kolizję
     wykryje dopiero próba nadpisania innego ProcessModel o tym samym
     kluczu, więc w razie konfliktu dodaj punkt 1 do jednego z folderów).

TODO: Uporządkować powyższe i opisać ostateczną wersję zarządzania obiegami - w tym wybór core
"""

from __future__ import annotations

import hashlib
import json
import os
import fnmatch
import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from wf.models import ProcessModel, ProcessModelFile, ProcessModelVersion
from wf.schemas.wf_schemas import ProcessFileType, ProcessModelVersionStatus
from wf.services.print_service import library_dir
from wf.services.model_git import source_info
from wf.services.process_service import MODEL_META_FILE, FormRenderError, build_spec_json

#: domyślny katalog ze zdefiniowanymi obiegami, względny do repo galwork-hub.
DEFAULT_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    'spiffworkflow',
)

_BPMN_PROCESS_ID_RE = re.compile(r'<bpmn:process\b[^>]*\bid="([^"]+)"')


class FilesystemSyncError(ValueError):
    """Błąd jednego folderu - łapany per-folder, nie przerywa całej synchronizacji."""


def _file_type(file_name: str) -> ProcessFileType | None:
    if file_name.endswith('.bpmn'):
        return ProcessFileType.BPMN
    # 2026-11-16: szablony wydruków (`*.html.j2`, makra `*.j2`) - wf/services/print_service.py
    if file_name.endswith('.j2'):
        return ProcessFileType.OTHER
    if file_name.endswith('-uischema.json'):
        return ProcessFileType.UI_SCHEMA
    if file_name.endswith('-schema.json'):
        return ProcessFileType.JSON_SCHEMA
    return None


def _bpmn_process_id(content: str) -> str:
    match = _BPMN_PROCESS_ID_RE.search(content)
    if not match:
        raise FilesystemSyncError('Nie znaleziono <bpmn:process id="..."> w pliku .bpmn')
    return match.group(1)


# -BEGIN 2026-11-17: profile obiegów - podzbiór katalogu obiegów do
# synchronizacji (np. start "od zera" tylko z obiegami cyfryzacji)
PROFILES_DIR = 'profiles'


def profile_patterns(root: str, name: str) -> list[str]:
    """Wzorce folderów z `<root>/profiles/<name>.txt`: jedna ścieżka względna
    na wiersz (glob, np. `regulacje/*`, `cyfryzacja/**`), `#` - komentarz."""
    path = os.path.join(root, PROFILES_DIR, f'{name}.txt')
    if not os.path.isfile(path):
        raise FilesystemSyncError(f'Brak profilu obiegów {name!r}: {path}')
    patterns = []
    with open(path, encoding='utf-8') as handle:
        for line in handle:
            line = line.split('#', 1)[0].strip().strip('/')
            if line:
                patterns.append(line)
    return patterns


def _in_profile(rel: str, patterns: list[str]) -> bool:
    rel = rel.replace(os.sep, '/')
    for pattern in patterns:
        if pattern.endswith('/**'):
            base = pattern[:-3]
            if rel == base or rel.startswith(base + '/'):
                return True
        elif fnmatch.fnmatchcase(rel, pattern):
            return True
    return False


def configured_profile() -> str | None:
    from wf.services.process_files import _config
    return (_config().get('process_profile') or '').strip() or None
# -END


def discover_process_folders(root: str, patterns: list[str] | None = None) -> list[str]:
    """Wszystkie foldery pod `root` zawierające dokładnie jeden plik .bpmn -
    to są "liście" (jeden obieg); foldery-grupy (np. "kancelaria/") bez
    własnego .bpmn są pomijane. Folder z >1 plikiem .bpmn to błąd konfiguracji
    (nie wiadomo, który plik jest który) - zgłaszany przez wołającego per-folder,
    nie przerywa reszty skanowania."""
    found = []
    for dirpath, _dirnames, filenames in os.walk(root):
        bpmn_files = [f for f in filenames if f.endswith('.bpmn')]
        if len(bpmn_files) >= 1:
            # 2026-11-17:  profil obiegów - tylko foldery pasujące do wzorców
            if patterns is not None and not _in_profile(os.path.relpath(dirpath, root), patterns):
                continue
            found.append(dirpath)
    return sorted(found)


def _read_process_model_json(folder: str) -> dict:
    path = os.path.join(folder, 'process_model.json')
    if not os.path.isfile(path):
        return {}
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def _key_name_description(folder: str) -> tuple[str, str, str]:
    meta = _read_process_model_json(folder)
    key = meta.get('key') or os.path.basename(folder.rstrip(os.sep))
    name = meta.get('display_name') or key
    description = meta.get('description') or f'Zsynchronizowane z {folder}'
    return key, name, description


def _library_files(library: str | None) -> list[tuple[str, ProcessFileType, str]]:
    """2026-11-16: wspólna biblioteka szablonów wydruku (`_base/*.j2`,
    `_wzory/*.html.j2`) jako pliki wersji o nazwach względnych do biblioteki -
    wersja obiegu niesie komplet szablonów, którymi drukuje."""
    if not library or not os.path.isdir(library):
        return []
    found = []
    for dirpath, dirnames, filenames in os.walk(library):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith('.'))
        for file_name in sorted(filenames):
            if file_name.startswith('.') or not file_name.endswith('.j2'):
                continue
            path = os.path.join(dirpath, file_name)
            rel = os.path.relpath(path, library).replace(os.sep, '/')
            if '/' not in rel:
                continue  # tylko podkatalogi biblioteki (_base, _wzory)
            with open(path, encoding='utf-8') as f:
                found.append((rel, ProcessFileType.OTHER, f.read()))
    return found


def _load_files(folder: str, library: str | None = None) -> list[tuple[str, ProcessFileType, str]]:
    loaded = []
    for file_name in sorted(os.listdir(folder)):
        file_type = _file_type(file_name)
        if file_type is None:
            continue
        with open(os.path.join(folder, file_name), encoding='utf-8') as f:
            loaded.append((file_name, file_type, f.read()))
    if any(name.endswith('.html.j2') for name, _, _ in loaded):
        own = {name for name, _, _ in loaded}
        loaded += [item for item in _library_files(library) if item[0] not in own]
    # 2026-11-22: process_model.json z blokiem `galwork` (albo ścieżkami metadanych) jest plikiem wersji -
    # zmiana widoczności daje nową wersję (content_hash), a po publikacji jest
    # chroniona jak reszta definicji. Bez bloku plik pomijamy (hashe bez zmian).
    meta_path = os.path.join(folder, MODEL_META_FILE)
    if os.path.isfile(meta_path):
        with open(meta_path, encoding='utf-8') as f:
            meta = f.read()
        try:
            parsed = json.loads(meta) or {}
        except ValueError as exc:
            raise FilesystemSyncError(f'{meta_path}: niepoprawny JSON ({exc})') from exc
        # 2026-11-22: także `metadata_extraction_paths` (kolumny listy, H1)
        if 'galwork' in parsed or parsed.get('metadata_extraction_paths'):
            loaded.append((MODEL_META_FILE, ProcessFileType.OTHER, meta))
    bpmn_count = sum(1 for _, t, _ in loaded if t == ProcessFileType.BPMN)
    if bpmn_count == 0:
        raise FilesystemSyncError(f'Brak pliku .bpmn w {folder}')
    if bpmn_count > 1:
        raise FilesystemSyncError(
            f'Więcej niż jeden plik .bpmn w {folder} - nie wiadomo, który jest procesem')
    return loaded


def _content_hash(
    files_content: list[tuple[str, ProcessFileType, str]]
) -> tuple[str, list[tuple[str, ProcessFileType, str, str]]]:
    combined = hashlib.sha256()
    with_sha: list[tuple[str, ProcessFileType, str, str]] = []
    for file_name, file_type, content in files_content:
        digest = hashlib.sha256(content.encode('utf-8')).hexdigest()
        combined.update(digest.encode('ascii'))
        with_sha.append((file_name, file_type, content, digest))
    return combined.hexdigest(), with_sha


@dataclass(frozen=True)
class SyncResult:
    folder: str
    key: str
    #: 'unchanged' | 'draft-created' | 'would-create-model' | 'would-create-draft' | 'error'
    status: str
    detail: str = ''


def sync_folder(db: Session, folder: str, *, dry_run: bool = False,
                root: str | None = None) -> SyncResult:
    """Synchronizuje jeden folder - tworzy ProcessModel, jeśli nie istnieje,
    i nową wersję `draft`, jeśli treść plików się zmieniła (po `content_hash`,
    tak jak w publish_process_model_version). Nigdy nie publikuje."""
    key, name, description = _key_name_description(folder)
    try:
        # 2026-11-16: biblioteka szablonów wydruku z katalogu obiegów (`root`)
        files_content = _load_files(folder, library_dir(root) if root else None)
        bpmn_content = next(c for _, t, c in files_content if t == ProcessFileType.BPMN)
        bpmn_process_id = _bpmn_process_id(bpmn_content)
    except FilesystemSyncError as exc:
        return SyncResult(folder, key, 'error', str(exc))

    content_hash, with_sha = _content_hash(files_content)
    try:
        spec_json = build_spec_json((n, t, c) for n, t, c, _ in with_sha)
    except FormRenderError as exc:
        return SyncResult(folder, key, 'error', str(exc))

    model = db.execute(select(ProcessModel).where(ProcessModel.key == key)).scalars().first()
    if model is None:
        if dry_run:
            return SyncResult(folder, key, 'would-create-model')
        model = ProcessModel(key=key, name=name, description=description)
        db.add(model)
        db.flush()

    latest = db.execute(
        select(ProcessModelVersion)
        .where(ProcessModelVersion.process_model_id == model.id)
        .order_by(ProcessModelVersion.version.desc())
    ).scalars().first()

    if latest is not None and latest.content_hash == content_hash:
        return SyncResult(folder, key, 'unchanged')

    if dry_run:
        return SyncResult(folder, key, 'would-create-draft')

    next_version = (latest.version if latest else 0) + 1
    # 2026-11-17:  pochodzenie wersji z gita (commit HEAD, niezacommitowane zmiany)
    source = source_info(folder, library_dir(root) if root else None)
    files = [ProcessModelFile(file_name=file_name, file_type=file_type, content=content, sha256=sha)
             for file_name, file_type, content, sha in with_sha]
    version = ProcessModelVersion(
        process_model_id=model.id, version=next_version,
        status=ProcessModelVersionStatus.DRAFT, bpmn_process_id=bpmn_process_id,
        content_hash=content_hash, files=files,
        spec_json=spec_json,
        git_commit=source.commit, git_dirty=source.dirty,
    )
    db.add(version)
    db.flush()
    return SyncResult(folder, key, 'draft-created', f'wersja {next_version}')


def sync_all(db: Session, root: str = DEFAULT_ROOT, *, dry_run: bool = False,
             profile: str | None = None) -> list[SyncResult]:
    """Synchronizuje wszystkie obiegi pod `root`. Commituje na końcu (albo
    nic, jeśli `dry_run`) - jeden folder z błędem (`FilesystemSyncError`) nie
    przerywa pozostałych, ale cała transakcja jest spójna (albo wszystkie
    poprawne zmiany, albo żadna, jeśli coś inne wywali wyjątek niżej)."""
    # 2026-11-17:  profil obiegów (wołający podaje jawnie, np. `[wf] process_profile`)
    patterns = profile_patterns(root, profile) if profile else None
    results = [sync_folder(db, folder, dry_run=dry_run, root=root)
               for folder in discover_process_folders(root, patterns)]
    if not dry_run:
        db.commit()
    return results
