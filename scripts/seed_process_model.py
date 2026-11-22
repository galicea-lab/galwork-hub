#!/usr/bin/env python3
# coding: utf-8
"""
Synchronizacja modeli procesów z katalogu obiegów i (opcjonalnie) publikacja.

2026-11-22: oba tryby używają tej samej synchronizacji co przycisk
„Synchronizuj z dysku” (wf/services/filesystem_sync.py) - wersja zawiera
`process_model.json` z blokiem `galwork` / `metadata_extraction_paths`
(kolumny listy, karta, rejestr jawny, słownik z rejestru) i szablony wydruku
z biblioteki. Publikacja idzie przez wf/services/model_publication.py - tak
samo jak `POST .../versions/{id}/publish` (przypięcie callActivity, wycofanie
poprzedniej wersji, `published_by`, tag wydania, dziennik wdrożeń).

Klucz modelu pochodzi z `process_model.json` (`key`) albo z nazwy folderu -
`--key` służy już tylko do kontroli: inny klucz niż w modelu to błąd (dawniej
tworzył osobny model, np. `rejestr_systemow` obok `rejestr-systemow`).

Jeden folder - synchronizacja i publikacja:
    python3 scripts/seed_process_model.py spiffworkflow/kancelaria/proces-wysylania
    python3 scripts/seed_process_model.py ../galwork-models/it/zarzadzanie-it/rejestr-systemow --dry-run

Całe drzewo (profil `[wf] process_profile`, katalog `[wf] process_files_dir`):
    python3 scripts/seed_process_model.py --sync-all              # tylko szkice
    python3 scripts/seed_process_model.py --sync-all --publish    # szkice + publikacja
    python3 scripts/seed_process_model.py --sync-all --publish --dry-run
    python3 scripts/seed_process_model.py --sync-all --profile galicea --publish

`--publish` publikuje NAJNOWSZĄ wersję każdego modelu z zakresu, jeśli jest
szkicem - także szkic z wcześniejszej synchronizacji. Modele z callActivity
czekają, aż proces wywoływany zostanie opublikowany (kolejne przebiegi).
Publikującym jest `--user` albo `[wf] system_user` (`[edr] system_user`).
"""
from __future__ import annotations

import argparse
import os
import sys

currentdir = os.path.dirname(os.path.abspath(__file__))
parentdir = os.path.dirname(currentdir)
sys.path.insert(0, parentdir)

from sqlalchemy import select  # noqa: E402

from connector.base import BaseConnector  # noqa: E402
from db.session import SessionLocal  # noqa: E402
from models.user import User  # noqa: E402
from wf.models import ProcessModel, ProcessModelVersion  # noqa: E402
from wf.schemas.wf_schemas import CurrentUser, ProcessModelVersionStatus  # noqa: E402
from wf.services import model_publication, process_files  # noqa: E402
from wf.services.filesystem_sync import _key_name_description, configured_profile, sync_all, sync_folder  # noqa: E402
from wf.services.process_service import ProcessServiceError  # noqa: E402


def _actor(db, login: str | None) -> CurrentUser:
    """Publikujący: `--user` albo `[wf] system_user` / `[edr] system_user`."""
    if not login:
        for section in ('wf', 'edr'):
            login = (BaseConnector.load_ini_section(section).get('system_user') or '').strip()
            if login:
                break
    user = db.execute(select(User).where((User.name == login) | (User.email == login))
                      ).scalars().first() if login else None
    if user is None:
        sys.exit('[seed_process_model] publikacja wymaga użytkownika: --user LOGIN albo '
                 '[wf] system_user (nazwa lub e-mail z tabeli users)')
    return CurrentUser(id=user.id, sub=user.keycloak_sub or 'sub-seed', username=user.name,
                       email=user.email, display_name=user.name, groups=frozenset())


def publish_drafts(db, keys: list[str], actor: CurrentUser | None, *, dry_run: bool) -> int:
    """Publikuje najnowsze szkice modeli `keys`; zwraca liczbę błędów.
    Kolejne przebiegi publikują modele, które czekały na proces wywoływany."""
    pending = []
    for key in dict.fromkeys(keys):
        model = db.execute(select(ProcessModel).where(ProcessModel.key == key)).scalars().first()
        version = model_publication.latest_version(db, model.id) if model else None
        if version is not None and version.status == ProcessModelVersionStatus.DRAFT:
            pending.append(version.id)
        elif version is not None:
            print(f"[publish] {'bez zmian':12s} {key} (wersja {version.version}: {version.status.value})")
    if dry_run:
        for version_id in pending:
            version = db.get(ProcessModelVersion, version_id)
            print(f"[publish] {'do publikacji':12s} {version.process_model.key} (wersja {version.version}, dry-run)")
        return 0
    errors: dict = {}
    while pending:
        progress, waiting = False, []
        for version_id in pending:
            version = db.get(ProcessModelVersion, version_id)
            key = version.process_model.key
            try:
                warnings = model_publication.publish_version(db, version, actor)
                db.commit()
            except ProcessServiceError as exc:
                db.rollback()
                errors[version_id] = f'{key}: {exc}'
                waiting.append(version_id)
                continue
            progress = True
            errors.pop(version_id, None)
            print(f"[publish] {'opublikowany':12s} {key} (wersja {version.version}"
                  + (f", tag {version.git_tag}" if version.git_tag else '') + ')')
            for warning in warnings:
                print(f"[publish]   UWAGA: {warning}")
        if not progress:
            break
        pending = waiting
    for message in errors.values():
        print(f"[publish] {'BŁĄD':12s} {message}")
    return len(errors)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('folder', nargs='?', default=None,
                        help='Katalog jednego obiegu (względem huba albo absolutny); bez --sync-all')
    parser.add_argument('--key', default=None,
                        help='Kontrola klucza: musi być równy kluczowi z process_model.json / nazwie folderu')
    parser.add_argument('--name', default=None, help='(ignorowane - nazwa z process_model.json: display_name)')
    parser.add_argument('--sync-all', action='store_true', help='Synchronizuj całe drzewo katalogu obiegów')
    parser.add_argument('--publish', action='store_true',
                        help='Z --sync-all: opublikuj najnowsze szkice modeli z zakresu')
    parser.add_argument('--root', default=None,
                        help='Katalog obiegów (domyślnie [wf] process_files_dir albo spiffworkflow/)')
    parser.add_argument('--profile', default=None,
                        help='Profil obiegów dla --sync-all, np. "galicea"; "-" = bez profilu. '
                             'Domyślnie [wf] process_profile.')
    parser.add_argument('--user', default=None, help='Publikujący (nazwa lub e-mail z tabeli users)')
    parser.add_argument('--dry-run', action='store_true', help='Nic nie zapisuje, wypisuje co by się stało')
    args = parser.parse_args()
    root = os.path.realpath(args.root) if args.root else process_files.files_root()

    db = SessionLocal()
    try:
        if args.sync_all:
            profile = None if args.profile == '-' else (args.profile or configured_profile())
            results = sync_all(db, root, dry_run=args.dry_run, profile=profile)
            for result in results:
                print(f"[sync_all] {result.status:20s} {result.key:30s} {result.folder}"
                      + (f" ({result.detail})" if result.detail else ''))
            failed = [r for r in results if r.status == 'error']
            print(f"[sync_all] {len(results)} folderów, "
                  f"{sum(1 for r in results if r.status in ('draft-created', 'would-create-draft', 'would-create-model'))}"
                  f" ze zmianami, {len(failed)} z błędem" + (' (dry-run)' if args.dry_run else ''))
            if not args.publish:
                return 1 if failed else 0
            keys = [r.key for r in results if r.status != 'error']
        else:
            if not args.folder:
                parser.error('podaj katalog obiegu albo --sync-all')
            folder = args.folder if os.path.isabs(args.folder) else os.path.join(parentdir, args.folder)
            folder = os.path.realpath(folder)
            if not os.path.isdir(folder):
                parser.error(f'katalog nie istnieje: {folder}')
            key, _name, _description = _key_name_description(folder)
            if args.key and args.key != key:
                parser.error(f"--key '{args.key}' różni się od klucza modelu '{key}' "
                             f"(process_model.json albo nazwa folderu) - powstałby osobny model")
            result = sync_folder(db, folder, dry_run=args.dry_run,
                                 root=root if folder.startswith(root + os.sep) else None)
            print(f"[sync] {result.status:20s} {result.key:30s} {result.folder}"
                  + (f" ({result.detail})" if result.detail else ''))
            if result.status == 'error':
                return 1
            if not args.dry_run:
                db.commit()
            keys = [key]
        actor = None if args.dry_run else _actor(db, args.user)
        return 1 if publish_drafts(db, keys, actor, dry_run=args.dry_run) else 0
    finally:
        db.close()


if __name__ == '__main__':
    raise SystemExit(main())
