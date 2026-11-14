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
    # 2026-11-12: kody = warunki `if` w galwork-models/dziekanat/prosba_studenta/
    # zgloszenie-schema.json (generowany z src/rodzaje.json). Nowy kod bez
    # sekcji w schemacie pokaże tylko sekcje wspólne.
    "rodzaj-pisma-studenta": [
        ("F-ISS", "Podanie o ISS"),
        ("PMDS", "Wniosek o przyznanie miejsca w Domu Studenta"),
        ("POD", "Podanie uniwersalne do Rektora/Dziekana/Dyrektora"),
        ("PPT", "Podanie o wyrażenie zgody na przywrócenie terminu"),
        ("PSS", "Podanie o wyrażenie zgody na powtarzanie semestru studiów"),
        ("PZSK", "Podanie o zmianę studiowanego kierunku (PZSK)"),
        ("TZPD", "Podanie o przedłużenie terminu złożenia pracy dyplomowej (Dziekan)"),
        ("UUZ", "Podanie o udzielenie urlopu od zajęć"),
        ("WS", "Podanie o wyrażenie zgody na wznowienie studiów"),
        ("ZA", "Zaświadczenie absolwenta"),
        ("ZKS", "Podanie o zmianę studiowanego kierunku (ZKS)"),
        ("ZOS", "Podanie o zwolnienie z opłat za kształcenie"),
        ("ZPD", "Podanie o przedłużenie terminu złożenia pracy dyplomowej (Dyrektor)"),
        ("ZPS", "Podanie o zmianę podstawy studiowania"),
        ("ZS", "Prośba o wydanie zaświadczenia o studiowaniu"),
        ("ZWW", "Podanie o wyrażenie zgody na wpis warunkowy"),
    ],
    "dzien-tygodnia": [
        ("1", "Poniedziałek"), ("2", "Wtorek"), ("3", "Środa"), ("4", "Czwartek"),
        ("5", "Piątek"), ("6", "Sobota"), ("7", "Niedziela"),
    ],
    # BEGIN 2026-11-13: słowniki wspólne dla uczelni (wzorce galwork-models);
    # treść do weryfikacji z aktualnymi przepisami przed wdrożeniem.
    # Art. 108 ustawy Prawo o szkolnictwie wyższym i nauce: ust. 1 - skreślenie
    # obowiązkowe, ust. 2 - fakultatywne.
    "powod-skreslenia": [
        ("108-1-1", "Niepodjęcie studiów"),
        ("108-1-2", "Rezygnacja ze studiów"),
        ("108-1-3", "Niezłożenie w terminie pracy dyplomowej lub egzaminu dyplomowego"),
        ("108-1-4", "Ukaranie karą dyscyplinarną wydalenia z uczelni"),
        ("108-2-1", "Brak udziału w obowiązkowych zajęciach"),
        ("108-2-2", "Brak postępów w nauce"),
        ("108-2-3", "Nieuzyskanie zaliczenia semestru lub roku w określonym terminie"),
        ("108-2-4", "Niewniesienie opłat związanych z odbywaniem studiów"),
    ],
    # Kategorie archiwalne: A - materiały archiwalne, B<n> - dokumentacja
    # niearchiwalna przechowywana n lat, BE<n> - po n latach ekspertyza
    # archiwum, Bc - krótkotrwała, brakowana po wykorzystaniu.
    "kategoria-archiwizacji": [
        ("A", "A - materiały archiwalne"),
        ("BE5", "BE5 - ekspertyza po 5 latach"), ("BE10", "BE10 - ekspertyza po 10 latach"),
        ("BE25", "BE25 - ekspertyza po 25 latach"), ("BE50", "BE50 - ekspertyza po 50 latach"),
        ("B2", "B2 - przechowywanie 2 lata"), ("B5", "B5 - przechowywanie 5 lat"),
        ("B10", "B10 - przechowywanie 10 lat"), ("B25", "B25 - przechowywanie 25 lat"),
        ("B50", "B50 - przechowywanie 50 lat"),
        ("Bc", "Bc - dokumentacja krótkotrwała"),
    ],
    # Kodeks pracy + urlopy nauczycieli akademickich (ustawa PSWN).
    "rodzaje-urlopow": [
        ("wypoczynkowy", "Urlop wypoczynkowy"),
        ("na-zadanie", "Urlop na żądanie"),
        ("okolicznosciowy", "Urlop okolicznościowy"),
        ("bezplatny", "Urlop bezpłatny"),
        ("opiekunczy", "Urlop opiekuńczy"),
        ("opieka-nad-dzieckiem", "Zwolnienie na opiekę nad dzieckiem"),
        ("macierzynski", "Urlop macierzyński"),
        ("rodzicielski", "Urlop rodzicielski"),
        ("ojcowski", "Urlop ojcowski"),
        ("wychowawczy", "Urlop wychowawczy"),
        ("szkoleniowy", "Urlop szkoleniowy"),
        ("naukowy", "Urlop naukowy"),
        ("zdrowotny", "Urlop dla poratowania zdrowia"),
    ],
    # END
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
