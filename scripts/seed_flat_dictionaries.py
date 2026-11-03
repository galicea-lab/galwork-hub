#!/usr/bin/env python3
# coding: utf-8
"""
Przeniesienie słowników PŁASKICH (kod/etykieta) z obiegów-słowników
SpiffWorkflow ("Krok słownikowy") do tabeli `dictionary_entry`
(wf/models/wf.py::DictionaryEntry) - dla wszystkich słowników zarejestrowanych
w `TABLE_DICTIONARIES` (wf/services/dictionary_service.py).

Definicja słownika (klucz + nazwa) żyje w `TABLE_DICTIONARIES`, w bazie są
tylko jego wiersze. Skrypt:

  1. dla każdego słownika z `TABLE_DICTIONARIES`, dla którego w hubie istnieje
     `ProcessModel` o kluczu == `dictionary_key` (stary obieg-słownik),
     przenosi jego instancje do `dictionary_entry`:
     `code` = `ProcessInstance.id` (to samo `value`, które zwracał
     `_internal_options` - wartości zapisane już w formularzach zostają
     ważne), `label` = `ProcessInstance.title`;
  2. seeduje wartości oczywiste z natury słownika (dziś: "dzien-tygodnia");
  3. wypisuje raport: słownik -> liczba wierszy / czy został stary obieg.

Idempotentny (istniejąca para dictionary_key+code jest pomijana).

Użycie:
    python3 scripts/seed_flat_dictionaries.py [--dry-run]
"""
from __future__ import annotations

import argparse
import os
import sys

currentdir = os.path.dirname(os.path.abspath(__file__))
parentdir = os.path.dirname(currentdir)
sys.path.insert(0, parentdir)

from sqlalchemy import func, select  # noqa: E402

from db.session import SessionLocal  # noqa: E402
from wf.models import DictionaryEntry, ProcessInstance, ProcessModel, ProcessModelVersion  # noqa: E402
from wf.services.dictionary_service import TABLE_DICTIONARIES  # noqa: E402

STATIC_ENTRIES: dict[str, list[tuple[str, str]]] = {
    "dzien-tygodnia": [
        ("1", "Poniedziałek"), ("2", "Wtorek"), ("3", "Środa"), ("4", "Czwartek"),
        ("5", "Piątek"), ("6", "Sobota"), ("7", "Niedziela"),
    ],
}


def _existing_codes(db, key: str) -> set[str]:
    return set(db.execute(
        select(DictionaryEntry.code).where(DictionaryEntry.dictionary_key == key)
    ).scalars())


def _add(db, key: str, code: str, label: str, sort_order: int, dry_run: bool,
         existing: set[str]) -> bool:
    if code in existing:
        return False
    if not dry_run:
        db.add(DictionaryEntry(dictionary_key=key, code=code, label=label,
                               sort_order=sort_order))
    existing.add(code)
    return True


def migrate_process_instances(db, key: str, dry_run: bool) -> tuple[bool, int]:
    """Zwraca (czy istnieje stary ProcessModel, ile wierszy dodano)."""
    model = db.execute(select(ProcessModel).where(ProcessModel.key == key)).scalars().first()
    if model is None:
        return False, 0
    instances = db.execute(
        select(ProcessInstance)
        .join(ProcessModelVersion, ProcessInstance.process_model_version_id == ProcessModelVersion.id)
        .where(ProcessModelVersion.process_model_id == model.id)
        .order_by(ProcessInstance.title)
    ).scalars().all()
    existing = _existing_codes(db, key)
    added = 0
    for i, instance in enumerate(instances):
        if instance.title and _add(db, key, str(instance.id), instance.title, (i + 1) * 10,
                                   dry_run, existing):
            added += 1
    return True, added


def seed_static(db, key: str, dry_run: bool) -> int:
    existing = _existing_codes(db, key)
    return sum(
        _add(db, key, code, label, (i + 1) * 10, dry_run, existing)
        for i, (code, label) in enumerate(STATIC_ENTRIES.get(key, []))
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dry-run", action="store_true", help="nic nie zapisuje")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        report = []
        for key, dictionary in TABLE_DICTIONARIES.items():
            has_model, migrated = migrate_process_instances(db, dictionary.dictionary_key, args.dry_run)
            seeded = seed_static(db, dictionary.dictionary_key, args.dry_run)
            report.append((key, dictionary.source_name, has_model, migrated, seeded))
        if args.dry_run:
            db.rollback()
        else:
            db.commit()

        counts = dict(db.execute(
            select(DictionaryEntry.dictionary_key, func.count())
            .group_by(DictionaryEntry.dictionary_key)
        ).all())
        print(f"{'słownik':42} {'wierszy':>7} {'+migr':>5} {'+seed':>5}  stary obieg w hubie")
        for key, name, has_model, migrated, seeded in report:
            print(f"{key:42} {counts.get(key, 0):7} {migrated:5} {seeded:5}  "
                  f"{'TAK (ProcessModel ' + key + ')' if has_model else '-'}")
        print(f"\nSłowników płaskich: {len(report)}"
              f"{' (DRY RUN - nic nie zapisano)' if args.dry_run else ''}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
