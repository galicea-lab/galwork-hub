# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""2026-11-21: normalizacja zdarzeń Taigi (webhook i timeline) do `TaigaChange`.

Jedyne miejsce, które zna kształt payloadów Taigi (warstwa antykorupcyjna).
Ustalenia z instancji Taigi 6, na których opiera się ten moduł:

- `by` / `data.user` nie zawiera e-maila - osobę identyfikuje `id`,
- godziny (atrybut własny) w webhooku i timeline są kluczowane NAZWĄ atrybutu;
  zmiana przychodzi w `diff.custom_attributes.{new,changed,deleted}`,
  a wyczyszczenie wartości to `changed` z `value_diff: [stara, null]`,
- `diff.assigned_to` / `assigned_users` niesie `full_name`, nie id - bieżący
  wykonawca jest tylko w `data.assigned_to` webhooka,
- przy `delete` webhook ma `custom_attributes_values = null`,
- powiązanie epika-US to osobny typ `relateduserstory`,
- timeline nie zawiera zdarzeń usunięcia ani bieżącego stanu obiektu, tylko
  różnice (`values_diff`); brakujący stan dociąga przetwarzanie (`needs_fetch`).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

#: 2026-11-21: typy obiektów, które integracja przetwarza; pozostałe są ignorowane
RELEVANT_TYPES = frozenset({"task", "userstory", "epic", "relateduserstory"})

#: 2026-11-21: pola diff bez znaczenia dla rozliczeń (kolejność na tablicach)
NOISE_FIELDS = frozenset({"kanban_order", "taskboard_order", "us_order", "backlog_order",
                          "sprint_order", "epics_order"})


class TaigaPayloadError(ValueError):
    """Payload Taigi o nieoczekiwanym kształcie."""


@dataclass
class TaigaChange:
    source: str                      # 2026-11-21: webhook | timeline
    project_id: int | None
    object_type: str                 # 2026-11-21: task | userstory | epic | relateduserstory | ...
    action: str                      # 2026-11-21: create | change | delete
    object_id: int | None
    occurred_at: datetime | None
    by_user_id: int | None
    ref: int | None = None
    subject: str | None = None
    user_story_id: int | None = None  # 2026-11-21: zadanie -> jego US; relacja -> US
    epic_id: int | None = None        # 2026-11-21: relacja epika-US
    changed_fields: frozenset[str] = frozenset()
    # 2026-11-21: stan po zmianie - *_known=False oznacza „nie wiadomo z tego zdarzenia”
    hours: Decimal | None = None
    hours_known: bool = False
    hours_changed: bool = False
    hours_error: str | None = None
    assigned_to_id: int | None = None
    assigned_known: bool = False
    assigned_users: tuple[int, ...] | None = None
    is_closed: bool | None = None
    tags: tuple[str, ...] | None = None
    # 2026-11-21: dla zadania - godziny wpisane na jego US (wykrycie podwójnego liczenia)
    user_story_hours: Decimal | None = None
    # 2026-11-21: epiki US - znane tylko ze stanu pobranego przez REST (`from_rest`)
    epic_ids: tuple[int, ...] | None = None
    needs_fetch: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def relevant(self) -> bool:
        return self.object_type in RELEVANT_TYPES


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise TaigaPayloadError(f"Niepoprawna data: {value!r}") from exc


def _hours_value(value: Any) -> tuple[Decimal | None, str | None]:
    """Wartość atrybutu godzin -> (Decimal | None, błąd); None/'' = brak wartości."""
    if value is None or value == "":
        return None, None
    if isinstance(value, bool):
        return None, f"niepoprawna wartość godzin: {value!r}"
    try:
        return Decimal(str(value)), None
    except (InvalidOperation, ValueError):
        return None, f"niepoprawna wartość godzin: {value!r}"


def _id(obj: Any) -> int | None:
    if isinstance(obj, dict):
        value = obj.get("id")
        return int(value) if value is not None else None
    if isinstance(obj, int):
        return obj
    return None


def _custom_attributes_change(diff: dict, hours_name: str) -> tuple[bool, Any]:
    """Czy diff `custom_attributes` zmienia atrybut godzin; zwraca (zmiana, nowa wartość)."""
    ca = diff.get("custom_attributes")
    if not isinstance(ca, dict):
        return False, None
    for group in ("new", "changed"):
        for item in ca.get(group) or []:
            if item.get("name") == hours_name:
                value_diff = item.get("value_diff")
                if isinstance(value_diff, list) and len(value_diff) == 2:
                    return True, value_diff[1]
                return True, item.get("value")
    for item in ca.get("deleted") or []:
        if item.get("name") == hours_name:
            return True, None
    return False, None


def _payload_project_id(data: dict) -> int | None:
    for key in ("project",):
        if isinstance(data.get(key), dict):
            return _id(data[key])
        if isinstance(data.get(key), int):
            return data[key]
    for key in ("epic", "user_story", "userstory"):
        nested = data.get(key)
        if isinstance(nested, dict) and isinstance(nested.get("project"), dict):
            return _id(nested["project"])
    return None


def from_webhook(payload: dict, *, hours_name: str) -> TaigaChange:
    """Webhook Taigi (treść żądania) -> TaigaChange."""
    if not isinstance(payload, dict):
        raise TaigaPayloadError("Webhook: treść nie jest obiektem JSON")
    object_type = payload.get("type")
    action = payload.get("action")
    if not object_type or not action:
        raise TaigaPayloadError("Webhook: brak pól type/action")
    data = payload.get("data") or {}
    change = TaigaChange(
        source="webhook",
        project_id=_payload_project_id(data) if isinstance(data, dict) else None,
        object_type=object_type,
        action=action,
        object_id=_id(data) if isinstance(data, dict) else None,
        occurred_at=parse_datetime(payload.get("date")),
        by_user_id=_id(payload.get("by")),
    )
    if not change.relevant or not isinstance(data, dict):
        return change

    change.ref = data.get("ref")
    change.subject = data.get("subject")
    diff = ((payload.get("change") or {}).get("diff")) or {}
    change.changed_fields = frozenset(k for k in diff if k not in NOISE_FIELDS)

    if object_type == "relateduserstory":
        change.epic_id = _id(data.get("epic"))
        change.user_story_id = _id(data.get("user_story"))
        return change

    if isinstance(data.get("status"), dict):
        change.is_closed = data["status"].get("is_closed")
    if isinstance(data.get("tags"), list):
        change.tags = tuple(str(t[0] if isinstance(t, list) else t) for t in data["tags"])

    if object_type in ("task", "userstory"):
        if action == "delete":
            # 2026-11-21: Taiga nie wysyła wartości atrybutów przy usunięciu -
            # godziny do odwrócenia bierze przetwarzanie z własnego stanu
            change.hours_known = False
        else:
            cav = data.get("custom_attributes_values")
            if isinstance(cav, dict):
                change.hours, change.hours_error = _hours_value(cav.get(hours_name))
                change.hours_known = change.hours_error is None
        changed, _ = _custom_attributes_change(diff, hours_name)
        change.hours_changed = changed or (action == "create" and change.hours is not None)
        change.assigned_to_id = _id(data.get("assigned_to"))
        change.assigned_known = "assigned_to" in data
        if object_type == "userstory" and isinstance(data.get("assigned_users"), list):
            change.assigned_users = tuple(int(u) for u in data["assigned_users"])

    if object_type == "task":
        user_story = data.get("user_story")
        change.user_story_id = _id(user_story)
        if isinstance(user_story, dict) and isinstance(user_story.get("custom_attributes_values"), dict):
            change.user_story_hours, _ = _hours_value(
                user_story["custom_attributes_values"].get(hours_name))
    return change


def _timeline_type(event_type: str) -> tuple[str, str]:
    """`tasks.task.change` -> ("task", "change")."""
    parts = (event_type or "").split(".")
    if len(parts) != 3:
        raise TaigaPayloadError(f"Timeline: nieznany event_type {event_type!r}")
    return parts[1], parts[2]


def from_timeline(entry: dict, *, hours_name: str) -> TaigaChange:
    """Wpis timeline'u projektu -> TaigaChange (tylko różnice, bez pełnego stanu)."""
    if not isinstance(entry, dict):
        raise TaigaPayloadError("Timeline: wpis nie jest obiektem JSON")
    object_type, action = _timeline_type(entry.get("event_type"))
    data = entry.get("data") or {}
    obj = data.get(object_type) or {}
    change = TaigaChange(
        source="timeline",
        project_id=_payload_project_id(data),
        object_type=object_type,
        action=action,
        object_id=_id(obj),
        occurred_at=parse_datetime(entry.get("created")),
        by_user_id=_id(data.get("user")),
        ref=obj.get("ref") if isinstance(obj, dict) else None,
        subject=obj.get("subject") if isinstance(obj, dict) else None,
        extra={"timeline_id": entry.get("id")},
    )
    if not change.relevant:
        return change

    values_diff = data.get("values_diff") or {}
    change.changed_fields = frozenset(k for k in values_diff if k not in NOISE_FIELDS)

    if object_type == "relateduserstory":
        change.epic_id = _id(data.get("epic"))
        change.user_story_id = _id(data.get("userstory"))
        return change

    if object_type == "task":
        change.user_story_id = _id(obj.get("userstory")) if isinstance(obj, dict) else None

    if object_type in ("task", "userstory"):
        changed, value = _custom_attributes_change(values_diff, hours_name)
        if changed:
            change.hours_changed = True
            change.hours, change.hours_error = _hours_value(value)
            change.hours_known = change.hours_error is None
        # 2026-11-21: timeline podaje wykonawcę nazwą - id trzeba pobrać z REST;
        # utworzenie obiektu też wymaga pobrania pełnego stanu
        if action == "create" or change.changed_fields & {"assigned_to", "assigned_users", "user_story"}:
            change.needs_fetch = True
    return change


def from_rest(kind: str, obj: dict, attribute_values: dict | None, *, hours_attribute_id: int | None,
              occurred_at: datetime | None, by_user_id: int | None = None) -> TaigaChange:
    """Pełny bieżący stan zadania / US pobrany przez REST -> TaigaChange (`source=rest`).

    Lista i szczegóły REST podają wykonawcę i US jako id, a wartości atrybutów
    (`/custom-attributes-values/{id}`) są kluczowane id atrybutu, nie nazwą.
    """
    if kind not in ("task", "userstory"):
        raise TaigaPayloadError(f"REST: nieobsługiwany rodzaj obiektu {kind!r}")
    project = obj.get("project")
    change = TaigaChange(
        source="rest", project_id=_id(project) if isinstance(project, dict) else project,
        object_type=kind, action="change", object_id=_id(obj), occurred_at=occurred_at,
        by_user_id=by_user_id, ref=obj.get("ref"), subject=obj.get("subject"),
    )
    values = (attribute_values or {}).get("attributes_values") or {}
    raw = values.get(str(hours_attribute_id)) if hours_attribute_id is not None else None
    change.hours, change.hours_error = _hours_value(raw)
    change.hours_known = change.hours_error is None
    change.assigned_to_id = _id(obj.get("assigned_to"))
    change.assigned_known = True
    if isinstance(obj.get("assigned_users"), list):
        change.assigned_users = tuple(int(u) for u in obj["assigned_users"])
    change.is_closed = obj.get("is_closed")
    if isinstance(obj.get("tags"), list):
        change.tags = tuple(str(t[0] if isinstance(t, list) else t) for t in obj["tags"])
    if kind == "task":
        change.user_story_id = _id(obj.get("user_story"))
    elif isinstance(obj.get("epics"), list):
        change.epic_ids = tuple(sorted(_id(e) for e in obj["epics"] if _id(e) is not None))
    elif "epics" in obj and obj["epics"] is None:
        change.epic_ids = ()
    return change
