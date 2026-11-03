#!/usr/bin/env python3
# coding: utf-8
"""
Dane startowe dla obszaru "Zakupy" (galwork-book, czesc-3-obszary/dzialy/
zakupy.md) - NIE schema (to robi alembic), tylko wiersze konfiguracyjne,
które administrator mógłby też wpisać ręcznie przez istniejące ekrany
admina (ale "Dział zamówień" nie ma jeszcze własnego ekranu CRUD dla
OrgUnit - zob. zakupy.md, routing do działu zamówień). Idempotentny:
bezpieczny do wielokrotnego uruchomienia.

Zakłada:
  * `OrgUnit` "Dział zamówień" (`code=zakupy_dzial`, `keycloak_group=
    dzial_zakupow`) - candidateGroups zadań "Realizacja zamówienia" i
    "Potwierdzenie odbioru" w wniosek-zakupowy.bpmn muszą wskazywać na TĘ
    SAMĄ grupę Keycloak (zob. rejestr w lanes.md: lane'y BPMN powinny
    odwoływać się do OrgUnit, nie do nazw zadań).
  * `DictionaryEntry` dla słownika płaskiego "rodzaj-wydatku" (koszt
    operacyjny/inwestycja) - "projekt-mpk" NIE jest tu zaseedowany, bo to
    słownik administrowany ręcznie (ekran "Słowniki" w galwork-arena),
    referencja do projektów/MPK z ERP (zakupy.md, "Co zostaje poza
    Nodavią").

Użycie:
    python3 scripts/seed_zakupy.py
"""
from __future__ import annotations

import os
import sys

currentdir = os.path.dirname(os.path.abspath(__file__))
parentdir = os.path.dirname(currentdir)
sys.path.insert(0, parentdir)

from sqlalchemy import select  # noqa: E402

from db.session import SessionLocal  # noqa: E402
from wf.models import DictionaryEntry, OrgUnit  # noqa: E402

DZIAL_ZAMOWIEN_CODE = "zakupy_dzial"
DZIAL_ZAMOWIEN_KEYCLOAK_GROUP = "dzial_zakupow"

RODZAJ_WYDATKU_ENTRIES = [
    ("operating", "Koszt operacyjny", 10),
    ("investment", "Inwestycja", 20),
]


def seed_org_unit(db) -> None:
    existing = db.execute(
        select(OrgUnit).where(OrgUnit.code == DZIAL_ZAMOWIEN_CODE)
    ).scalars().first()
    if existing is not None:
        print(f"OrgUnit '{DZIAL_ZAMOWIEN_CODE}' już istnieje ({existing.id}) - bez zmian.")
        return
    org_unit = OrgUnit(
        code=DZIAL_ZAMOWIEN_CODE,
        name="Dział zamówień",
        keycloak_group=DZIAL_ZAMOWIEN_KEYCLOAK_GROUP,
    )
    db.add(org_unit)
    db.commit()
    print(f"Utworzono OrgUnit '{DZIAL_ZAMOWIEN_CODE}' (keycloak_group="
          f"'{DZIAL_ZAMOWIEN_KEYCLOAK_GROUP}') - id={org_unit.id}")


def seed_rodzaj_wydatku(db) -> None:
    for code, label, sort_order in RODZAJ_WYDATKU_ENTRIES:
        existing = db.execute(
            select(DictionaryEntry).where(
                DictionaryEntry.dictionary_key == "rodzaj-wydatku",
                DictionaryEntry.code == code,
            )
        ).scalars().first()
        if existing is not None:
            print(f"DictionaryEntry rodzaj-wydatku/{code} już istnieje - bez zmian.")
            continue
        db.add(DictionaryEntry(
            dictionary_key="rodzaj-wydatku", code=code, label=label, sort_order=sort_order,
        ))
        db.commit()
        print(f"Utworzono DictionaryEntry rodzaj-wydatku/{code} ({label}).")


def main() -> None:
    db = SessionLocal()
    try:
        seed_org_unit(db)
        seed_rodzaj_wydatku(db)
    finally:
        db.close()


if __name__ == "__main__":
    main()
