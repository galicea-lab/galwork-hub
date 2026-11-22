#!/usr/bin/env python3
# coding: utf-8
"""
(ZAK.1): uzgadnianie faktur zakupu ERPNext z obiegiem w hubie.

Szkice faktur (`Purchase Invoice`, docstatus 0) bez sprawy obiegu
(`[erpnext] invoice_process_key`, domyślnie `faktura-zakupu`) dostają sprawę -
np. gdy powiadomienie z n8n albo webhook ERPNext nie dotarło. Podstawowym
wyzwalaczem jest `POST /v1/wf/erpnext/invoices/notify`; ten skrypt to cron
zabezpieczający (np. co godzinę).

    python3 scripts/erpnext_invoices.py sync [--limit 200] [--dry-run]
    python3 scripts/erpnext_invoices.py start ACC-PINV-2026-00001

Użytkownik systemowy: `[wf] system_user` (albo `[edr] system_user`).
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.session import SessionLocal  # noqa: E402
from wf.services import purchase_invoice_flow  # noqa: E402
from wf.services.system_actor import SystemActorError, get_system_actor  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    sync = sub.add_parser('sync', help='szkice faktur bez sprawy -> nowe sprawy')
    sync.add_argument('--limit', type=int, default=200)
    sync.add_argument('--dry-run', action='store_true')
    start = sub.add_parser('start', help='sprawa dla jednej faktury')
    start.add_argument('name')
    args = parser.parse_args(argv)

    db = SessionLocal()
    try:
        try:
            actor = get_system_actor(db, display_name='ERPNext – faktury')
        except SystemActorError as exc:
            print(f'[erpnext_invoices] {exc}')
            return 2
        if args.command == 'sync':
            results = purchase_invoice_flow.sync_drafts(db, actor, limit=args.limit, dry_run=args.dry_run)
        else:
            results = [purchase_invoice_flow.start_for_invoice(db, args.name, actor)]
        for result in results:
            print(f'[erpnext_invoices] {result.status:12s} {result.invoice}'
                  + (f' -> {result.instance_id}' if result.instance_id else '')
                  + (f' ({result.detail})' if result.detail else ''))
        started = sum(1 for r in results if r.status == 'started')
        print(f'[erpnext_invoices] faktur: {len(results)}, nowych spraw: {started}')
        return 0
    finally:
        db.close()


if __name__ == '__main__':
    raise SystemExit(main())
