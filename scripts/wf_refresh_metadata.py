#!/usr/bin/env python3
# coding: utf-8
"""
2026-11-22: przeliczenie `process_instance.extracted_metadata` (kolumny list)
dla spraw, których wersja modelu ma `metadata_extraction_paths` - po migracji
e8a4c2f6b1d9 albo po zmianie sposobu liczenia. Nowe dane powstają same przy
każdym kroku sprawy; skrypt jest potrzebny tylko dla spraw, które stoją.

  python scripts/wf_refresh_metadata.py [--model KLUCZ] [--dry-run]
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select  # noqa: E402

from db.session import SessionLocal  # noqa: E402
from wf.models import ProcessInstance, ProcessModel, ProcessModelVersion  # noqa: E402
from wf.services import instance_metadata  # noqa: E402
from wf.services.process_service import get_service  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--model', help='klucz modelu procesu (domyślnie wszystkie)')
    parser.add_argument('--dry-run', action='store_true', help='tylko policz zmiany')
    args = parser.parse_args()

    service = get_service()
    changed = checked = 0
    with SessionLocal() as db:
        stmt = select(ProcessInstance).join(ProcessModelVersion)
        if args.model:
            stmt = stmt.join(ProcessModel).where(ProcessModel.key == args.model)
        for instance in db.execute(stmt).scalars():
            paths = instance_metadata.paths_of(instance.version.spec_json)
            if not paths:
                continue
            checked += 1
            workflow, _spec = service.load_workflow(instance)
            metadata = instance_metadata.extract(paths, instance_metadata.current_data(service.engine, workflow))
            if metadata != (instance.extracted_metadata or {}):
                changed += 1
                if not args.dry_run:
                    instance.extracted_metadata = metadata
        if not args.dry_run:
            db.commit()
    print(f'sprawy z kolumnami listy: {checked}, zmienione: {changed}'
          + (' (dry-run)' if args.dry_run else ''))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
