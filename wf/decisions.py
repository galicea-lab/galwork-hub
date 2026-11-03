# wf/decisions.py
# coding: utf-8
"""
Decyzje gatewayów wyliczane z BPMN (bez kodu per definicja).

Dla każdego `exclusiveGateway` z warunkami `chosen_path == ...` budujemy listę
opcji z przepływów wychodzących: wartość = to, co ma trafić do `chosen_path`
(numeryczny ID z importu Webcona albo `Flow_*`), etykieta = nazwa przepływu
(z fallbackiem na nazwę zadania docelowego). Formularz zadania przed gatewayem
dostaje pole wyboru, a wybór jest normalizowany przed uruchomieniem Spiffa.

To jedno miejsce obsługuje wszystkie definicje i przyszłe zmiany w BPMN.
"""
from __future__ import annotations

import copy
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, replace
from typing import Any, Mapping

BPMN_NS = 'http://www.omg.org/spec/BPMN/20100524/MODEL'
DEFAULT_FIELD = 'chosen_path'
DEFAULT_VALUE = '__default__'

# `chosen_path == 421`, `chosen_path == 'Flow_5'`, `chosen_path == "Flow_5"`
_CHOSEN_RE = re.compile(r"chosen_path\s*==\s*(\d+|'[^']*'|\"[^\"]*\")")
_GUARD_RE = re.compile(r'\b([A-Z][A-Z0-9_]{2,})\b')

_NODE_TAGS = {
    'startEvent', 'endEvent', 'userTask', 'manualTask', 'serviceTask', 'scriptTask',
    'businessRuleTask', 'sendTask', 'receiveTask', 'task', 'callActivity', 'subProcess',
    'exclusiveGateway', 'parallelGateway', 'inclusiveGateway', 'eventBasedGateway',
    'complexGateway', 'intermediateCatchEvent', 'intermediateThrowEvent', 'boundaryEvent',
}


def _local(tag: str) -> str:
  return tag.split('}', 1)[1] if '}' in tag else tag


def decisions_enabled() -> bool:
  """Feature flag: WF_DECISIONS=off wyłącza wstrzykiwanie i prefill."""
  return os.getenv('WF_DECISIONS', 'auto').strip().lower() not in (
    'off', '0', 'false', 'no')


@dataclass(frozen=True)
class DecisionOption:
  """Jedna opcja wyboru (jeden przepływ wychodzący z gatewaya)."""
  value: str
  label: str
  flow_id: str
  target_id: str
  is_default: bool = False


@dataclass(frozen=True)
class Decision:
  """Decyzja przypisana do zadania przed gatewayem."""
  task_id: str
  gateway_id: str
  gateway_name: str
  field: str = DEFAULT_FIELD
  options: tuple[DecisionOption, ...] = ()
  blocked: bool = False
  block_reason: str | None = None

  def as_dict(self) -> dict[str, Any]:
    return {
      'task_id': self.task_id,
      'gateway_id': self.gateway_id,
      'gateway_name': self.gateway_name,
      'field': self.field,
      'blocked': self.blocked,
      'block_reason': self.block_reason,
      'options': [asdict(option) for option in self.options],
    }


def _chosen_value(condition: str) -> str | None:
  match = _CHOSEN_RE.search(condition or '')
  if not match:
    return None
  return match.group(1).strip("'\"")


def _guards(condition: str) -> tuple[str, ...]:
  """Zmienne poza chosen_path, które warunek dodatkowo wymaga."""
  names = {name for name in _GUARD_RE.findall(condition or '') if name != 'CHOSEN_PATH'}
  if re.search(r'\bFalse\b', condition or ''):
    names.add('False')
  return tuple(sorted(names))


def read_decisions(xml_text: str) -> dict[str, Decision]:
  """Mapa {id zadania: Decision} dla wszystkich gatewayów z warunkami.

  Pomija gatewaye bez warunków oraz bez dokładnie jednego wejścia z userTask/
  manualTask. Gatewaye, których warunki nie dotyczą `chosen_path` albo mają
  dodatkowe guardy, zwraca jako `blocked` - nie wstrzykujemy dla nich wyboru,
  bo sama opcja nie gwarantuje przejścia (guard mógłby ją zablokować).
  """
  try:
    root = ET.fromstring(xml_text)
  except ET.ParseError:
    return {}
  process = root.find(f'{{{BPMN_NS}}}process')
  if process is None:
    return {}

  nodes: dict[str, tuple[str, str]] = {}
  for element in process.iter():
    tag = _local(element.tag)
    if tag in _NODE_TAGS and element.get('id'):
      nodes[element.get('id')] = (tag, element.get('name') or element.get('id'))

  flows: list[dict[str, Any]] = []
  for element in process.iter():
    if _local(element.tag) != 'sequenceFlow':
      continue
    condition = element.find(f'{{{BPMN_NS}}}conditionExpression')
    flows.append({
      'id': element.get('id'),
      'source': element.get('sourceRef'),
      'target': element.get('targetRef'),
      'name': element.get('name'),
      'condition': (condition.text or '').strip() if condition is not None else None,
    })

  gateways: dict[str, dict[str, Any]] = {}
  for element in process.iter():
    if _local(element.tag) == 'exclusiveGateway' and element.get('id'):
      gateways[element.get('id')] = {
        'id': element.get('id'),
        'name': element.get('name') or element.get('id'),
        'default': element.get('default'),
      }

  decisions: dict[str, Decision] = {}
  for gateway_id, gateway in gateways.items():
    outgoing = [flow for flow in flows if flow['source'] == gateway_id]
    conditional = [flow for flow in outgoing if flow['condition']]
    if not conditional:
      continue
    incoming = [flow for flow in flows if flow['target'] == gateway_id]
    if len(incoming) != 1:
      continue
    task_id = incoming[0]['source']
    task_type = nodes.get(task_id, ('?', ''))[0]
    if task_type not in ('userTask', 'manualTask'):
      continue

    options: list[DecisionOption] = []
    blocked = False
    block_reason: str | None = None
    for flow in outgoing:
      condition = flow['condition']
      if condition:
        value = _chosen_value(condition)
        if value is None:
          blocked = True
          block_reason = f'warunek bez chosen_path: {condition}'
          continue
        guards = _guards(condition)
        if guards:
          # Bezpiecznik: wybór uruchomiłby eval() niezdefiniowanych zmiennych.
          blocked = True
          block_reason = f'guard: {", ".join(guards)}'
      else:
        value = DEFAULT_VALUE
      target = nodes.get(flow['target'], ('?', flow['target']))
      options.append(DecisionOption(
        value=value,
        label=flow['name'] or target[1] or flow['id'],
        flow_id=flow['id'],
        target_id=flow['target'],
        is_default=flow['id'] == gateway['default'],
      ))

    if not options:
      continue

    # Dwie opcje o tej samej etykiecie byłyby nierozróżnialne w radio.
    labels = [option.label for option in options]
    if len(labels) != len(set(labels)):
      options = [
        replace(option, label=f'{option.label} ({option.flow_id})')
        if labels.count(option.label) > 1 else option
        for option in options
      ]
    # Domyślna opcja pierwsza — łatwiej ją zaznaczyć i zobaczyć.
    options.sort(key=lambda option: (not option.is_default,))

    decisions[task_id] = Decision(
      task_id=task_id,
      gateway_id=gateway_id,
      gateway_name=gateway['name'],
      options=tuple(options),
      blocked=blocked,
      block_reason=block_reason,
    )
  return decisions


def default_value(decision: Mapping[str, Any]) -> str | None:
  """Wartość opcji domyślnej (do prefilla form_data)."""
  for option in decision.get('options') or ():
    if option.get('is_default'):
      return str(option.get('value'))
  return None


def inject_decision(schema: Mapping[str, Any] | None,
                    ui_schema: Mapping[str, Any] | None,
                    decision: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
  """Dodaje pole wyboru do JSON Schema i uiSchema (RJSF).

  Nie nadpisuje istniejącego pola `chosen_path` — formularz biznesowy wygrywa.
  """
  if not isinstance(decision, Mapping) or decision.get('blocked'):
    return dict(schema or {}), dict(ui_schema or {})
  field = str(decision.get('field') or DEFAULT_FIELD)
  schema_dict = copy.deepcopy(dict(schema or {}))
  properties = schema_dict.get('properties') or {}
  if field in properties:
    return schema_dict, copy.deepcopy(dict(ui_schema or {}))

  options = list(decision.get('options') or ())
  one_of: list[dict[str, Any]] = []
  selected: str | None = None
  for option in options:
    value = str(option.get('value'))
    label = str(option.get('label') or value)
    if option.get('is_default'):
      label = f'{label} (domyślna)'
      selected = value
    one_of.append({'const': value, 'title': label})

  schema_dict.setdefault('type', 'object')
  schema_dict.setdefault('properties', {})[field] = {
    'type': 'string',
    'title': str(decision.get('gateway_name') or 'Wybór ścieżki'),
    'oneOf': one_of,
  }
  if selected is not None:
    schema_dict['properties'][field]['default'] = selected

  ui_dict = copy.deepcopy(dict(ui_schema or {}))
  ui_dict[field] = {'ui:widget': 'radio'}
  order = list(ui_dict.get('ui:order') or ())
  ui_dict['ui:order'] = [field] + [item for item in order if item != field]
  return schema_dict, ui_dict


def normalize_choice(value: Any) -> Any:
  """`__default__` -> None, `'421'` -> 421, `'Flow_5'` -> 'Flow_5'."""
  if isinstance(value, str):
    stripped = value.strip()
    if stripped in ('', DEFAULT_VALUE):
      return None
    if re.fullmatch(r'-?\d+', stripped):
      return int(stripped)
    return stripped
  return value
