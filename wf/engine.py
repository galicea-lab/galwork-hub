# wf/engine.py - jedyne miejsce w hubie, ktore importuje SpiffWorkflow
#
# Co zmieniono w stosunku do wersji z 2026-10-04 (szkieletu):
#   * `BpmnWorkflowSerializer.configure(SPIFF_CONFIG)` byl bledny - SPIFF_CONFIG
#     jest slownikiem konwerterow dla specyfikacji SPIFF, a nie rejestrem BPMN.
#     Efekt: `serialize_json` rzucalo "Object of type UserTask is not JSON
#     serializable" (potwierdzone testem na SpiffWorkflow 3.2.0). Teraz
#     uzywamy domyslnego rejestru BPMN.
#   * dodany rejestr specyfikacji budowany z plikow ProcessModelFile
#     (`ProcessSpec`) - parser, proces glowny, procesy pomocnicze oraz
#     odczytane z BPMN przypisania (assignee/candidateGroups).
#   * stan instancji zapisywany jest jako JSON (kolumna
#     ProcessInstanceState.state), a `serializer_version` opisuje format.
#
# Skrypty BPMN (scriptTask) nie sa obslugiwane: SpiffWorkflow 3.x z rdzenia nie
# dostarcza silnika skryptow (brak modulu SpiffWorkflow.spiff.script), wiec
# `script_engine` pozostaje wylaczony. Zadania `userTask`/`manualTask` dzialaja.
from __future__ import annotations

import json
import re
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from SpiffWorkflow.bpmn.parser.BpmnParser import BpmnParser
from SpiffWorkflow.bpmn.parser.ValidationException import ValidationException
from SpiffWorkflow.bpmn.serializer import BpmnWorkflowSerializer
from SpiffWorkflow.bpmn.workflow import BpmnWorkflow
from SpiffWorkflow.util.task import TaskState
from lxml.etree import XMLSyntaxError

from wf.decisions import normalize_choice

# wersja formatu zapisywanego w ProcessInstanceState
# .serializer_version - przy zmianie rejestru serializera stan zapisany
# poprzednio trzeba przeliczyć (albo wygenerowac od nowa).
SERIALIZER_VERSION = 'spiff-bpmn-3.2-default-registry'

BPMN_NS = 'http://www.omg.org/spec/BPMN/20100524/MODEL'
FLOWABLE_NS = 'http://flowable.org/bpmn'
CAMUNDA_NS = 'http://camunda.org/schema/1.0/bpmn'
ASSIGNEE_TAGS = (f'{{{FLOWABLE_NS}}}assignee', f'{{{CAMUNDA_NS}}}assignee')
CANDIDATE_USERS_TAGS = (f'{{{FLOWABLE_NS}}}candidateUsers', f'{{{CAMUNDA_NS}}}candidateUsers')
CANDIDATE_GROUPS_TAGS = (f'{{{FLOWABLE_NS}}}candidateGroups', f'{{{CAMUNDA_NS}}}candidateGroups')
FORM_TAGS = (f'{{{FLOWABLE_NS}}}formKey', f'{{{CAMUNDA_NS}}}formKey')

# 2026-11-06: przypisania mogą wskazywać zmienną procesu, np.
# `flowable:assignee="${student_username}"` albo `candidateGroups="${grupy}"`
# (lista). Wartość rozwiązuje ProcessService.sync_tasks z danych instancji i
# zapisuje jako kandydatów zadania (HumanTaskCandidate). Zmienna bez wartości
# NIE oznacza "zadanie dla każdego": dostaje kandydata UNRESOLVED_PRINCIPAL,
# którego nikt nie ma.
_VARIABLE_RE = re.compile(r'^\$\{\s*([A-Za-z_][A-Za-z0-9_.]*)\s*\}$')
UNRESOLVED_PRINCIPAL = '__unresolved__'


def _lookup(data: Mapping[str, Any], path: str) -> Any:
  value: Any = data
  for part in path.split('.'):
    if not isinstance(value, Mapping):
      return None
    value = value.get(part)
  return value


def _resolve_values(values: Iterable[str], data: Mapping[str, Any]) -> tuple[str, ...]:
  resolved: list[str] = []
  for raw in values:
    match = _VARIABLE_RE.match(raw)
    if not match:
      resolved.append(raw)
      continue
    value = _lookup(data, match.group(1))
    items = value if isinstance(value, (list, tuple, set, frozenset)) else [value]
    resolved.extend(str(item).strip() for item in items
                    if item is not None and str(item).strip())
  return tuple(dict.fromkeys(resolved))


class SpecError(ValueError):
  """Specyfikacja BPMN jest niepoprawna (bledny XML, brak procesu itd.)."""


@dataclass(frozen=True)
class TaskAssignment:
  """Przypisanie zadania odczytane z BPMN (atrybuty rozszerzen)."""
  assignee: str | None = None
  candidate_users: tuple[str, ...] = ()
  candidate_groups: tuple[str, ...] = ()
  form_key: str | None = None

  def allows(self, username: str, groups: Iterable[str]) -> bool:
    """Czy uzytkownik moze wykonac zadanie.

    Brak jakichkolwiek atrybutow przypisania = zadanie dostepne dla kazdego
    uwierzytelnionego uzytkownika (czesc modeli procesow tak projektowano).
    W realnym wdrozeniu to nalezy ograniczyc rolami z Keycloak.
    """
    if self.assignee:
      return username == self.assignee
    if not self.candidate_users and not self.candidate_groups:
      return True
    return username in self.candidate_users or bool(set(groups) & set(self.candidate_groups))

  @property
  def is_dynamic(self) -> bool:
    """Czy któreś przypisanie wskazuje zmienną procesu (`${...}`)."""
    values = [self.assignee or '', *self.candidate_users, *self.candidate_groups]
    return any(_VARIABLE_RE.match(value) for value in values)

  def resolve(self, data: Mapping[str, Any] | None) -> 'TaskAssignment':
    """Przypisanie z wartościami zmiennych `${...}` wziętymi z `data`."""
    if not self.is_dynamic:
      return self
    data = data or {}
    assignee = _resolve_values([self.assignee], data) if self.assignee else ()
    users = _resolve_values(self.candidate_users, data)
    groups = _resolve_values(self.candidate_groups, data)
    if not (assignee or users or groups):
      groups = (UNRESOLVED_PRINCIPAL,)
    if len(assignee) > 1:
      # kilka wartości w `assignee` (lista) traktujemy jak kandydatów
      users, assignee = tuple(dict.fromkeys((*assignee, *users))), ()
    return TaskAssignment(
      assignee=assignee[0] if assignee else None,
      candidate_users=users,
      candidate_groups=groups,
      form_key=self.form_key,
    )


@dataclass(frozen=True)
class ProcessSpec:
  """Specyfikacja procesu gotowa do uruchomienia."""
  process_id: str
  spec: Any
  subprocess_specs: dict[str, Any]
  assignments: Mapping[str, TaskAssignment] = field(default_factory=dict)


def _split_list(value: str | None) -> tuple[str, ...]:
  if not value:
    return ()
  return tuple(item.strip() for item in value.split(',') if item.strip())


def _read_assignments(xml_text: str) -> dict[str, TaskAssignment]:
  """Odczytuje `assignee`/`candidateUsers`/`candidateGroups` z BPMN.

  Rdzen SpiffWorkflow nie umie przypisan (to rozszerzenie Spiff-Areny), wiec
  autoryzacje robimy sami na podstawie atrybutow rozszerzen flowable/camunda.
  """
  try:
    root = ET.fromstring(xml_text)
  except ET.ParseError as exc:
    raise SpecError(f'Nieprawidlowy XML BPMN: {exc}') from exc

  assignments: dict[str, TaskAssignment] = {}
  for element in root.iter():
    if element.tag not in (f'{{{BPMN_NS}}}userTask', f'{{{BPMN_NS}}}manualTask'):
      continue
    task_id = element.get('id')
    if not task_id:
      continue
    assignments[task_id] = TaskAssignment(
      assignee=_first_attr(element, ASSIGNEE_TAGS),
      candidate_users=_split_list(_first_attr(element, CANDIDATE_USERS_TAGS)),
      candidate_groups=_split_list(_first_attr(element, CANDIDATE_GROUPS_TAGS)),
      form_key=_first_attr(element, FORM_TAGS),
    )
  return assignments


def _first_attr(element: ET.Element, tags: Iterable[str]) -> str | None:
  for tag in tags:
    value = element.get(tag)
    if value:
      return value
  return None


# Spiff-Arena trzyma nazwy plikow formularza w rozszerzeniach
# elementow BPMN (przestrzen nazw spiffworkflow moze sie zmieniac, wiec
# dopasowujemy po localname - tak jak przy czytaniu assignee/candidateGroups).
FORM_SCHEMA_PROP = 'formJsonSchemaFilename'
FORM_UI_SCHEMA_PROP = 'formUiSchemaFilename'
FORM_ELEMENTS = (f'{{{BPMN_NS}}}userTask', f'{{{BPMN_NS}}}manualTask',
                 f'{{{BPMN_NS}}}startEvent')


@dataclass(frozen=True)
class ElementFormFiles:
  """Nazwy plikow formularza wskazane w rozszerzeniu elementu BPMN."""
  schema_file: str | None = None
  ui_schema_file: str | None = None

  @property
  def present(self) -> bool:
    return bool(self.schema_file or self.ui_schema_file)


def read_form_files(xml_text: str) -> tuple[dict[str, ElementFormFiles], tuple[str, ...]]:
  """Odczytuje przypisanie plikow formularzy do elementow BPMN.

  Zapis w pliku BPMN (Spiff-Arena):
      <bpmn:userTask id="task_x">
        <bpmn:extensionElements>
          <spiffworkflow:properties>
            <spiffworkflow:property name="formJsonSchemaFilename" value="x.json"/>
            <spiffworkflow:property name="formUiSchemaFilename" value="x.uischema.json"/>

  Zwraca ({id_elementu: ElementFormFiles}, (id startEventow, ...)) - te same
  pliki moga byc wskazane dla startEvent (formularz startowy procesu).
  """
  try:
    root = ET.fromstring(xml_text)
  except ET.ParseError as exc:
    raise SpecError(f'Nieprawidlowy XML BPMN: {exc}') from exc

  files: dict[str, ElementFormFiles] = {}
  start_ids: list[str] = []
  for element in root.iter():
    element_id = element.get('id')
    if not element_id:
      continue
    if element.tag == f'{{{BPMN_NS}}}startEvent':
      start_ids.append(element_id)
    if element.tag not in FORM_ELEMENTS:
      continue
    properties = _extension_properties(element)
    form = ElementFormFiles(schema_file=properties.get(FORM_SCHEMA_PROP),
                            ui_schema_file=properties.get(FORM_UI_SCHEMA_PROP))
    if form.present:
      files[element_id] = form
  return files, tuple(start_ids)


def _extension_properties(element: ET.Element) -> dict[str, str]:
  """{nazwa: wartosc} z <...:properties><...:property name="..." value="..."/>."""
  found: dict[str, str] = {}
  for child in element.iter():
    if child.tag.rsplit('}', 1)[-1] != 'property':
      continue
    name, value = child.get('name'), child.get('value')
    if name and value:
      found[name] = value
  return found


class WorkflowEngine:
  """Uruchamia i wznawia instancje procesow na SpiffWorkflow.

  Stan silnika jest zapisywany w ProcessInstanceState.state (JSON) - ten
  modul go serializuje/deserializuje; reszta logiki (uprawnienia, walidacja
  formularza, spójność z tabelami human_task) jest w
  wf/services/process_service.py.
  """

  def __init__(self, script_engine: Any | None = None, serializer_version: str = SERIALIZER_VERSION):
    self.script_engine = script_engine
    self.serializer_version = serializer_version
    # domyslny rejestr BPMN (nie SPIFF_CONFIG).
    self.serializer = BpmnWorkflowSerializer(BpmnWorkflowSerializer.configure())

  # --- rejestr specyfikacji -------------------------------------------

  def build_spec(self, bpmn_files: Mapping[str, str], process_id: str) -> ProcessSpec:
    """Buduje specyfikacje procesu z plikow BPMN wersji modelu.

    `bpmn_files` - mapa {nazwa_pliku: tresc}. Plik glowny musi zawierac proces
    o id `process_id` (ProcessModelVersion.bpmn_process_id); pozostale pliki
    moga zawierac procesy pomocnicze (call activity).
    """
    parser = BpmnParser()
    assignments: dict[str, TaskAssignment] = {}
    for name in sorted(bpmn_files):
      content = bpmn_files[name]
      # `add_bpmn_str` przyjmuje `str` tylko bez deklaracji
      # XML - z deklaracja `encoding=...` lxml rzuca ValueError. Dlatego
      # wysyłamy bajty UTF-8 (test: SpiffWorkflow 3.2.0).
      try:
        parser.add_bpmn_str(content.encode('utf-8'))
      except (ValueError, ET.ParseError, XMLSyntaxError) as exc:
        # SpiffWorkflow parsuje lxml, a nie ElementTree,
        # więc błędny XML przychodzi jako lxml.etree.XMLSyntaxError.
        raise SpecError(f'Nie udalo sie sparsowac pliku {name}: {exc}') from exc
      assignments.update(_read_assignments(content))

    try:
      spec = parser.get_spec(process_id)
    except (KeyError, ValueError, ValidationException) as exc:
      # SpiffWorkflow rzuca własne ValidationException
      # ("The process 'x' was not found..."), a nie KeyError - bez tego
      # wyjątek uciekłby do klienta jako 500 zamiast 409 przy starcie procesu.
      raise SpecError(
        f'Proces {process_id} nie znaleziono w plikach BPMN wersji modelu') from exc
    subprocess_specs = parser.get_subprocess_specs(process_id)
    return ProcessSpec(process_id=process_id, spec=spec,
                       subprocess_specs=subprocess_specs, assignments=assignments)

  # --- cykl zycia instancji -------------------------------------------

  def start(self, process_spec: ProcessSpec, data: Mapping[str, Any] | None = None) -> BpmnWorkflow:
    """Tworzy instancje i wykonuje kroki bez zadan uzytkownika."""
    workflow = BpmnWorkflow(process_spec.spec, process_spec.subprocess_specs,
                            script_engine=self.script_engine)
    if data:
      workflow.data.update(data)
    workflow.do_engine_steps()
    return workflow

  def ready_user_tasks(self, workflow: BpmnWorkflow) -> list[Any]:
    """Zadania gotowe do wykonania przez czlowieka (Spiff: manual=True)."""
    return workflow.get_tasks(state=TaskState.READY, manual=True)

  def get_task(self, workflow: BpmnWorkflow, task: uuid.UUID | str):
    """Zadanie po jego UUID (HumanTask.id = TaskInstance.id w Spiffie).

    2026-10-05: SpiffWorkflow.get_task_from_id() przyjmuje UUID zadania,
    a nie id definicji BPMN - stąd jawne rzutowanie na UUID i obsługa
    braku zadania (TaskNotFoundException -> KeyError dla warstwy HTTP).
    """
    try:
      task_id = task if isinstance(task, uuid.UUID) else uuid.UUID(str(task))
    except ValueError as exc:
      raise KeyError(f'Nieprawidlowy identyfikator zadania: {task}') from exc
    if task_id not in workflow.tasks:
      raise KeyError(f'Zadanie {task_id} nie nalezy do tej instancji procesu')
    return workflow.get_task_from_id(task_id)

  def find_ready_task_by_spec_id(self, workflow: BpmnWorkflow, bpmn_task_id: str):
    """Znajduje gotowe zadanie po `task_spec.bpmn_id` (HumanTask.bpmn_task_id).

    2026-10-05: w SpiffWorkflow 3.x identyfikator definicji zadania to
    `task_spec.bpmn_id` (nie `id`) - nazwa zadania to `task_spec.bpmn_name`.
    """
    for task in self.ready_user_tasks(workflow):
      if task.task_spec.bpmn_id == bpmn_task_id:
        return task
    raise KeyError(f'Zadanie {bpmn_task_id} nie jest gotowe do wykonania')

  def complete(self, workflow: BpmnWorkflow, task: uuid.UUID | str,
               form_data: Mapping[str, Any] | None = None):
    """Wykonuje zadanie i przechodzi do kolejnych automatycznych krokow."""
    spiff_task = self.get_task(workflow, task)
    if form_data:
      spiff_task.data.update(form_data)
    # warunki gatewayów używają `chosen_path == 'Flow_*'`
    # (po normalizacji definicji), ale utrzymujemy zgodność ze starymi
    # wartościami liczbowymi i sentinelem `__default__` z formularza.
    # `normalize_choice` zamienia wartość na typ oczekiwany przez warunek
    # (int albo str), a `__default__` na None - wtedy gateway wybiera gałąź
    # `default=`. Gdy formularz nie niesie wyboru (np. zadanie bez decyzji),
    # zostaje dotychczasowe `None`, więc warunki `chosen_path == ...` nie
    # pasują i gateway bezpiecznie spada na `default=`.
    if form_data and 'chosen_path' in form_data:
      spiff_task.data['chosen_path'] = normalize_choice(spiff_task.data.get('chosen_path'))
    else:
      spiff_task.data.setdefault('chosen_path', None)
    spiff_task.run()
    workflow.do_engine_steps()
    return workflow

  def is_completed(self, workflow: BpmnWorkflow) -> bool:
    return workflow.is_completed()

  @staticmethod
  def bpmn_task_id(task) -> str:
    """`HumanTask.bpmn_task_id` dla zadania Spiffa."""
    return str(task.task_spec.bpmn_id)

  @staticmethod
  def task_name(task) -> str:
    """Nazwa zadania z BPMN; gdy brak `name`, używamy id definicji."""
    return str(task.task_spec.bpmn_name or task.task_spec.bpmn_id)

  @staticmethod
  def task_lane(task) -> str | None:
    """`bpmn:lane` zadania (kolumna HumanTask.lane) albo None."""
    lane = getattr(task.task_spec, 'lane', None)
    return str(lane.name) if lane is not None else None

  # --- serializacja stanu ----------------------------------------------

  def dump(self, workflow: BpmnWorkflow) -> str:
    return self.serializer.serialize_json(workflow)

  def load(self, raw: str | Mapping[str, Any]) -> BpmnWorkflow:
    if isinstance(raw, Mapping):
      raw = json.dumps(raw)
    return self.serializer.deserialize_json(raw)

  def dump_state(self, workflow: BpmnWorkflow) -> dict[str, Any]:
    """Stan gotowy do kolumny JSON ProcessInstanceState.state."""
    return json.loads(self.dump(workflow))

  def load_state(self, state: Mapping[str, Any]) -> BpmnWorkflow:
    return self.load(state)


def assignment_for(process_spec: ProcessSpec, bpmn_task_id: str) -> TaskAssignment:
  """Przypisanie zadania (brak -> TaskAssignment(), czyli brak ograniczen)."""
  return process_spec.assignments.get(bpmn_task_id, TaskAssignment())
