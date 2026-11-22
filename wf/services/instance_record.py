# wf/services/instance_record.py
# coding: utf-8
"""
2026-11-22 (H4, custom_dashboard2.md §3.2, §4.1): karta obiektu - dane sprawy
w formularzu tylko do odczytu, bez kodu domenowego.

`GET /process-instances/{id}/record` składa:

  * krok karty - `galwork.record_step` z process_model.json (nazwa zadania albo
    zdarzenia startowego); domyślnie zdarzenie startowe,
  * formularz tego kroku przefiltrowany przez `x-permissions` dla tej nazwy
    (pole `none` nie trafia na kartę) i wymuszony jako tylko do odczytu
    (`readOnly` w korzeniu schematu, `ui:readonly`, bez przycisku),
  * dane BIEŻĄCE (`instance_metadata.current_data`), PRZYCIĘTE do pól, które
    zostały w schemacie - pola ukryte w kroku nie wychodzą do przeglądarki,
  * `edit_task_id` - otwarte zadanie `galwork.edit_step`, które użytkownik może
    wykonać (podjęte przez niego albo nie podjęte, a on jest kandydatem lub
    kandydatów brak - jak `GET /tasks/my`),
  * wydruki (`x-print`) - tylko dla uczestników, jak `GET .../prints`.

Formularz z szablonem Jinja jest renderowany bieżącymi danymi przed filtrem.
Model bez formularza w kroku karty: `has_form = false` (klient pokazuje przebieg).
"""
from __future__ import annotations

import copy
import xml.etree.ElementTree as ET
from typing import Any, Iterable, Mapping

from wf.engine import BPMN_NS
from wf.models import HumanTask, ProcessInstance
from wf.schemas.wf_schemas import CurrentUser, HumanTaskStatus
from wf.services import instance_access, instance_metadata
from wf.services.form_permissions import filter_form_for_step, has_x_permissions
from wf.services.process_service import (
    ProcessService,
    _bpmn_files,
    _form_entry,
    _try_parse_json,
    render_form_json,
)

_ELEMENTS = ('startEvent', 'userTask', 'manualTask', 'task')


def element_ids_by_name(xml_texts: Iterable[str], name: str) -> list[str]:
    """Id elementów (startEvent / zadania) o podanej nazwie."""
    found = []
    for xml_text in xml_texts:
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            continue
        for tag in _ELEMENTS:
            for element in root.iter(f'{{{BPMN_NS}}}{tag}'):
                if (element.get('name') or '').strip() == name and element.get('id'):
                    found.append(element.get('id'))
    return found


def _start_step(service: ProcessService, version) -> tuple[str | None, str | None]:
    """(nazwa kroku, id elementu) zdarzenia startowego z formularzem."""
    from wf.engine import start_event_name
    start = (service.form_spec(version).get('start_form') or {})
    task_id = start.get('task_id') or start.get('bpmn_task_id')
    if not task_id:
        return None, None
    return start_event_name(_bpmn_files(version).values(), task_id) or task_id, task_id


def _resolve(schema: Mapping[str, Any], node: Any) -> Any:
    if isinstance(node, Mapping) and isinstance(node.get('$ref'), str):
        defs = schema.get('$defs') or schema.get('definitions') or {}
        return defs.get(node['$ref'].rsplit('/', 1)[-1], {})
    return node


def prune(root: Mapping[str, Any], node: Any, data: Any) -> Any:
    """Dane ograniczone do pól schematu (obiekty z `properties`, tablice obiektów)."""
    node = _resolve(root, node)
    if not isinstance(node, Mapping):
        return data
    props = node.get('properties')
    if isinstance(props, Mapping) and isinstance(data, Mapping):
        return {key: prune(root, props[key], value) for key, value in data.items() if key in props}
    items = node.get('items')
    if isinstance(items, Mapping) and isinstance(data, list):
        return [prune(root, items, item) for item in data]
    return data


def _readonly(schema: dict[str, Any], ui_schema: dict[str, Any]) -> tuple[dict, dict]:
    schema = copy.deepcopy(schema)
    schema['readOnly'] = True
    schema.pop('required', None)
    ui = copy.deepcopy(ui_schema or {})
    ui['ui:readonly'] = True
    ui['ui:submitButtonOptions'] = {'norender': True}
    return schema, ui


def record_form(service: ProcessService, instance: ProcessInstance, data: Mapping[str, Any]
                ) -> tuple[str | None, dict[str, Any], dict[str, Any]]:
    """(krok karty, schemat tylko do odczytu, UI schema) - puste, gdy brak formularza."""
    version = instance.version
    spec = service.form_spec(version)
    galwork = (version.spec_json or {}).get('galwork') or {}
    start_name, start_id = _start_step(service, version)
    step = galwork.get('record_step') or start_name
    if not step:
        return None, {}, {}
    element_ids = [start_id] if step == start_name and start_id else []
    element_ids += [e for e in element_ids_by_name(_bpmn_files(version).values(), step) if e not in element_ids]
    for element_id in element_ids:
        schema_entry = _form_entry(spec['forms'], spec['form_files'], element_id, 'schema')
        if schema_entry is None:
            continue
        ui_entry = _form_entry(spec['uischemas'], spec['form_files'], element_id, 'ui')
        schema_obj = _try_parse_json(schema_entry)
        ui_obj = _try_parse_json(ui_entry) if ui_entry is not None else {}
        if schema_obj is None:  # szablon Jinja - najpierw renderowanie danymi
            schema_obj = render_form_json(schema_entry, data)
            ui_obj = render_form_json(ui_entry, data) if ui_entry is not None else {}
        if has_x_permissions(schema_obj):
            schema_obj, ui_obj = filter_form_for_step(schema_obj, ui_obj or {}, step)
        schema = render_form_json(schema_obj, data)
        ui_schema = render_form_json(ui_obj or {}, data)
        return (step, *_readonly(schema, ui_schema))
    return step, {}, {}


def edit_task(instance: ProcessInstance, user: CurrentUser) -> HumanTask | None:
    """Otwarte zadanie `galwork.edit_step`, które użytkownik może wykonać."""
    galwork = ((instance.version.spec_json if instance.version else None) or {}).get('galwork') or {}
    step = galwork.get('edit_step')
    if not step:
        return None
    acting = user.acting_user_ids
    names = set(instance_access.principals(user))
    for task in sorted(instance.tasks or [], key=lambda t: t.created_at, reverse=True):
        if task.status != HumanTaskStatus.OPEN or task.name != step:
            continue
        if task.claimed_by_id is not None:
            if task.claimed_by_id in acting:
                return task
            continue
        candidates = task.candidates or []
        if not candidates or any(c.principal_value in names for c in candidates):
            return task
    return None


def record(service: ProcessService, instance: ProcessInstance, user: CurrentUser) -> dict[str, Any]:
    """Treść odpowiedzi `GET /process-instances/{id}/record`."""
    from wf.services import instance_print
    workflow, _spec = service.load_workflow(instance)
    data = instance_metadata.current_data(service.engine, workflow)
    step, schema, ui_schema = record_form(service, instance, data)
    task = edit_task(instance, user)
    prints = []
    if instance_access.is_participant(instance, user):
        prints = [vars(option) for option in instance_print.available_prints(instance, None)]
    return {
        'instance_id': instance.id,
        'process_model_key': instance.version.process_model.key if instance.version else None,
        'title': instance.title,
        'status': instance.status,
        'record_step': step,
        'has_form': bool(schema.get('properties')),
        'json_schema': schema,
        'ui_schema': ui_schema,
        'form_data': prune(schema, schema, data) if schema else {},
        'metadata': dict(instance.extracted_metadata or {}),
        'edit_task_id': task.id if task is not None else None,
        'edit_task_name': task.name if task is not None else None,
        'prints': prints,
    }
