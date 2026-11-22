# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Słownik obiekt Taigi -> Activity Type (rodzaj pracy) i jego rozstrzyganie.

Activity Type to rodzaj pracy (programowanie, testowanie, ...), nie
konkretna praca. Kolejność rozstrzygania dla pozycji (pierwsze trafienie
wygrywa, rozdz. 5.1 projektu): zadanie -> US -> epika -> rola wykonawcy ->
domyślny rodzaj pracy projektu -> brak (triaż). Projekt ERPNext: pierwsze
niepuste nadpisanie z zadania / US / epiki, w przeciwnym razie projekt
z powiązania. Tagi `at:` (rozdz. 5.2) celowo pominięte w MVP.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from pm.models.taiga import TaigaProjectLink
from pm.models.taiga_ledger import ActivityMap, ActivityMapScope, TaigaItem


class ActivityMapError(ValueError):
    """Niepoprawne przypisanie w słowniku."""


@dataclass(frozen=True)
class MapEntry:
    activity_type: str | None
    erp_project: str | None
    map_id: int | None = None


class MapIndex:
    """Bieżące (niezamknięte) przypisania słownika jednego projektu, w pamięci."""

    def __init__(self, entries: dict[tuple[str, int], MapEntry]):
        self.entries = dict(entries)

    @classmethod
    def load(cls, db: Session, taiga_project_id: int) -> MapIndex:
        rows = db.execute(select(ActivityMap).where(
            ActivityMap.taiga_project_id == taiga_project_id,
            ActivityMap.valid_to.is_(None))).scalars()
        return cls({(r.scope, r.taiga_object_id): MapEntry(r.activity_type, r.erp_project, r.id) for r in rows})

    def get(self, scope: str, object_id: int | None) -> MapEntry | None:
        if object_id is None:
            return None
        return self.entries.get((scope, object_id))

    def with_entry(self, scope: str, object_id: int, entry: MapEntry | None) -> MapIndex:
        """Kopia z podmienionym przypisaniem - do podglądu skutków zmiany (Z4)."""
        copy = MapIndex(self.entries)
        if entry is None or (entry.activity_type is None and entry.erp_project is None):
            copy.entries.pop((scope, object_id), None)
        else:
            copy.entries[(scope, object_id)] = entry
        return copy


@dataclass(frozen=True)
class Resolution:
    activity_type: str | None
    erp_project: str | None
    activity_source: str      # 2026-11-21: task | userstory | epic | role | project | none
    project_source: str       # 2026-11-21: task | userstory | epic | project | none


def resolve(item: TaigaItem, link: TaigaProjectLink, index: MapIndex,
            user_story: TaigaItem | None = None, role_id: int | None = None) -> Resolution:
    """Rodzaj pracy i projekt ERPNext dla pozycji (zadania albo US)."""
    chain: list[tuple[str, MapEntry | None]] = []
    if item.kind == "task":
        chain.append(("task", index.get(ActivityMapScope.TASK, item.taiga_id)))
        us_id = item.user_story_id
        epics = (user_story.epic_ids if user_story is not None else None) or []
    else:
        us_id = item.taiga_id
        epics = item.epic_ids or []
    chain.append(("userstory", index.get(ActivityMapScope.USERSTORY, us_id)))
    # 2026-11-21: US w kilku epikach - pierwsza (najmniejsze id) z przypisaniem
    for epic_id in sorted(epics):
        entry = index.get(ActivityMapScope.EPIC, epic_id)
        if entry is not None:
            chain.append(("epic", entry))
            break
    activity_type, activity_source = None, "none"
    erp_project, project_source = None, "none"
    for source, entry in chain:
        if entry is None:
            continue
        if activity_type is None and entry.activity_type:
            activity_type, activity_source = entry.activity_type, source
        if erp_project is None and entry.erp_project:
            erp_project, project_source = entry.erp_project, source
    role = role_id if role_id is not None else item.assigned_role_id
    if activity_type is None:
        entry = index.get(ActivityMapScope.ROLE, role)
        if entry is not None and entry.activity_type:
            activity_type, activity_source = entry.activity_type, "role"
    if activity_type is None and link.default_activity_type:
        activity_type, activity_source = link.default_activity_type, "project"
    if erp_project is None and link.erp_project:
        erp_project, project_source = link.erp_project, "project"
    return Resolution(activity_type, erp_project, activity_source, project_source)


def set_mapping(db: Session, *, taiga_project_id: int, scope: str, taiga_object_id: int,
                activity_type: str | None, erp_project: str | None = None, source: str = "manual",
                created_by: str | None = None, note: str | None = None,
                allowed_activity_types: Iterable[str] | Callable[[], Iterable[str]] | None = None,
                now: datetime | None = None) -> ActivityMap | None:
    """Nowa wersja przypisania (poprzednia dostaje `valid_to`); oba pola puste = usunięcie.

    Nie zapisuje dziennika - po zmianie słownika wywołaj przeliczenie
    (`ledger.recompute_for_mapping`), które przeniesie godziny.
    """
    if scope not in ActivityMapScope.ALL:
        raise ActivityMapError(f"Nieznany zakres {scope!r} (dozwolone: {', '.join(ActivityMapScope.ALL)})")
    if activity_type and allowed_activity_types is not None:
        allowed = set(allowed_activity_types() if callable(allowed_activity_types) else allowed_activity_types)
        if activity_type not in allowed:
            raise ActivityMapError(f"Activity Type {activity_type!r} nie jest dozwolony "
                                   f"(dozwolone: {', '.join(sorted(allowed))})")
    now = now or datetime.now(UTC)
    current = db.execute(select(ActivityMap).where(
        ActivityMap.taiga_project_id == taiga_project_id, ActivityMap.scope == scope,
        ActivityMap.taiga_object_id == taiga_object_id, ActivityMap.valid_to.is_(None))).scalars().all()
    if len(current) == 1 and (current[0].activity_type, current[0].erp_project) == (activity_type, erp_project):
        return current[0]
    for row in current:
        row.valid_to = now
    row = None
    if activity_type or erp_project:
        row = ActivityMap(taiga_project_id=taiga_project_id, scope=scope, taiga_object_id=taiga_object_id,
                          activity_type=activity_type, erp_project=erp_project, source=source,
                          valid_from=now, created_by=created_by, note=note)
        db.add(row)
    db.commit()
    return row


def mapping_history(db: Session, taiga_project_id: int, scope: str, taiga_object_id: int) -> list[ActivityMap]:
    return list(db.execute(select(ActivityMap).where(
        ActivityMap.taiga_project_id == taiga_project_id, ActivityMap.scope == scope,
        ActivityMap.taiga_object_id == taiga_object_id).order_by(ActivityMap.valid_from, ActivityMap.id)).scalars())


def allowed_activity_types(erp, narrowed: str | None = None) -> list[str]:
    """Aktywne Activity Types z ERPNext, opcjonalnie zawężone listą (`[taiga] activity_types`)."""
    names = [row["name"] for row in erp.get_list("Activity Type", filters=[["disabled", "=", 0]],
                                                  fields=["name"], limit=500)]
    if narrowed:
        wanted = {name.strip() for name in narrowed.split(",") if name.strip()}
        names = [name for name in names if name in wanted]
    return sorted(names)
