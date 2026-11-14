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
#
# 2026-11-13: `serviceTask` z wlasciwoscia `handler` to krok systemowy -
# Spiff zatrzymuje na nim proces (stan STARTED), a hub wykonuje obsluge
# z wf/services/service_tasks.py (ProcessService.advance_system). `sendTask`
# z `startProcess` uruchamia osobna instancje innego modelu (StartLink),
# a `callActivity` moze wywolac proces z innego modelu (ProcessSpec.called_versions).
from __future__ import annotations

import json
import re
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from SpiffWorkflow.bpmn.parser.BpmnParser import BpmnParser
from SpiffWorkflow.bpmn.exceptions import WorkflowDataException
from SpiffWorkflow.bpmn.parser.ValidationException import ValidationException
from SpiffWorkflow.bpmn.serializer import BpmnWorkflowSerializer
from SpiffWorkflow.bpmn.serializer.config import DEFAULT_CONFIG
from SpiffWorkflow.bpmn.serializer.default.task_spec import BpmnTaskSpecConverter
from SpiffWorkflow.bpmn.specs.defaults import ServiceTask
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


# BEGIN 2026-11-13: uruchamianie osobnej instancji z procesu
@dataclass(frozen=True)
class StartLink:
  """Deklaracja w BPMN: element uruchamia osobną instancję innego modelu.

  - `sendTask`/`intermediateThrowEvent` z właściwością `startProcess`
    (`automatic=True`) - hub uruchamia instancję, gdy proces przejdzie
    przez element, i nie czeka na nią,
  - `userTask`/`manualTask` z właściwością `quickAction` (`automatic=False`)
    - przycisk w formularzu otwartego zadania.
  `inputs` - jawne mapowanie danych (`startInput`: "a, b->c, Sekcja.pole->d"),
  `title` - szablon Jinja tytułu (`startTitle`)."""
  element_id: str
  name: str
  process_keys: tuple[str, ...]
  automatic: bool
  inputs: tuple[tuple[str, str], ...] = ()
  title: str | None = None


HANDLER_PROP = 'handler'
START_PROCESS_PROP = 'startProcess'
QUICK_ACTION_PROP = 'quickAction'
START_INPUT_PROP = 'startInput'
START_TITLE_PROP = 'startTitle'
_AUTO_START_TAGS = (f'{{{BPMN_NS}}}sendTask', f'{{{BPMN_NS}}}intermediateThrowEvent')
_ACTION_TAGS = (f'{{{BPMN_NS}}}userTask', f'{{{BPMN_NS}}}manualTask')


def _parse_inputs(value: str | None) -> tuple[tuple[str, str], ...]:
  pairs = []
  for item in _split_list(value):
    source, _, target = item.partition('->')
    source, target = source.strip(), (target.strip() or source.strip())
    if source:
      pairs.append((source, target))
  return tuple(pairs)


@dataclass(frozen=True)
class ServiceCall:
  """2026-11-13: `serviceTask` wykonywany przez hub - `handler` wskazuje
  obsługę z wf/services/service_tasks.py; `params` - pozostałe właściwości
  elementu (np. `retryMinutes`)."""
  element_id: str
  name: str
  handler: str
  params: Mapping[str, str] = field(default_factory=dict)


def read_service_calls(xml_text: str) -> dict[str, dict[str, ServiceCall]]:
  """{id procesu: {id elementu: ServiceCall}} dla `serviceTask` w pliku BPMN
  (także bez `handler` - pusty napis, hub zgłosi brak obsługi)."""
  try:
    root = ET.fromstring(xml_text)
  except ET.ParseError as exc:
    raise SpecError(f'Nieprawidlowy XML BPMN: {exc}') from exc
  result: dict[str, dict[str, ServiceCall]] = {}
  for process in root.iter(f'{{{BPMN_NS}}}process'):
    calls = {}
    for element in process.iter(f'{{{BPMN_NS}}}serviceTask'):
      if not element.get('id'):
        continue
      props = _extension_properties(element)
      calls[element.get('id')] = ServiceCall(
        element_id=element.get('id'), name=element.get('name') or element.get('id'),
        handler=props.pop(HANDLER_PROP, ''), params=props)
    if process.get('id') and calls:
      result[process.get('id')] = calls
  return result


def read_start_links(xml_text: str) -> dict[str, dict[str, StartLink]]:
  """{id procesu: {id elementu: StartLink}} z jednego pliku BPMN."""
  try:
    root = ET.fromstring(xml_text)
  except ET.ParseError as exc:
    raise SpecError(f'Nieprawidlowy XML BPMN: {exc}') from exc
  result: dict[str, dict[str, StartLink]] = {}
  for process in root.iter(f'{{{BPMN_NS}}}process'):
    links: dict[str, StartLink] = {}
    for element in process.iter():
      automatic = element.tag in _AUTO_START_TAGS
      if not (automatic or element.tag in _ACTION_TAGS) or not element.get('id'):
        continue
      props = _extension_properties(element)
      keys = _split_list(props.get(START_PROCESS_PROP if automatic else QUICK_ACTION_PROP))
      if keys:
        links[element.get('id')] = StartLink(
          element_id=element.get('id'), name=element.get('name') or element.get('id'),
          process_keys=keys, automatic=automatic,
          inputs=_parse_inputs(props.get(START_INPUT_PROP)),
          title=props.get(START_TITLE_PROP))
    if process.get('id') and links:
      result[process.get('id')] = links
  return result
# -END


@dataclass(frozen=True)
class ProcessSpec:
  """Specyfikacja procesu gotowa do uruchomienia."""
  process_id: str
  spec: Any
  subprocess_specs: dict[str, Any]
  assignments: Mapping[str, TaskAssignment] = field(default_factory=dict)
  # BEGIN 2026-11-13: callActivity między modelami
  # {id procesu: {id zadania: przypisanie}} - id zadań powtarzają się między
  # procesami (np. Task_01 w rodzicu i w podprocesie).
  process_assignments: Mapping[str, Mapping[str, TaskAssignment]] = field(default_factory=dict)
  # {id procesu wywoływanego: id wersji modelu, z której pochodzi}
  called_versions: Mapping[str, str] = field(default_factory=dict)
  # {id procesu: {id elementu: StartLink}} - zob. read_start_links
  start_links: Mapping[str, Mapping[str, StartLink]] = field(default_factory=dict)
  # {id procesu: {id elementu: ServiceCall}} - zob. read_service_calls
  service_calls: Mapping[str, Mapping[str, ServiceCall]] = field(default_factory=dict)
  # -END


def _split_list(value: str | None) -> tuple[str, ...]:
  if not value:
    return ()
  return tuple(item.strip() for item in value.split(',') if item.strip())


# BEGIN 2026-11-13
def _read_assignments_by_process(xml_text: str) -> dict[str, dict[str, TaskAssignment]]:
  """Przypisania zadań osobno dla każdego `bpmn:process` w pliku."""
  try:
    root = ET.fromstring(xml_text)
  except ET.ParseError as exc:
    raise SpecError(f'Nieprawidlowy XML BPMN: {exc}') from exc
  result: dict[str, dict[str, TaskAssignment]] = {}
  for process in root.iter(f'{{{BPMN_NS}}}process'):
    pid = process.get('id')
    if pid:
      result[pid] = _assignments_in(process)
  return result


def called_elements(xml_text: str) -> set[str]:
  """`calledElement` wszystkich `callActivity` w pliku BPMN."""
  try:
    root = ET.fromstring(xml_text)
  except ET.ParseError:
    return set()
  return {el.get('calledElement') for el in root.iter(f'{{{BPMN_NS}}}callActivity')
          if el.get('calledElement')}


def defined_processes(xml_text: str) -> set[str]:
  """Id procesów (`bpmn:process`) zdefiniowanych w pliku BPMN."""
  try:
    root = ET.fromstring(xml_text)
  except ET.ParseError:
    return set()
  return {el.get('id') for el in root.iter(f'{{{BPMN_NS}}}process') if el.get('id')}


def start_event_name(xml_texts: Iterable[str], event_id: str | None) -> str | None:
  """2026-11-13: nazwa `startEvent` o danym id (krok formularza startowego)."""
  for xml_text in xml_texts:
    try:
      root = ET.fromstring(xml_text)
    except ET.ParseError:
      continue
    for element in root.iter(f'{{{BPMN_NS}}}startEvent'):
      if element.get('id') == event_id:
        return (element.get('name') or '').strip() or None
  return None


def has_io_specification(xml_text: str, process_id: str) -> bool:
  """Czy proces deklaruje dane wejścia/wyjścia (`ioSpecification`) -
  bez niej SpiffWorkflow kopiuje do podprocesu WSZYSTKIE dane rodzica."""
  try:
    root = ET.fromstring(xml_text)
  except ET.ParseError:
    return False
  for process in root.iter(f'{{{BPMN_NS}}}process'):
    if process.get('id') == process_id:
      return process.find(f'{{{BPMN_NS}}}ioSpecification') is not None
  return False


def _assignments_in(scope: ET.Element) -> dict[str, TaskAssignment]:
  assignments: dict[str, TaskAssignment] = {}
  for element in scope.iter():
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
# -END


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
    # 2026-11-13: + ServiceTask (kroki systemowe wykonywane przez hub) -
    # domyślna konfiguracja nie umie go zserializować.
    config = dict(DEFAULT_CONFIG)
    config[ServiceTask] = BpmnTaskSpecConverter
    self.serializer = BpmnWorkflowSerializer(BpmnWorkflowSerializer.configure(config))

  # --- rejestr specyfikacji -------------------------------------------

  def build_spec(self, bpmn_files: Mapping[str, str], process_id: str,
                 called_files: Mapping[str, Mapping[str, str]] | None = None,
                 called_versions: Mapping[str, str] | None = None) -> ProcessSpec:
    """Buduje specyfikacje procesu z plikow BPMN wersji modelu.

    `bpmn_files` - mapa {nazwa_pliku: tresc}. Plik glowny musi zawierac proces
    o id `process_id` (ProcessModelVersion.bpmn_process_id); pozostale pliki
    moga zawierac procesy pomocnicze (call activity).

    2026-11-13: `called_files` - pliki BPMN wersji innych modeli
    {id wersji: {nazwa: treść}} dla `callActivity` wskazujących procesy
    spoza tej wersji; `called_versions` - {id procesu: id wersji}.
    """
    parser = BpmnParser()
    assignments: dict[str, TaskAssignment] = {}
    process_assignments: dict[str, dict[str, TaskAssignment]] = {}
    start_links: dict[str, dict[str, StartLink]] = {}
    service_calls: dict[str, dict[str, ServiceCall]] = {}
    all_files = [(name, bpmn_files[name], True) for name in sorted(bpmn_files)]
    for version_id in sorted(called_files or {}):
      files = called_files[version_id]
      all_files += [(f'{version_id}/{name}', files[name], False) for name in sorted(files)]
    for name, content, own in all_files:
      # `add_bpmn_str` przyjmuje `str` tylko bez deklaracji
      # XML - z deklaracja `encoding=...` lxml rzuca ValueError. Dlatego
      # wysyłamy bajty UTF-8 (test: SpiffWorkflow 3.2.0).
      try:
        parser.add_bpmn_str(content.encode('utf-8'))
      except (ValueError, ET.ParseError, XMLSyntaxError) as exc:
        # SpiffWorkflow parsuje lxml, a nie ElementTree,
        # więc błędny XML przychodzi jako lxml.etree.XMLSyntaxError.
        raise SpecError(f'Nie udalo sie sparsowac pliku {name}: {exc}') from exc
      if own:
        assignments.update(_read_assignments(content))
      process_assignments.update(_read_assignments_by_process(content))
      start_links.update(read_start_links(content))
      service_calls.update(read_service_calls(content))

    try:
      spec = parser.get_spec(process_id)
    except (KeyError, ValueError, ValidationException) as exc:
      # SpiffWorkflow rzuca własne ValidationException
      # ("The process 'x' was not found..."), a nie KeyError - bez tego
      # wyjątek uciekłby do klienta jako 500 zamiast 409 przy starcie procesu.
      raise SpecError(
        f'Proces {process_id} nie znaleziono w plikach BPMN wersji modelu') from exc
    try:
      subprocess_specs = parser.get_subprocess_specs(process_id)
    except (KeyError, ValueError, ValidationException) as exc:
      # 2026-11-13: brak procesu wywoływanego przez callActivity - był
      # poza `try`, więc kończył się błędem 500 przy każdej operacji.
      raise SpecError(f'Proces {process_id}: nie znaleziono procesu wywoływanego '
                      f'(callActivity): {exc}') from exc
    return ProcessSpec(process_id=process_id, spec=spec,
                       subprocess_specs=subprocess_specs, assignments=assignments,
                       process_assignments=process_assignments,
                       called_versions=dict(called_versions or {}),
                       start_links=start_links, service_calls=service_calls)

  # --- cykl zycia instancji -------------------------------------------

  def start(self, process_spec: ProcessSpec, data: Mapping[str, Any] | None = None) -> BpmnWorkflow:
    """Tworzy instancje i wykonuje kroki bez zadan uzytkownika."""
    workflow = BpmnWorkflow(process_spec.spec, process_spec.subprocess_specs,
                            script_engine=self.script_engine)
    if data:
      workflow.data.update(data)
      # 2026-11-13: dane startowe (formularz na startEvent) także w danych
      # zadań - jak dane dawnego kroku "Start"; warunki bramek czytają
      # dane zadania, nie `workflow.data`.
      for task in workflow.get_tasks(state=TaskState.READY):
        task.data.update(data)
    self._engine_steps(workflow)
    return workflow

  # BEGIN 2026-11-13
  def _engine_steps(self, workflow: BpmnWorkflow, first=None) -> None:
    """`do_engine_steps` z uzupełnianiem brakujących wejść podprocesu.

    Proces wywoływany deklaruje wejścia w `ioSpecification`; gdy proces
    wywołujący ich nie ma, Spiff rzuca WorkflowDataException. Zamiast
    wymagać ich w każdym modelu wywołującym, brakujące wejście dostaje
    `None` - podproces nadal nie widzi pozostałych danych rodzica i sam
    uzupełnia brak (np. podpis bierze główny dokument instancji)."""
    for _attempt in range(20):
      try:
        if first is not None:
          step, first = first, None
          step()
        workflow.do_engine_steps()
        return
      except WorkflowDataException as exc:
        if not self._fill_missing_inputs(workflow, exc):
          raise
    raise SpecError('Nie udało się uzupełnić danych wejścia podprocesu')

  @staticmethod
  def _fill_missing_inputs(workflow: BpmnWorkflow, exc: WorkflowDataException) -> bool:
    task = getattr(exc, 'task', None)
    called = getattr(getattr(task, 'task_spec', None), 'spec', None)
    sub_spec = (workflow.subprocess_specs or {}).get(called) if called else None
    io_spec = getattr(sub_spec, 'io_specification', None)
    if task is None or io_spec is None:
      return False
    missing = [item.bpmn_id for item in io_spec.data_inputs if item.bpmn_id not in task.data]
    for name in missing:
      task.data[name] = None
    return bool(missing)
  # -END

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
    # 2026-11-13: zadania podprocesów (callActivity) są w osobnych
    # pod-workflow, nie w `workflow.tasks`.
    for scope in (workflow, *workflow.subprocesses.values()):
      if task_id in scope.tasks:
        return scope.get_task_from_id(task_id)
    raise KeyError(f'Zadanie {task_id} nie nalezy do tej instancji procesu')

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
    self._engine_steps(workflow, first=spiff_task.run)
    return workflow

  def waiting_service_tasks(self, workflow: BpmnWorkflow) -> list[Any]:
    """2026-11-13: `serviceTask` czekające na wynik (stan STARTED) -
    proces stoi, dopóki hub ich nie zakończy (`complete_service_task`)."""
    tasks = {task.id: task for scope in (workflow, *workflow.subprocesses.values())
             for task in scope.get_tasks(state=TaskState.STARTED)
             if isinstance(task.task_spec, ServiceTask)}
    return list(tasks.values())

  def complete_service_task(self, workflow: BpmnWorkflow, task, data: Mapping[str, Any]) -> None:
    """2026-11-13: kończy krok systemowy z danymi wynikowymi."""
    task.data.update(dict(data or {}))
    self._engine_steps(workflow, first=task.complete)

  def completed_tasks(self, workflow: BpmnWorkflow) -> list[Any]:
    """2026-11-13: wykonane zadania procesu i jego podprocesów."""
    tasks = {task.id: task for scope in (workflow, *workflow.subprocesses.values())
             for task in scope.get_tasks(state=TaskState.COMPLETED)}
    return list(tasks.values())

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
  def task_process_id(task) -> str:
    """2026-11-13: id procesu BPMN, w którym leży zadanie (rodzic albo
    proces wywołany przez callActivity)."""
    return str(task.workflow.spec.name)

  @staticmethod
  def task_lane(task) -> str | None:
    """`bpmn:lane` zadania (kolumna HumanTask.lane) albo None."""
    lane = getattr(task.task_spec, 'lane', None)
    # 2026-11-12: SpiffWorkflow 3.x podaje nazwę toru jako str (bez `.name`) -
    # dawny `lane.name` kończył się AttributeError na każdym zadaniu z toru.
    return str(getattr(lane, 'name', lane)) if lane is not None else None

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


def assignment_for(process_spec: ProcessSpec, bpmn_task_id: str,
                   bpmn_process_id: str | None = None) -> TaskAssignment:
  """Przypisanie zadania (brak -> TaskAssignment(), czyli brak ograniczen).

  2026-11-13: `bpmn_process_id` - zadanie podprocesu (callActivity)
  bierze przypisanie ze swojego procesu, nie z rodzica o tym samym id zadania."""
  if bpmn_process_id and bpmn_process_id in process_spec.process_assignments:
    return process_spec.process_assignments[bpmn_process_id].get(bpmn_task_id, TaskAssignment())
  return process_spec.assignments.get(bpmn_task_id, TaskAssignment())
