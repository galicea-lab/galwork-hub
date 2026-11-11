# wf/services/form_permissions.py
# coding: utf-8
"""
Filtrowanie JSON Schema/UI Schema formularza wg macierzy `x-permissions`
(zob. spiffworkflow-core/*-schema.json, gdzie każde pole niesie
`x-permissions: {"<nazwa kroku>": "editable"|"readonly"|"none"}` i opcjonalnie
`x-required-in-steps: ["<nazwa kroku>", ...]`).

Backend ma tu być - "banalnym filtrem": dla danego
kroku (`step` = `HumanTask.name`, bo macierz jest kluczowana nazwą zadania
BPMN, nie jego technicznym `id`):

  * pole/grupa z `x-permissions[step] == "none"` - wycinane całkowicie
    (razem z dzieckami, jeśli to grupa/tablica),
  * `"readonly"` - pole zostaje, dostaje `"readOnly": true`,
  * `"editable"` - pole zostaje bez zmian,
  * pole bez `x-permissions` w ogóle (formularze  np. Spiff-Arena/Jinja) - zostaje bez zmian; `has_x_permissions()`
    pozwala wywołującemu w ogóle nie wchodzić w tę ścieżkę dla takich
    formularzy (zob. process_service.py::task_form).

Blok `x-permissions`/`x-required-in-steps` jest wycinany z wyniku - zgodnie
z rjsf.md ("Blok x-permissions zostaje wycięty ze schematu, aby nie wysyłać
uprawnień procesowych do przeglądarki").

`required` dla danego kroku to unia: pól globalnie wymaganych (oryginalny
`required` kontenera, przefiltrowany do pól, które przeszły filtr) oraz pól
z `x-required-in-steps` zawierającym `step`.

UI Schema jest filtrowany równolegle do JSON Schema (ta sama struktura
kluczy), żeby nie zostawić w nim `ui:order`/widgetów dla pól, które zniknęły
z formularza.
"""

from __future__ import annotations

from typing import Any, Mapping

NodeOrNone = "dict[str, Any] | None"


def has_x_permissions(node: Any) -> bool:
    """Czy schemat (rekurencyjnie) zawiera choć jedno pole z `x-permissions` -
    tylko takie formularze przechodzą przez filtrowanie/cache (zob. moduł)."""
    if isinstance(node, Mapping):
        if "x-permissions" in node:
            return True
        if isinstance(node.get("properties"), Mapping):
            if any(has_x_permissions(child) for child in node["properties"].values()):
                return True
        if isinstance(node.get("items"), Mapping):
            return has_x_permissions(node["items"])
    return False


def _filter_node(schema_node: Mapping[str, Any], ui_node: Mapping[str, Any] | None,
                  step: str) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Jeden węzeł (pole, grupa, element tablicy) - zwraca (schema, ui) albo
    (None, {}), gdy węzeł ma `x-permissions[step] == "none"`."""
    permissions = schema_node.get("x-permissions")
    if isinstance(permissions, Mapping) and permissions.get(step, "none") == "none":
        return None, {}

    filtered_schema = {k: v for k, v in schema_node.items()
                       if k not in ("x-permissions", "x-required-in-steps")}
    filtered_ui = {k: v for k, v in (ui_node or {}).items()
                   if k.startswith("ui:") and k != "ui:order"}

    if isinstance(permissions, Mapping) and permissions.get(step) == "readonly":
        filtered_schema["readOnly"] = True

    properties = schema_node.get("properties")
    if isinstance(properties, Mapping):
        kept_properties: dict[str, Any] = {}
        required = set(schema_node.get("required") or ())
        step_required: set[str] = set()
        for name, child in properties.items():
            if not isinstance(child, Mapping):
                kept_properties[name] = child
                continue
            child_ui = (ui_node or {}).get(name) if isinstance(ui_node, Mapping) else None
            filtered_child, filtered_child_ui = _filter_node(child, child_ui, step)
            if filtered_child is None:
                continue
            kept_properties[name] = filtered_child
            if filtered_child_ui:
                filtered_ui[name] = filtered_child_ui
            if step in (child.get("x-required-in-steps") or ()):
                step_required.add(name)
        filtered_schema["properties"] = kept_properties
        final_required = sorted((required | step_required) & kept_properties.keys())
        if final_required:
            filtered_schema["required"] = final_required
        else:
            filtered_schema.pop("required", None)

        original_order = (ui_node or {}).get("ui:order") if isinstance(ui_node, Mapping) else None
        if isinstance(original_order, list):
            new_order = [name for name in original_order if name in kept_properties]
            if new_order:
                filtered_ui["ui:order"] = new_order

    items = schema_node.get("items")
    if isinstance(items, Mapping):
        items_ui = (ui_node or {}).get("items") if isinstance(ui_node, Mapping) else None
        filtered_items, filtered_items_ui = _filter_node(items, items_ui, step)
        filtered_schema["items"] = filtered_items if filtered_items is not None else {
            "type": "object", "properties": {}}
        if filtered_items_ui:
            filtered_ui["items"] = filtered_items_ui

    return filtered_schema, filtered_ui


def filter_form_for_step(schema: Mapping[str, Any], ui_schema: Mapping[str, Any] | None,
                          step: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Filtruje JSON Schema + UI Schema formularza dla kroku `step`
    (`HumanTask.name`). Węzeł najwyższego poziomu nie ma własnego
    `x-permissions`, więc nigdy nie zostaje całkowicie wycięty."""
    filtered_schema, filtered_ui = _filter_node(schema, ui_schema, step)
    return filtered_schema or {"type": "object", "properties": {}}, filtered_ui
