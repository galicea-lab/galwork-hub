#!/usr/bin/env python
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
# coding: utf-8
"""
Integracja Taiga - galwork-pm - ERPNext, polecenia administracyjne.

Konfiguracja: sekcje [taiga] i [erpnext] w config/config.ini (zob.
config/config.ini.example).

Użycie:
    python3 scripts/pm_taiga.py seed PLIK.json            # dane startowe (idempotentne)
    python3 scripts/pm_taiga.py register SLUG [--apply]   # atrybuty „Czas [h]” + webhook
    python3 scripts/pm_taiga.py persons ID [--apply]      # propozycje mapowania osób po e-mailu
    python3 scripts/pm_taiga.py reconcile [--project ID]  # timeline -> kolejka zdarzeń
    python3 scripts/pm_taiga.py process                   # klasyfikacja kolejki
    python3 scripts/pm_taiga.py audit                     # kontrola konfiguracji i dostaw
    python3 scripts/pm_taiga.py purge [--days 90]         # retencja surowych zdarzeń

Etap 2 - słownik rodzajów prac i dziennik:
    python3 scripts/pm_taiga.py baseline ID               # stan początkowy (raz, przed process)
    python3 scripts/pm_taiga.py map ID                    # bieżący słownik projektu
    python3 scripts/pm_taiga.py map ID SCOPE OBJ AT [--erp-project P] [--apply]
                                                          # zmiana (bez --apply: podgląd skutków)
    python3 scripts/pm_taiga.py map ID SCOPE OBJ - --apply  # usunięcie przypisania
    python3 scripts/pm_taiga.py confirm-person TAIGA_USER EMPLOYEE
    python3 scripts/pm_taiga.py summary ID [--week 2026-W48]
    python3 scripts/pm_taiga.py anomalies ID
    python3 scripts/pm_taiga.py close-month 2026-11
    python3 scripts/pm_taiga.py approve-week ID 2026-W48
    python3 scripts/pm_taiga.py recompute ID

Etap 3 - Timesheet w ERPNext (osoba × projekt × tydzień):
    python3 scripts/pm_taiga.py erp-check                 # pracownicy, projekty, rodzaje prac, stawki
    python3 scripts/pm_taiga.py erp-plan                  # plan bez połączenia z ERPNext
    python3 scripts/pm_taiga.py erp-sync --dry-run        # stan z ERPNext + plan, bez zapisu
    python3 scripts/pm_taiga.py erp-sync                  # zapis (wymaga [taiga] erp_sync=true)

Etap 5 - przegląd tygodnia (proces BPMN pm-przeglad-tygodnia):
    python3 scripts/pm_taiga.py review-start [--week 2026-W48] [--project ID]
                                                          # instancje przeglądu (domyślnie poprzedni tydzień)
    python3 scripts/pm_taiga.py remind [--week 2026-W48]  # powiadomienia dla osób bez godzin w tygodniu

Polecenia zmieniające Taigę (`register`) bez `--apply` tylko pokazują plan.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

currentdir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(currentdir))

from sqlalchemy import select  # noqa: E402

from db.session import SessionLocal  # noqa: E402
from pm.integrations.taiga import service  # noqa: E402
from pm.models.taiga import TaigaProjectLink  # noqa: E402


def _taiga():
    from connector.taiga import get_connector
    return get_connector()


def cmd_seed(db, args) -> int:
    with open(args.file, encoding="utf-8") as fh:
        data = json.load(fh)
    stats = service.load_seed(db, data)
    print("[pm_taiga seed] " + ", ".join(f"{k}={v}" for k, v in stats.items()))
    if data.get("allowed_activity_types"):
        print("[pm_taiga seed] allowed_activity_types - nie są zapisywane: dozwolone rodzaje prac "
              "to aktywne Activity Types w ERPNext, opcjonalnie [taiga] activity_types")
    return 0


def cmd_register(db, args) -> int:
    taiga = _taiga()
    webhook_url = args.webhook_url or taiga.config.get("webhook_url")
    result = service.register_project(db, taiga, slug=args.slug, webhook_url=webhook_url,
                                      apply=args.apply)
    mode = "" if args.apply else " (podgląd - bez zmian, dodaj --apply)"
    print(f"[pm_taiga register] projekt {args.slug} id={result.taiga_project_id}{mode}")
    for action in result.actions or ["nic do zrobienia"]:
        print(f"  - {action}")
    print(f"  atrybuty: {result.attributes}, webhook_id={result.webhook_id}")
    if not webhook_url:
        print("  UWAGA: brak [taiga] webhook_url - webhook nie był sprawdzany")
    if result.webhook_secret:
        print("  Sekret nowego webhooka (zapisz w konfiguracji huba, nie zostanie pokazany ponownie):")
        print(f"    TAIGA_WEBHOOK_SECRET_{result.taiga_project_id}={result.webhook_secret}")
    return 0


def cmd_persons(db, args) -> int:
    from connector.erpnext.erpnext import get_connector as erp_connector
    proposals = service.propose_person_links(db, _taiga(), erp_connector(),
                                             taiga_project_id=args.project, apply=args.apply)
    for p in proposals:
        print(f"  taiga {p.taiga_user_id:>5} {p.full_name or '':<30} -> {p.erp_employee or '-':<14} {p.status}")
    if not args.apply:
        print("[pm_taiga persons] podgląd - z --apply nowe propozycje trafią do pm_person_link "
              "jako niezatwierdzone")
    return 0


def _links(db, project_id: int | None) -> list[TaigaProjectLink]:
    query = select(TaigaProjectLink).where(TaigaProjectLink.active.is_(True))
    if project_id is not None:
        query = query.where(TaigaProjectLink.taiga_project_id == project_id)
    return list(db.execute(query).scalars())


def cmd_reconcile(db, args) -> int:
    from pm.integrations.taiga import ledger
    taiga = _taiga()
    for link in _links(db, args.project):
        created = service.reconcile_timeline(db, taiga, link)
        deleted = []
        if link.baseline_at is not None:
            # 2026--11-21: timeline nie zawiera usunięć - porównanie list id z REST
            deleted = ledger.detect_deletions(ledger.LedgerContext(db, taiga), link)
        print(f"[pm_taiga reconcile] projekt {link.taiga_slug or link.taiga_project_id}: "
              f"{created} nowych zdarzeń, kursor={link.timeline_cursor}, usunięte pozycje={len(deleted)}")
    return 0


def cmd_process(db, args) -> int:
    from pm.integrations.taiga import ledger
    taiga = _taiga()
    for link in _links(db, None):
        if link.baseline_at is None:
            print(f"[pm_taiga process] projekt {link.taiga_slug or link.taiga_project_id}: brak stanu "
                  "początkowego - zdarzenia czekają (uruchom: pm_taiga.py baseline "
                  f"{link.taiga_project_id})")
    result = service.process_pending(db, technical_user_id=taiga.technical_user_id,
                                     handlers=[ledger.make_handler(taiga)])
    print(f"[pm_taiga process] przetworzone={result.processed} zignorowane={result.ignored} "
          f"błędy={result.errors} czekające={result.waiting}")
    return 0


def cmd_audit(db, args) -> int:
    taiga = _taiga()
    findings = service.audit(db, taiga, webhook_url=taiga.config.get("webhook_url"))
    for f in findings:
        print(f"  [{f.level}] projekt {f.taiga_project_id}: {f.code} - {f.message}")
    print(f"[pm_taiga audit] {len(findings)} uwag")
    return 1 if any(f.level == "error" for f in findings) else 0


def cmd_purge(db, args) -> int:
    print(f"[pm_taiga purge] usunięto {service.purge_events(db, older_than_days=args.days)} zdarzeń")
    return 0


def _link_or_exit(db, project_id: int) -> TaigaProjectLink:
    link = service.get_link(db, project_id)
    if link is None:
        sys.exit(f"projekt Taigi {project_id} nie ma powiązania (pm_taiga.py seed / register)")
    return link


def _print_entries(plans) -> None:
    for plan in plans:
        item = plan.item
        print(f"  {item.kind} #{item.ref or item.taiga_id} {(item.subject or '')[:40]}")
        for e in plan.entries:
            print(f"      {e.work_week} (z {e.origin_week}) {e.hours_delta:+} h  {e.key.erp_employee or e.key.taiga_user_id}"
                  f" / {e.key.erp_project} / {e.key.activity_type}  [{e.reason}]")


def cmd_baseline(db, args) -> int:
    from pm.integrations.taiga import ledger
    link = _link_or_exit(db, args.project)
    if link.baseline_at is not None and not args.force:
        print(f"[pm_taiga baseline] projekt ma już stan początkowy z {link.baseline_at} "
              "(--force dopisze tylko brakujące pozycje)")
        return 0
    stats = ledger.baseline(ledger.LedgerContext(db, _taiga()), link)
    print(f"[pm_taiga baseline] pozycje: utworzone={stats['created']} pominięte={stats['skipped']}; "
          "godziny już wpisane w Taidze nie będą księgowane")
    return 0


def cmd_map(db, args) -> int:
    from pm.integrations.taiga import activity, ledger
    from pm.models.taiga_ledger import ActivityMap
    _link_or_exit(db, args.project)
    if args.scope is None:
        rows = db.execute(select(ActivityMap).where(ActivityMap.taiga_project_id == args.project,
                                                    ActivityMap.valid_to.is_(None))
                          .order_by(ActivityMap.scope, ActivityMap.taiga_object_id)).scalars()
        for r in rows:
            print(f"  {r.scope:<10} {r.taiga_object_id:>6} -> {r.activity_type or '-':<20} "
                  f"{r.erp_project or ''}  ({r.source}, od {r.valid_from:%F})")
        return 0
    if args.object is None or args.activity_type is None:
        sys.exit("podaj: SCOPE OBIEKT ACTIVITY_TYPE (albo '-' aby usunąć)")
    activity_type = None if args.activity_type == "-" else args.activity_type
    ctx = ledger.LedgerContext(db, _taiga())
    if not args.apply:
        plans = ledger.preview_mapping(ctx, taiga_project_id=args.project, scope=args.scope,
                                       taiga_object_id=args.object, activity_type=activity_type,
                                       erp_project=args.erp_project)
        print(f"[pm_taiga map] podgląd - {len(plans)} pozycji zmieni rozliczenie (dodaj --apply):")
        _print_entries(plans)
        return 0
    from connector.erpnext.erpnext import get_connector as erp_connector
    narrowed = ctx.taiga.config.get("activity_types") if ctx.taiga else None
    plans = ledger.apply_mapping(
        ctx, taiga_project_id=args.project, scope=args.scope, taiga_object_id=args.object,
        activity_type=activity_type, erp_project=args.erp_project, created_by="cli",
        allowed_activity_types=lambda: activity.allowed_activity_types(erp_connector(), narrowed))
    print(f"[pm_taiga map] zapisano; przeniesienia w dzienniku dla {len(plans)} pozycji:")
    _print_entries(plans)
    return 0


def cmd_confirm_person(db, args) -> int:
    from pm.integrations.taiga import ledger
    plans = ledger.confirm_person(ledger.LedgerContext(db, _taiga()), args.taiga_user, args.employee)
    print(f"[pm_taiga confirm-person] {args.taiga_user} -> {args.employee}; przeniesienia: {len(plans)} pozycji")
    _print_entries(plans)
    return 0


def cmd_summary(db, args) -> int:
    from pm.integrations.taiga import ledger
    rows = ledger.week_summary(db, args.project, args.week)
    states = ledger.week_states(db, args.project, sorted({r.week for r in rows}))
    for r in rows:
        print(f"  {r.week} [{states[r.week]:<8}] {r.erp_employee or f'taiga:{r.taiga_user_id}':<14} "
              f"{r.erp_project or '-':<12} {r.activity_type or '(nieprzypisane)':<20} {r.hours:>8} h")
    print(f"[pm_taiga summary] {len(rows)} wierszy")
    return 0


def cmd_anomalies(db, args) -> int:
    from pm.integrations.taiga import ledger
    found = ledger.anomalies(ledger.LedgerContext(db), args.project)
    for a in found:
        print(f"  [{a.code}] {a.message}")
    print(f"[pm_taiga anomalies] {len(found)} uwag")
    return 0


def cmd_close_month(db, args) -> int:
    from pm.integrations.taiga import periods
    row = periods.close_month(db, args.month, closed_by="cli")
    print(f"[pm_taiga close-month] {row.month} zamknięty {row.closed_at:%F %T}")
    return 0


def cmd_approve_week(db, args) -> int:
    from pm.integrations.taiga import periods
    row = periods.approve_week(db, args.project, args.week, approved_by="cli")
    print(f"[pm_taiga approve-week] projekt {args.project} {row.week} zatwierdzony {row.approved_at:%F %T}")
    return 0


def cmd_recompute(db, args) -> int:
    from pm.integrations.taiga import ledger
    _link_or_exit(db, args.project)
    plans = ledger.recompute(ledger.LedgerContext(db, _taiga()), args.project)
    print(f"[pm_taiga recompute] przeniesienia dla {len(plans)} pozycji")
    _print_entries(plans)
    return 0


def _print_plan(plan) -> None:
    for doc in plan.docs:
        if doc.action == "none":
            continue
        name = doc.record.timesheet_name if doc.record and doc.record.timesheet_name else "-"
        lines = ", ".join(f"{a} {h} h" for a, h in sorted(doc.lines.items())) or "(brak godzin)"
        print(f"  {doc.action:<12} {doc.key.week} {doc.key.erp_employee:<14} {doc.key.erp_project:<12} "
              f"{name:<18} {lines}{'  [zatwierdzony]' if doc.approved else ''}")
    for u in plan.unrealized:
        print(f"  [korekta niezrealizowana] {u.erp_employee} / {u.erp_project} / {u.activity_type}: "
              f"{u.hours} h (po {u.last_week}) - do decyzji PM")
    for reason, hours in sorted(plan.unpostable.items()):
        if hours:
            print(f"  [nie do wysłania] {reason}: {hours} h (zob. anomalies)")


def cmd_erp_check(db, args) -> int:
    from connector.erpnext.erpnext import get_connector as erp_connector
    from pm.integrations.taiga import erp_sync
    findings = erp_sync.check(db, erp_connector())
    for f in findings:
        print(f"  [{f.level}] {f.code} - {f.message}")
    print(f"[pm_taiga erp-check] {len(findings)} uwag")
    return 1 if any(f.level == "error" for f in findings) else 0


def cmd_erp_plan(db, args) -> int:
    from pm.integrations.taiga import erp_sync
    plan = erp_sync.build_plan(db)
    _print_plan(plan)
    print(f"[pm_taiga erp-plan] dokumenty do zmiany: {sum(1 for d in plan.docs if d.action != 'none')}")
    return 0


def cmd_erp_sync(db, args) -> int:
    from connector.erpnext.erpnext import get_connector as erp_connector
    from pm.integrations.taiga import erp_sync
    enabled = (_taiga().config.get("erp_sync") or "").strip().lower() in ("1", "true", "yes", "tak")
    if not args.dry_run and not enabled:
        print("[pm_taiga erp-sync] zapis do ERPNext wyłączony - ustaw [taiga] erp_sync=true "
              "(albo uruchom z --dry-run)")
        return 0
    result = erp_sync.ErpSync(db, erp_connector()).sync(dry_run=args.dry_run)
    _print_plan(result.plan)
    for key, message in result.errors:
        print(f"  [odrzucone] {key.week} {key.erp_employee} {key.erp_project}: {message}")
    mode = " (dry-run)" if args.dry_run else ""
    print(f"[pm_taiga erp-sync]{mode} " + (", ".join(f"{k}={v}" for k, v in sorted(result.actions.items()))
                                          or "brak zmian") + f", odrzucone={len(result.errors)}")
    return 1 if result.errors else 0


def cmd_review_start(db, args) -> int:
    from pm.integrations.taiga import review
    try:
        actor = review.system_actor(db)
    except LookupError as exc:
        sys.exit(f"[pm_taiga review-start] {exc}")
    for r in review.start_reviews(db, actor, week=args.week, project=args.project):
        print(f"  projekt {r.taiga_project_id} {r.week}: {r.status} {r.detail}")
    return 0


def cmd_remind(db, args) -> int:
    from connector.erpnext.erpnext import get_connector as erp_connector
    from pm.integrations.taiga import review
    notified = review.remind(db, erp_connector(), week=args.week, link_base=_taiga().config.get("pm_url"))
    print(f"[pm_taiga remind] powiadomienia: {len(notified)} {', '.join(notified)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("seed")
    p.add_argument("file")
    p.set_defaults(fn=cmd_seed)
    p = sub.add_parser("register")
    p.add_argument("slug")
    p.add_argument("--webhook-url", help="domyślnie [taiga] webhook_url")
    p.add_argument("--apply", action="store_true")
    p.set_defaults(fn=cmd_register)
    p = sub.add_parser("persons")
    p.add_argument("project", type=int, help="id projektu Taigi")
    p.add_argument("--apply", action="store_true")
    p.set_defaults(fn=cmd_persons)
    p = sub.add_parser("reconcile")
    p.add_argument("--project", type=int)
    p.set_defaults(fn=cmd_reconcile)
    p = sub.add_parser("process")
    p.set_defaults(fn=cmd_process)
    p = sub.add_parser("audit")
    p.set_defaults(fn=cmd_audit)
    p = sub.add_parser("purge")
    p.add_argument("--days", type=int, default=90)
    p.set_defaults(fn=cmd_purge)
    p = sub.add_parser("baseline")
    p.add_argument("project", type=int)
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_baseline)
    p = sub.add_parser("map")
    p.add_argument("project", type=int)
    p.add_argument("scope", nargs="?", choices=["task", "userstory", "epic", "role"])
    p.add_argument("object", nargs="?", type=int)
    p.add_argument("activity_type", nargs="?")
    p.add_argument("--erp-project")
    p.add_argument("--apply", action="store_true")
    p.set_defaults(fn=cmd_map)
    p = sub.add_parser("confirm-person")
    p.add_argument("taiga_user", type=int)
    p.add_argument("employee")
    p.set_defaults(fn=cmd_confirm_person)
    p = sub.add_parser("summary")
    p.add_argument("project", type=int)
    p.add_argument("--week")
    p.set_defaults(fn=cmd_summary)
    p = sub.add_parser("anomalies")
    p.add_argument("project", type=int)
    p.set_defaults(fn=cmd_anomalies)
    p = sub.add_parser("close-month")
    p.add_argument("month")
    p.set_defaults(fn=cmd_close_month)
    p = sub.add_parser("approve-week")
    p.add_argument("project", type=int)
    p.add_argument("week")
    p.set_defaults(fn=cmd_approve_week)
    p = sub.add_parser("recompute")
    p.add_argument("project", type=int)
    p.set_defaults(fn=cmd_recompute)
    p = sub.add_parser("erp-check")
    p.set_defaults(fn=cmd_erp_check)
    p = sub.add_parser("erp-plan")
    p.set_defaults(fn=cmd_erp_plan)
    p = sub.add_parser("erp-sync")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_erp_sync)
    p = sub.add_parser("review-start")
    p.add_argument("--week")
    p.add_argument("--project", type=int)
    p.set_defaults(fn=cmd_review_start)
    p = sub.add_parser("remind")
    p.add_argument("--week")
    p.set_defaults(fn=cmd_remind)
    args = parser.parse_args()
    db = SessionLocal()
    try:
        return args.fn(db, args)
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
