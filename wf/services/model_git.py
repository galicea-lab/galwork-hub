# wf/services/model_git.py
# 
# 2026-11-17: git jako źródło wersji modeli procesów - commit, z którego
# powstała wersja, i tag wydania przy publikacji.
"""
Wersje modeli procesów a git.

  * `source_info` - przy tworzeniu wersji z katalogu obiegów
    (wf/services/filesystem_sync.py): commit HEAD repozytorium i znacznik
    niezacommitowanych zmian w folderze modelu (i bibliotece wydruków).
    Wersja z `git_dirty = true` nie odpowiada w całości żadnemu commitowi,
  * `tag_release` - przy publikacji: tag adnotowany `model/<klucz>/v<wersja>`
    na commicie wersji, z autorem = publikującym i treścią: skrót treści
    wersji, id wersji. Tag powstaje tylko, gdy katalog obiegów jest
    korzeniem repozytorium (jak commit z edytora - process_files.py), a wersja
    pochodzi z czystego commita. Hub nie wypycha tagów (push - poza hubem).

Tagi `model/...` nie pasują do wzorca `v*`, który uruchamia publikację
pakietów i mirrora.

Konfiguracja: `[wf] process_release_tags` (domyślnie true).
"""
from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass

from wf.schemas.wf_schemas import CurrentUser

logger = logging.getLogger(__name__)

TAG_PREFIX = 'model'


@dataclass(frozen=True)
class SourceInfo:
    commit: str | None = None
    dirty: bool | None = None


def _git(cwd: str, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        # katalog obiegów to zaufana konfiguracja huba; właściciel plików może
        # być inny niż użytkownik procesu (jak w process_files._git)
        ['git', '-c', 'safe.directory=*', '-C', cwd, *args],
        capture_output=True, text=True, timeout=30, env=env, check=False)


def _toplevel(path: str) -> str | None:
    try:
        result = _git(path, 'rev-parse', '--show-toplevel')
    except (OSError, subprocess.SubprocessError):
        return None
    return os.path.realpath(result.stdout.strip()) if result.returncode == 0 else None


def source_info(folder: str, *extra_paths: str | None) -> SourceInfo:
    """Commit HEAD i niezacommitowane zmiany w `folder` (oraz `extra_paths`)."""
    top = _toplevel(folder)
    if top is None:
        return SourceInfo()
    try:
        head = _git(top, 'rev-parse', 'HEAD')
        if head.returncode != 0:  # repozytorium bez commitów
            return SourceInfo(dirty=True)
        paths = [os.path.relpath(os.path.realpath(p), top) for p in (folder, *extra_paths)
                 if p and os.path.exists(p)]
        status = _git(top, 'status', '--porcelain', '--untracked-files=all', '--', *paths)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning('model_git: %s: %s', folder, exc)
        return SourceInfo()
    return SourceInfo(commit=head.stdout.strip(), dirty=bool(status.stdout.strip()))


def _tags_enabled() -> bool:
    from wf.services.process_files import _config
    raw = (_config().get('process_release_tags') or 'true').strip().lower()
    return raw not in ('0', 'false', 'no', 'off', 'nie')


def tag_name(model_key: str, version: int) -> str:
    return f'{TAG_PREFIX}/{model_key}/v{version}'


def tag_release(root: str, *, model_key: str, model_name: str, version: int, version_id: str,
                content_hash: str, commit: str | None, dirty: bool | None,
                user: CurrentUser) -> tuple[str | None, str | None]:
    """(nazwa tagu, ostrzeżenie). Błąd gita nie blokuje publikacji."""
    if not _tags_enabled():
        return None, None
    root = os.path.realpath(root)
    if _toplevel(root) != root:
        return None, None  # katalog obiegów nie jest repozytorium modeli (np. dev)
    name = tag_name(model_key, version)
    if not commit:
        return None, f"Wersja {version} nie pochodzi z commita git - nie utworzono tagu {name}"
    if dirty:
        return None, (f"Wersja {version} zawiera niezacommitowane zmiany - nie utworzono tagu "
                      f"{name}; zacommituj pliki i zsynchronizuj model ponownie")
    try:
        existing = _git(root, 'rev-parse', '--verify', '--quiet', f'refs/tags/{name}^{{commit}}')
        if existing.returncode == 0:
            if existing.stdout.strip() == commit:
                return name, None  # ponowna publikacja tej samej wersji
            return None, f"Tag {name} już istnieje i wskazuje inny commit - nie zmieniono go"
        tagger = user.display_name or user.username
        env = {**os.environ,
               'GIT_COMMITTER_NAME': tagger,
               'GIT_COMMITTER_EMAIL': user.email or f'{user.username}@galwork.invalid'}
        message = (f'{model_name} - wersja {version}\n\n'
                   f'Model: {model_key}\nWersja: {version}\nId wersji: {version_id}\n'
                   f'Skrót treści: {content_hash}\nOpublikował: {tagger}\n')
        created = _git(root, 'tag', '-a', name, commit, '-m', message, env=env)
        if created.returncode != 0:
            return None, f"Nie utworzono tagu {name}: {created.stderr.strip()}"
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"Nie utworzono tagu {name}: {exc}"
    return name, None
