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

2026-11-13: profile uprawnień - zamiast macierzy przy każdym polu
`"x-permissions": "<nazwa profilu>"`, a profile raz w korzeniu schematu:
`"x-permission-profiles": {"<nazwa profilu>": {"<nazwa kroku>": "..."}}`.
Nieznany profil = brak uprawnień ("none") w każdym kroku. Słownik profili
też jest wycinany z wyniku.

2026-11-13: wspólne sekcje w `$defs` - pole `{"$ref": "#/$defs/<nazwa>"}`
jest filtrowane jak jego definicja (ta sama macierz i UI pola). Wynik
zachowuje `$ref`, a w `$defs` zostają tylko definicje widoczne w kroku;
definicja w całości `none` usuwa też pole, które ją wskazuje.

`required` dla danego kroku to unia: pól globalnie wymaganych (oryginalny
`required` kontenera, przefiltrowany do pól, które przeszły filtr) oraz pól
z `x-required-in-steps` zawierającym `step`.

UI Schema jest filtrowany równolegle do JSON Schema (ta sama struktura
kluczy), żeby nie zostawić w nim `ui:order`/widgetów dla pól, które zniknęły
z formularza.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

NodeOrNone = "dict[str, Any] | None"

# BEGIN 2026-11-13
PROFILES_KEY = "x-permission-profiles"
_STRIPPED_KEYS = ("x-permissions", "x-required-in-steps", PROFILES_KEY)


DEFS_KEY = "$defs"
_DEFS_PREFIX = "#/$defs/"


@dataclass
class _Context:
    """Słowniki z korzenia schematu wspólne dla całego filtrowania."""
    profiles: Mapping[str, Any]
    defs: Mapping[str, Any]
    filtered_defs: dict[str, Any] = field(default_factory=dict)


def _def_name(node: Mapping[str, Any], ctx: _Context) -> str | None:
    """Nazwa definicji z `$defs`, gdy węzeł to lokalne `$ref` do niej."""
    ref = node.get("$ref")
    if isinstance(ref, str) and ref.startswith(_DEFS_PREFIX):
        name = ref[len(_DEFS_PREFIX):]
        if isinstance(ctx.defs.get(name), Mapping):
            return name
    return None


def _required_steps(node: Mapping[str, Any], ctx: _Context) -> Any:
    """`x-required-in-steps` węzła albo definicji, którą wskazuje."""
    name = _def_name(node, ctx)
    if "x-required-in-steps" not in node and name is not None:
        return ctx.defs[name].get("x-required-in-steps") or ()
    return node.get("x-required-in-steps") or ()


def _resolve_permissions(permissions: Any, profiles: Mapping[str, Any]) -> Any:
    """Macierz pola: obiekt wprost albo nazwa profilu z `x-permission-profiles`."""
    if isinstance(permissions, str):
        profile = profiles.get(permissions)
        return profile if isinstance(profile, Mapping) else {}
    return permissions
# -END


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
        # 2026-11-13: macierz może być tylko w definicjach `$defs`.
        if isinstance(node.get(DEFS_KEY), Mapping):
            if any(has_x_permissions(child) for child in node[DEFS_KEY].values()):
                return True
        # 2026-11-12: sekcje warunkowe (allOf/if/then/else) też niosą macierz.
        return any(has_x_permissions(branch) for branch in _conditional_branches(node))
    return False


# BEGIN 2026-11-12
# Sekcje warunkowe: jeden formularz dla wielu wariantów dokumentu
# (np. rodzaj pisma ze słownika) zamiast osobnego pliku na wariant.
# Pola zależne od wariantu są w gałęziach `then`/`else` - na samym węźle
# albo w elementach `allOf`. Gałąź `if` (warunek) nie jest filtrowana.
_CONDITIONAL_KEYS = ("then", "else")


def _conditional_branches(node: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Gałęzie `then`/`else` węzła i elementów jego `allOf`."""
    holders = [node] + [sub for sub in (node.get("allOf") or ()) if isinstance(sub, Mapping)]
    return [holder[key] for holder in holders for key in _CONDITIONAL_KEYS
            if isinstance(holder.get(key), Mapping)]


def _filter_conditionals(schema_node: Mapping[str, Any], ui_node: Mapping[str, Any] | None,
                         step: str, filtered_schema: dict[str, Any],
                         filtered_ui: dict[str, Any],
                         ctx: _Context) -> set[str]:
    """Filtruje gałęzie `then`/`else` (na węźle i w `allOf`) w miejscu.

    UI Schema pól z gałęzi trafia do UI Schema węzła (RJSF nie ma
    warunkowego uiSchema - pola są kluczowane nazwą). Zwraca nazwy pól
    zachowanych w gałęziach - potrzebne do `ui:order` węzła."""
    kept_names: set[str] = set()
    branch_uis: dict[str, list[Mapping[str, Any]]] = {}

    def filter_holder(holder: Mapping[str, Any]) -> dict[str, Any]:
        result = dict(holder)
        for key in _CONDITIONAL_KEYS:
            branch = holder.get(key)
            if not isinstance(branch, Mapping):
                continue
            filtered_branch, branch_ui = _filter_node(branch, ui_node, step, ctx)
            if filtered_branch is None:
                result.pop(key, None)
                continue
            result[key] = filtered_branch
            kept_names.update((filtered_branch.get("properties") or {}).keys())
            for name, child_ui in branch_ui.items():
                if not name.startswith("ui:"):
                    branch_uis.setdefault(name, []).append(child_ui)
        return result

    if any(isinstance(schema_node.get(key), Mapping) for key in _CONDITIONAL_KEYS):
        filtered_node = filter_holder(schema_node)
        for key in _CONDITIONAL_KEYS:
            if key in filtered_node:
                filtered_schema[key] = filtered_node[key]
            else:
                filtered_schema.pop(key, None)
    all_of = schema_node.get("allOf")
    if isinstance(all_of, list):
        filtered_schema["allOf"] = [filter_holder(sub) if isinstance(sub, Mapping) else sub
                                    for sub in all_of]
    for name, uis in branch_uis.items():
        filtered_ui[name] = _merge_ui(uis)
    return kept_names


def _merge_ui(uis: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Łączy UI Schema tej samej sekcji z kilku gałęzi (np. sekcja "PISMO"
    w każdym wariancie). Sprzeczne `ui:order` jest pomijane - RJSF ustawia
    wtedy pola w kolejności schematu aktywnej gałęzi; przy innych
    sprzecznych wartościach wygrywa pierwsza gałąź."""
    merged: dict[str, Any] = {}
    keys = list(dict.fromkeys(key for ui in uis for key in ui))
    for key in keys:
        values = [ui[key] for ui in uis if key in ui]
        if all(isinstance(value, Mapping) for value in values):
            merged[key] = _merge_ui(values)
        elif key == "ui:order":
            if all(value == values[0] for value in values):
                merged[key] = values[0]
        else:
            merged[key] = values[0]
    return merged
# -END


def _filter_node(schema_node: Mapping[str, Any], ui_node: Mapping[str, Any] | None,
                  step: str, ctx: _Context
                  ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Jeden węzeł (pole, grupa, element tablicy) - zwraca (schema, ui) albo
    (None, {}), gdy węzeł ma `x-permissions[step] == "none"`."""
    # 2026-11-13: `x-permissions` może być nazwą profilu.
    permissions = _resolve_permissions(schema_node.get("x-permissions"), ctx.profiles)
    if isinstance(permissions, Mapping) and permissions.get(step, "none") == "none":
        return None, {}

    filtered_schema = {k: v for k, v in schema_node.items()
                       if k not in _STRIPPED_KEYS}
    filtered_ui = {k: v for k, v in (ui_node or {}).items()
                   if k.startswith("ui:") and k != "ui:order"}

    if isinstance(permissions, Mapping) and permissions.get(step) == "readonly":
        filtered_schema["readOnly"] = True

    # 2026-11-13: `$ref` do `$defs` - filtrujemy definicję, pole zostaje
    # odwołaniem. Ta sama definicja daje w kroku zawsze ten sam wynik.
    def_name = _def_name(schema_node, ctx)
    if def_name is not None:
        filtered_def, def_ui = _filter_node(ctx.defs[def_name], ui_node, step, ctx)
        if filtered_def is None:
            return None, {}
        ctx.filtered_defs[def_name] = filtered_def
        filtered_ui.update(def_ui)
        return filtered_schema, filtered_ui

    # 2026-11-12: najpierw gałęzie warunkowe - ich pola liczą się do `ui:order`.
    conditional_names = _filter_conditionals(schema_node, ui_node, step,
                                             filtered_schema, filtered_ui, ctx)

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
            filtered_child, filtered_child_ui = _filter_node(child, child_ui, step, ctx)
            if filtered_child is None:
                continue
            kept_properties[name] = filtered_child
            if filtered_child_ui:
                filtered_ui[name] = filtered_child_ui
            if step in _required_steps(child, ctx):
                step_required.add(name)
        filtered_schema["properties"] = kept_properties
        final_required = sorted((required | step_required) & kept_properties.keys())
        if final_required:
            filtered_schema["required"] = final_required
        else:
            filtered_schema.pop("required", None)

        original_order = (ui_node or {}).get("ui:order") if isinstance(ui_node, Mapping) else None
        if isinstance(original_order, list):
            # 2026-11-12: "*" (reszta pól w kolejności schematu) i pola
            # z gałęzi warunkowych zostają - bez "*" RJSF zgłasza błąd, gdy
            # pola wariantu nie ma na liście.
            new_order = [name for name in original_order
                         if name == "*" or name in kept_properties or name in conditional_names]
            if new_order:
                filtered_ui["ui:order"] = new_order

    items = schema_node.get("items")
    if isinstance(items, Mapping):
        items_ui = (ui_node or {}).get("items") if isinstance(ui_node, Mapping) else None
        filtered_items, filtered_items_ui = _filter_node(items, items_ui, step, ctx)
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
    profiles = schema.get(PROFILES_KEY)
    defs = schema.get(DEFS_KEY)
    ctx = _Context(profiles=profiles if isinstance(profiles, Mapping) else {},
                   defs=defs if isinstance(defs, Mapping) else {})
    filtered_schema, filtered_ui = _filter_node(schema, ui_schema, step, ctx)
    # 2026-11-13: tylko definicje widoczne w kroku (wskazane przez pola,
    # które przeszły filtr); `$defs` bez macierzy przechodzą bez zmian.
    if filtered_schema is not None and DEFS_KEY in filtered_schema:
        kept_defs = {name: ctx.filtered_defs.get(name, value)
                     for name, value in ctx.defs.items()
                     if name in ctx.filtered_defs or not has_x_permissions(value)}
        if kept_defs:
            filtered_schema[DEFS_KEY] = kept_defs
        else:
            filtered_schema.pop(DEFS_KEY)
    return filtered_schema or {"type": "object", "properties": {}}, filtered_ui
