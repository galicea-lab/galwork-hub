#!/usr/bin/env python
# coding: utf-8
"""
2026-11-17: wysyłka dokumentów huba do Paperless-ngx (scripts/cron_wf.sh) -
ponowienie wysyłek nieudanych przy zapisie i odczyt wyniku konsumpcji
(wf/services/document_ingest.py). Dokumenty e-Doręczeń obsługuje scripts/edr.py.

Użytkownik crona: `[wf] system_user` (albo `[edr] system_user`), jak w wf_wake.py.

Użycie:
    python3 scripts/wf_documents.py            # wysyłka + odczyt wyników
    python3 scripts/wf_documents.py --list     # tylko wypisz oczekujące wersje
"""
import argparse
import os
import sys

currentdir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(currentdir))

from connector.paperless import get_connector  # noqa: E402
from db.session import SessionLocal  # noqa: E402
from wf.services import document_ingest  # noqa: E402

sys.path.insert(0, currentdir)
from wf_wake import _system_user  # noqa: E402


def main() -> int:
  parser = argparse.ArgumentParser(description=__doc__,
                                   formatter_class=argparse.RawDescriptionHelpFormatter)
  parser.add_argument('--list', action='store_true', help='tylko wypisz oczekujące wersje')
  parser.add_argument('--limit', type=int, default=50)
  args = parser.parse_args()
  db = SessionLocal()
  try:
    if args.list:
      for version in document_ingest.pending_versions(db, args.limit):
        print(f"{version.document_id} v{version.version_no} {version.ingest_status.value} "
              f"{version.file_name} {version.ingest_error or ''}")
      return 0
    paperless = get_connector()
    result = document_ingest.submit_pending(db, paperless, limit=args.limit)
    handled = document_ingest.poll_submitted(db, paperless, actor=_system_user(db),
                                             limit=args.limit)
    print(f"wysłane: {result.submitted}, do ponowienia: {result.retried_later}, "
          f"odrzucone: {result.rejected}, wyniki konsumpcji: {handled}")
    return 1 if result.rejected else 0
  finally:
    db.close()


if __name__ == '__main__':
  sys.exit(main())
