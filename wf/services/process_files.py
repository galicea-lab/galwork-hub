# wf/services/process_files.py
# coding: utf-8
# 2026-11-15: API plików obiegów - zastępuje serwer plików areny
# (galwork-arena/server/bpmn-files.mjs)
"""
Odczyt i zapis plików obiegów w katalogu `spiffworkflow/` (ten sam, który
czyta `wf/services/filesystem_sync.py`).

Rodzaje plików (`kind`):
  * `bpmn`  - definicje procesów `*.bpmn`,
  * `form`  - pliki formularzy JSON: `schema.json`, `*-schema.json`,
              `uischema.json`, `*-uischema.json`, `ioschema.json`,
              `*-ioschema.json`,
  * `print` - szablony wydruków `*.html.j2` i makra `*.j2`.

Zasady:
  * ścieżka zawsze względna do katalogu obiegów; wyjście poza katalog (także
    przez dowiązanie symboliczne) i pliki/katalogi ukryte są odrzucane,
  * zapis jest atomowy (plik tymczasowy w tym samym katalogu + os.replace),
    a cała operacja (sprawdzenie wersji, zapis, commit) idzie pod blokadą
    plikową - hub działa w kilku procesach uvicorn,
  * blokada optymistyczna: klient podaje `base_sha256` treści, którą
    edytował (`None` dla nowego pliku); inna treść na dysku = konflikt 409,
  * walidacja przed zapisem: XML/BPMN, JSON (i JSON Schema dla plików
    schematu), składnia Jinja; zastrzeżenia, które nie blokują pracy
    projektanta, wracają jako `warnings`,
  * jeśli katalog obiegów jest korzeniem repozytorium Git, każdy zapis jest
    commitowany z autorem = zalogowany użytkownik (`[wf]
    process_files_git_commit`, domyślnie włączone). Hub nie robi push.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator

from jinja2 import TemplateSyntaxError
from jinja2.sandbox import SandboxedEnvironment
from jsonschema import SchemaError
from jsonschema.validators import validator_for

from config.config import section
from wf.engine import SpecError, check_bpmn
from wf.schemas.wf_schemas import CurrentUser
from wf.services.filesystem_sync import DEFAULT_ROOT

logger = logging.getLogger(__name__)

MAX_CONTENT_BYTES = 25 * 1024 * 1024

KIND_BPMN = 'bpmn'
KIND_FORM = 'form'
KIND_PRINT = 'print'
KINDS = (KIND_BPMN, KIND_FORM, KIND_PRINT)

# Pliki JSON, które nie są formularzami (metadane modelu/grupy procesów).
_NON_FORM_JSON = {'process_model.json', 'process_group.json'}


class ProcessFileError(ValueError):
    """Błąd operacji na pliku obiegu; `status` to kod HTTP dla routera."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class FileContent:
    path: str
    kind: str
    content: str
    sha256: str


@dataclass(frozen=True)
class SaveResult:
    path: str
    kind: str
    sha256: str
    created: bool
    warnings: list[str] = field(default_factory=list)
    committed: bool = False
    commit: str | None = None
    git_error: str | None = None


# --- konfiguracja ---------------------------------------------------------

def _config() -> dict:
    return section('wf')


def files_root() -> str:
    """Katalog obiegów: `[wf] process_files_dir` albo `spiffworkflow/` huba."""
    configured = (_config().get('process_files_dir') or '').strip()
    return os.path.realpath(configured or DEFAULT_ROOT)


def _git_commit_enabled() -> bool:
    raw = (_config().get('process_files_git_commit') or 'true').strip().lower()
    return raw not in ('0', 'false', 'no', 'off', '')


# --- rodzaje plików -------------------------------------------------------

def classify_form_file(name: str) -> tuple[str, str] | None:
    """(`base`, `role`) dla pliku formularza albo None.

    `role` to `schema` albo `ui`. Konwencje jak w bpmn-files.mjs:
    `<nazwa>-schema.json` + `<nazwa>-uischema.json` (SpiffArena) oraz
    `schema.json` + `ioschema.json`/`uischema.json` (jeden formularz obiegu).
    """
    if name in _NON_FORM_JSON or not name.endswith('.json'):
        return None
    if name == 'schema.json':
        return '', 'schema'
    if name in ('ioschema.json', 'uischema.json'):
        return '', 'ui'
    for suffix, role in (('-uischema.json', 'ui'), ('-ioschema.json', 'ui'),
                         ('-schema.json', 'schema')):
        if name.endswith(suffix):
            return name[:-len(suffix)], role
    return None


def file_kind(name: str) -> str | None:
    lower = name.lower()
    if lower.endswith('.bpmn'):
        return KIND_BPMN
    # 2026-11-16: także makra biblioteki wydruków (`_base/makra.j2`)
    if lower.endswith('.j2'):
        return KIND_PRINT
    if classify_form_file(name):
        return KIND_FORM
    return None


# --- ścieżki --------------------------------------------------------------

def resolve_path(rel_path: str | None, *, root: str | None = None) -> tuple[str, str, str]:
    """(ścieżka absolutna, ścieżka względna POSIX, rodzaj) albo ProcessFileError."""
    root = root or files_root()
    if not isinstance(rel_path, str) or not rel_path.strip():
        raise ProcessFileError('Brak parametru "path".')
    if '\x00' in rel_path:
        raise ProcessFileError('Nieprawidłowa ścieżka.')
    parts = [p for p in rel_path.replace('\\', '/').split('/') if p not in ('', '.')]
    if not parts or any(p == '..' for p in parts):
        raise ProcessFileError('Ścieżka poza katalogiem obiegów.')
    if any(p.startswith('.') for p in parts):
        raise ProcessFileError('Pliki i katalogi ukryte są niedostępne.')
    kind = file_kind(parts[-1])
    if kind is None:
        raise ProcessFileError(
            'Niedozwolony rodzaj pliku. Dozwolone: *.bpmn, pliki formularzy '
            '(schema.json, *-schema.json, uischema.json, *-uischema.json, '
            'ioschema.json, *-ioschema.json) i szablony wydruku *.j2.')
    absolute = os.path.join(root, *parts)
    # realpath rozwiązuje dowiązania także w istniejących katalogach nadrzędnych
    real = os.path.realpath(absolute)
    if not real.startswith(root + os.sep):
        raise ProcessFileError('Ścieżka poza katalogiem obiegów.')
    return real, '/'.join(parts), kind


def _relative(root: str, absolute: str) -> str:
    return os.path.relpath(absolute, root).replace(os.sep, '/')


def _sha256(content: str) -> str:
    return hashlib.sha256(content.encode('utf-8')).hexdigest()


# --- odczyt ---------------------------------------------------------------

def _walk(root: str) -> Iterator[tuple[str, str]]:
    """(ścieżka względna, rodzaj) dla plików obiegów, bez ukrytych."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith('.'))
        for name in sorted(filenames):
            if name.startswith('.'):
                continue
            kind = file_kind(name)
            if kind:
                yield _relative(root, os.path.join(dirpath, name)), kind


def list_files(kind: str | None = None) -> list[dict]:
    if kind is not None and kind not in KINDS:
        raise ProcessFileError(f'Nieznany rodzaj pliku: {kind} (dozwolone: {", ".join(KINDS)})')
    root = files_root()
    return [{'path': rel, 'kind': k} for rel, k in _walk(root) if kind in (None, k)]


def list_form_pairs() -> list[dict]:
    """Pary plików formularza zgrupowane po (katalog, wspólny prefiks) -
    logika `listFormPairs` z bpmn-files.mjs, łącznie z parowaniem
    osieroconych plików przy literówkach w nazwach."""
    pairs: dict[tuple[str, str], dict] = {}
    for rel, kind in _walk(files_root()):
        if kind != KIND_FORM:
            continue
        directory, _, name = rel.rpartition('/')
        base, role = classify_form_file(name)
        pair = pairs.setdefault((directory, base), {
            'dir': directory, 'base': base, 'schemaPath': None, 'uiSchemaPath': None})
        key = 'schemaPath' if role == 'schema' else 'uiSchemaPath'
        pair[key] = pair[key] or rel

    by_dir: dict[str, list[dict]] = {}
    for pair in pairs.values():
        by_dir.setdefault(pair['dir'], []).append(pair)
    for items in by_dir.values():
        schema_only = [p for p in items if p['schemaPath'] and not p['uiSchemaPath']]
        ui_only = [p for p in items if p['uiSchemaPath'] and not p['schemaPath']]
        if len(schema_only) == 1 and len(ui_only) == 1:
            schema_only[0]['uiSchemaPath'] = ui_only[0]['uiSchemaPath']
            del pairs[(ui_only[0]['dir'], ui_only[0]['base'])]

    return sorted(pairs.values(), key=lambda p: p['schemaPath'] or p['uiSchemaPath'] or '')


def read_file(rel_path: str | None) -> FileContent:
    absolute, rel, kind = resolve_path(rel_path)
    try:
        with open(absolute, encoding='utf-8') as f:
            content = f.read()
    except FileNotFoundError as exc:
        raise ProcessFileError(f'Plik {rel} nie istnieje.', status=404) from exc
    except UnicodeDecodeError as exc:
        raise ProcessFileError(f'Plik {rel} nie jest tekstem UTF-8.', status=422) from exc
    return FileContent(path=rel, kind=kind, content=content, sha256=_sha256(content))


# --- walidacja ------------------------------------------------------------

def validate(rel: str, kind: str, content: str) -> list[str]:
    """Rzuca ProcessFileError(422), gdy pliku nie da się użyć; zwraca ostrzeżenia."""
    if kind == KIND_BPMN:
        try:
            return check_bpmn(content)
        except SpecError as exc:
            raise ProcessFileError(str(exc), status=422) from exc

    if kind == KIND_PRINT:
        try:
            SandboxedEnvironment().parse(content)
        except TemplateSyntaxError as exc:
            raise ProcessFileError(
                f'Błąd składni szablonu (wiersz {exc.lineno}): {exc.message}', status=422) from exc
        return []

    # formularz: pusty uiSchema jest dopuszczalny, reszta musi być JSON
    text = content.lstrip('﻿')
    if not text.strip():
        if classify_form_file(rel.rpartition('/')[2])[1] == 'schema':
            raise ProcessFileError('Plik JSON Schema nie może być pusty.', status=422)
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProcessFileError(f'Treść nie jest poprawnym JSON: {exc}', status=422) from exc
    if classify_form_file(rel.rpartition('/')[2])[1] != 'schema':
        return []
    if not isinstance(data, dict):
        raise ProcessFileError('JSON Schema musi być obiektem.', status=422)
    try:
        validator_for(data).check_schema(data)
    except SchemaError as exc:
        return [f'JSON Schema: {exc.message}']
    return []


# --- zapis ----------------------------------------------------------------

@contextmanager
def _locked(root: str) -> Iterator[None]:
    """Blokada między procesami uvicorn. Plik blokady poza katalogiem
    obiegów, żeby nie trafiał do repozytorium Git."""
    digest = hashlib.sha256(root.encode('utf-8')).hexdigest()[:16]
    lock_path = os.path.join(tempfile.gettempdir(), f'galwork-process-files-{digest}.lock')
    with open(lock_path, 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _atomic_write(absolute: str, content: str) -> None:
    directory = os.path.dirname(absolute)
    os.makedirs(directory, exist_ok=True)
    mode = None
    if os.path.exists(absolute):
        mode = os.stat(absolute).st_mode & 0o777
    fd, tmp_path = tempfile.mkstemp(prefix='.tmp-', dir=directory)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='') as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp_path, mode if mode is not None else 0o644)
        os.replace(tmp_path, absolute)
    except BaseException:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def _git(root: str, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    # safe.directory: katalog obiegów należy do GALWORK_UID, a nie zawsze do
    # użytkownika procesu huba - bez tego git odmawia pracy ("dubious ownership").
    return subprocess.run(
        ['git', '-c', f'safe.directory={root}', '-C', root, *args],
        capture_output=True, text=True, timeout=30, env=env, check=False)


def _is_git_root(root: str) -> bool:
    try:
        result = _git(root, 'rev-parse', '--show-toplevel')
    except (OSError, subprocess.SubprocessError):
        return False
    # Tylko gdy katalog obiegów JEST korzeniem repozytorium - w środowisku
    # deweloperskim `spiffworkflow/` leży w repo galwork-hub i nie wolno
    # tam commitować z API.
    return result.returncode == 0 and os.path.realpath(result.stdout.strip()) == root


def _git_commit(root: str, rel: str, user: CurrentUser, created: bool) -> tuple[bool, str | None, str | None]:
    """(czy zacommitowano, sha commita, błąd). Błąd gita nie cofa zapisu pliku."""
    if not _git_commit_enabled() or not _is_git_root(root):
        return False, None, None
    name = user.display_name or user.username
    email = user.email or f'{user.username}@galwork.invalid'
    env = {**os.environ,
           'GIT_AUTHOR_NAME': name, 'GIT_AUTHOR_EMAIL': email,
           'GIT_COMMITTER_NAME': 'galwork-hub', 'GIT_COMMITTER_EMAIL': 'galwork-hub@galwork.invalid'}
    message = f'{"Dodanie" if created else "Zmiana"} {rel} (galwork-arena)'
    try:
        added = _git(root, 'add', '--', rel, env=env)
        if added.returncode != 0:
            return False, None, added.stderr.strip() or 'git add nie powiódł się'
        if _git(root, 'diff', '--cached', '--quiet', '--', rel, env=env).returncode == 0:
            return False, None, None  # treść bez zmian względem HEAD
        committed = _git(root, 'commit', '--no-verify', '-m', message, '--', rel, env=env)
        if committed.returncode != 0:
            return False, None, committed.stderr.strip() or committed.stdout.strip()
        head = _git(root, 'rev-parse', 'HEAD', env=env)
        return True, head.stdout.strip() or None, None
    except (OSError, subprocess.SubprocessError) as exc:
        return False, None, str(exc)


def write_file(rel_path: str | None, content: str, base_sha256: str | None,
               user: CurrentUser) -> SaveResult:
    if len(content.encode('utf-8')) > MAX_CONTENT_BYTES:
        raise ProcessFileError('Plik przekracza 25 MB.', status=413)
    root = files_root()
    absolute, rel, kind = resolve_path(rel_path, root=root)
    warnings = validate(rel, kind, content)

    with _locked(root):
        exists = os.path.isfile(absolute)
        if exists:
            with open(absolute, encoding='utf-8', errors='replace') as f:
                current = _sha256(f.read())
            if base_sha256 != current:
                raise ProcessFileError(
                    f'Plik {rel} został zmieniony przez kogoś innego od chwili otwarcia. '
                    'Wczytaj go ponownie i nanieś zmiany.', status=409)
        elif base_sha256 is not None:
            raise ProcessFileError(
                f'Plik {rel} został usunięty od chwili otwarcia.', status=409)

        _atomic_write(absolute, content)
        committed, commit, git_error = _git_commit(root, rel, user, created=not exists)

    logger.info('Plik obiegu %s zapisany przez %s (commit: %s)', rel, user.username, commit or '-')
    if git_error:
        logger.warning('Plik obiegu %s: commit nie powiódł się: %s', rel, git_error)
    return SaveResult(path=rel, kind=kind, sha256=_sha256(content), created=not exists,
                      warnings=warnings, committed=committed, commit=commit, git_error=git_error)
