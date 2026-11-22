# wf/services/instance_metadata.py
# coding: utf-8
"""
2026-11-22: metadane instancji - kolumny list rejestrów (H1, custom_dashboard2.md §3.1).

`process_model.json` modelu niesie pole Spiff-Arena `metadata_extraction_paths`:

    "metadata_extraction_paths": [{"key": "nr", "path": "Nr dokumentu"},
                                  {"key": "tytul", "path": "Szczegóły dokumentu.Tytuł"}]

Lista trafia do `spec_json['metadata_paths']` wersji (niezmienna po publikacji,
jak `galwork`). Przy każdym zapisie stanu silnika (`ProcessService._save_state`)
hub wylicza `ProcessInstance.extracted_metadata = {key: wartość}` z BIEŻĄCYCH
danych sprawy: `workflow.data` nadpisane danymi wykonanych zadań w kolejności
wykonania (ostatnia zmiana wygrywa). `summary` pozostaje danymi startowymi.

Pola listy (`GET /process-models/{key}/metadata-fields`) dostają tytuł, typ,
format i enum z pierwszego formularza modelu, który zawiera ścieżkę
(najpierw formularz startowy), żeby klient nie powtarzał etykiet.
"""
from __future__ import annotations

import json
import re
from typing import Any, Iterable, Mapping

KEY_RE = re.compile(r'^[a-z0-9_][a-z0-9_-]{0,62}$')


class MetadataPathsError(ValueError):
    """Błędne `metadata_extraction_paths` w process_model.json."""


def parse_paths(value: Any) -> list[dict[str, str]]:
    """Walidacja `metadata_extraction_paths` (None/[] = brak kolumn)."""
    if value in (None, []):
        return []
    if not isinstance(value, list):
        raise MetadataPathsError('metadata_extraction_paths musi być listą')
    result, seen = [], set()
    for item in value:
        if not isinstance(item, Mapping) or not isinstance(item.get('key'), str) \
                or not isinstance(item.get('path'), str) or not item['path'].strip():
            raise MetadataPathsError(
                f'metadata_extraction_paths: element {item!r} musi mieć "key" i "path" (tekst)')
        key = item['key']
        if not KEY_RE.match(key):
            raise MetadataPathsError(
                f'metadata_extraction_paths: klucz {key!r} - dozwolone małe litery, cyfry, "_" i "-"')
        if key in seen:
            raise MetadataPathsError(f'metadata_extraction_paths: powtórzony klucz {key!r}')
        seen.add(key)
        result.append({'key': key, 'path': item['path'].strip()})
    return result


def _get_path(data: Mapping[str, Any], path: str) -> Any:
    node: Any = data
    for part in path.split('.'):
        if not isinstance(node, Mapping) or part not in node:
            return None
        node = node[part]
    return node


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return str(value)


def extract(paths: Iterable[Mapping[str, str]], data: Mapping[str, Any]) -> dict[str, Any]:
    """{key: wartość} dla ścieżek obecnych w danych (brak/None - pomijane)."""
    result = {}
    for item in paths or ():
        value = _get_path(data, item['path'])
        if value is not None and value != '':
            result[item['key']] = _json_safe(value)
    return result


def current_data(engine, workflow) -> dict[str, Any]:
    """Bieżące dane sprawy: `workflow.data` + dane wykonanych zadań wg kolejności."""
    data: dict[str, Any] = dict(getattr(workflow, 'data', None) or {})
    tasks = sorted(engine.completed_tasks(workflow),
                   key=lambda t: getattr(t, 'last_state_change', 0) or 0)
    for task in tasks:
        data.update(dict(task.data or {}))
    return data


def paths_of(spec: Mapping[str, Any] | None) -> list[dict[str, str]]:
    return list((spec or {}).get('metadata_paths') or [])


# --- opis pól dla klienta -----------------------------------------------------

def _schema_node(schema: Mapping[str, Any], path: str) -> Mapping[str, Any] | None:
    defs = schema.get('$defs') or schema.get('definitions') or {}
    node: Any = schema
    for part in path.split('.'):
        if isinstance(node, Mapping) and isinstance(node.get('$ref'), str):
            node = defs.get(node['$ref'].rsplit('/', 1)[-1], {})
        props = node.get('properties') if isinstance(node, Mapping) else None
        if not isinstance(props, Mapping) or part not in props:
            return None
        node = props[part]
    if isinstance(node, Mapping) and isinstance(node.get('$ref'), str):
        node = defs.get(node['$ref'].rsplit('/', 1)[-1], node)
    return node if isinstance(node, Mapping) else None


def field_info(spec: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Kolumny listy z opisem z formularzy modelu (tytuł, typ, format, enum)."""
    spec = spec or {}
    forms = dict(spec.get('forms') or {})
    start_key = None
    start = spec.get('start_form') or {}
    files = (spec.get('form_files') or {}).get(start.get('task_id') or '', {})
    if files.get('schema'):
        start_key = files['schema'].rsplit('.json', 1)[0]
    ordered = ([forms.pop(start_key)] if start_key in forms else []) + list(forms.values())
    schemas = []
    for text in ordered:
        try:
            parsed = json.loads(text) if isinstance(text, str) else text
        except ValueError:
            continue  # szablon Jinja - nie da się czytać bez danych
        if isinstance(parsed, Mapping):
            schemas.append(parsed)

    result = []
    for item in paths_of(spec):
        info: dict[str, Any] = {'key': item['key'], 'path': item['path'],
                                'title': item['path'].rsplit('.', 1)[-1]}
        for schema in schemas:
            node = _schema_node(schema, item['path'])
            if node is not None:
                info['title'] = node.get('title') or info['title']
                for name in ('type', 'format', 'enum'):
                    if name in node:
                        info[name] = node[name]
                break
        result.append(info)
    return result
