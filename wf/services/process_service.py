# wf/services/process_service.py - logika aplikacji, bez szczegółów Spiffa
# Odpowiedzialności:
#   * zbudowanie specyfikacji procesu z plików ProcessModelFile (BPMN),
#   * start instancji: Spiff -> ProcessInstance + ProcessInstanceState + HumanTask,
#   * wykonanie zadania: autoryzacja (przypisanie z BPMN), walidacja danych
#     formularza (JSON Schema z ProcessModelVersion.spec_json), zapis stanu,
#     synchronizacja tabeli human_task i dziennika ProcessEvent,
#   * anulowanie/wstrzymanie/wznowienie instancji (blokada optimistic przez
#     `lock_version`, którą pilnuje ProcessInstance.__mapper_args__).
#
# Silnik jest wstrzykiwany, ale domyślnie tworzony jest leniwie i współdzielony
# (`get_service()`), bo SpiffWorkflow nie jest thread-safe per instancja, a
# serializacja rejestru jest kosztowna.
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any, Iterable, Mapping

from jinja2.exceptions import TemplateError
from jinja2.sandbox import SandboxedEnvironment
from sqlalchemy import select
from sqlalchemy.orm import Session, object_session
from SpiffWorkflow.bpmn.exceptions import WorkflowTaskException

from core.redis_cache import cache_get_json, cache_set_json
from wf.decisions import (
    DEFAULT_FIELD,
    default_value,
    decisions_enabled,
    inject_decision,
    read_decisions,
)
# 2026-11-06: słuchacze zdarzeń (start instancji, wykonanie zadania) -
# wywoływani po commicie, zob. wf/services/process_hooks.py.
from wf.services import process_hooks, service_tasks
from wf.engine import (
  SERIALIZER_VERSION,
  UNRESOLVED_PRINCIPAL,
  ProcessSpec,
  SpecError,
  TaskAssignment,
  WorkflowEngine,
  assignment_for,
  called_elements,
  defined_processes,
  start_event_name,
  has_io_specification,
  read_form_files,
)
from wf.services.form_permissions import filter_form_for_step, has_x_permissions
from wf.models import (
    HumanTask,
    HumanTaskCandidate,
    ProcessEvent,
    ProcessModelFile,
    ProcessInstance,
    ProcessInstanceState,
    ProcessModel,
    ProcessModelVersion,
)
from wf.schemas.wf_schemas import (
    CurrentUser,
    EventType,
    HumanTaskStatus,
    ProcessFileType,
    ProcessInstanceStatus,
    ProcessModelVersionStatus,
    PrincipalType,
)

logger = logging.getLogger(__name__)


class ProcessServiceError(RuntimeError):
  """Błąd logiki procesu mapowany przez routery na HTTP 409."""


class NotAuthorizedError(PermissionError):
  """Użytkownik nie może wykonać zadania - router mapuje na HTTP 403."""


class InvalidTaskDataError(ValueError):
  """Dane formularza nie przechodzą walidacji JSON Schema - HTTP 422."""


class FormRenderError(ValueError):
  """Szablon Jinja w formularzu nie daje się wyrenderować - HTTP 422."""


# Sufiksy nazw plików Spiff-Areny obcinane przy budowie klucza formularza
# ('ocena.uischema.json' i 'ocena.json' -> klucz 'ocena'). Kolejność ważna:
# najpierw dłuższe sufiksy.
# 2026-10-06: formularze Spiff-Arena (JSON Schema + uiSchema dla RJSF).
_FORM_SUFFIXES = ('.uischema.json', '.ui.json', '_ui.json', '.uischema', '.json')


def form_key(file_name: str) -> str:
  """Klucz formularza z nazwy pliku (patrz _FORM_SUFFIXES)."""
  name = (file_name or '').strip()
  for suffix in _FORM_SUFFIXES:
    if name.endswith(suffix) and len(name) > len(suffix):
      return name[: -len(suffix)]
  return name


def build_spec_json(files: Iterable[tuple[str, Any, str]]) -> dict[str, Any]:
  """Indeks formularzy dla `ProcessModelVersion.spec_json`.

  `files` - krotki (file_name, file_type, content). Zwraca:
    * `forms`      {klucz: treść JSON Schema},
    * `uischemas`  {klucz: treść UI Schema},
    * `form_files` {id elementu BPMN: {'schema': nazwa, 'ui': nazwa}},
    * `start_form` {task_id: id pierwszego startEvent},
    * `decisions`  {id zadania: wybór ścieżki gatewaya}.

  Treść plików zostaje jako surowy tekst, bo schemat może zawierać szablony
  Jinja i nie być poprawnym JSON przed renderowaniem (`render_form_json`).
  """
  forms: dict[str, str] = {}
  uischemas: dict[str, str] = {}
  form_files: dict[str, dict[str, str]] = {}
  decisions: dict[str, dict[str, Any]] = {}
  start_ids: tuple[str, ...] = ()

  for file_name, file_type, content in files:
    if file_type == ProcessFileType.JSON_SCHEMA:
      forms[form_key(file_name)] = content
    elif file_type == ProcessFileType.UI_SCHEMA:
      uischemas[form_key(file_name)] = content
    elif file_type == ProcessFileType.BPMN:
      elements, ids = read_form_files(content)
      start_ids = start_ids or ids
      for element_id, ref in elements.items():
        mapping = {kind: value for kind, value in (('schema', ref.schema_file),
                                                   ('ui', ref.ui_schema_file))
                   if value}
        if mapping:
          form_files.setdefault(element_id, {}).update(mapping)
      # 2026-10-17: decyzje gatewayów liczone z BPMN (wf/decisions.py) -
      # jedno miejsce dla wszystkich definicji, także przyszłych.
      for task_id, decision in read_decisions(content).items():
        decisions[task_id] = decision.as_dict()

  spec: dict[str, Any] = {'forms': forms, 'uischemas': uischemas}
  if form_files:
    spec['form_files'] = form_files
  if decisions:
    spec['decisions'] = decisions
  if start_ids and (form_files or forms or uischemas):
    # Bez plików startowego formularza start_form nie ma sensu - klient i tak
    # dostanie pusty schemat.
    task_id = next((sid for sid in start_ids if sid in form_files), start_ids[0])
    spec['start_form'] = {'task_id': task_id}
  return spec


_jinja: SandboxedEnvironment | None = None


def _jinja_env() -> SandboxedEnvironment:
  """Współdzielone środowisko Jinja (jak w Spiff-Arenej) - bezpieczny sandbox."""
  global _jinja
  if _jinja is None:
    _jinja = SandboxedEnvironment(autoescape=False)
  return _jinja


def render_form_json(template: Any, data: Mapping[str, Any] | None = None) -> dict[str, Any]:
  """Renderuje szablon Jinja w formularzu i parsuje wynik do słownika.

  Spiff-Arena renderuje schematy po stronie serwera danymi zadania, zanim
  trafią do react-jsonschema-form. Kontekst to klucze danych zadania oraz
  `data` (pełny słownik) - bez markerów Jinja to zwykły JSON.
  """
  if isinstance(template, Mapping):
    text = json.dumps(template, ensure_ascii=False)
  elif isinstance(template, str):
    text = template
  else:
    raise FormRenderError(f'Formularz musi być obiektem JSON (jest: {type(template).__name__})')

  if '{%' in text or '{{' in text:
    context = {key: value for key, value in dict(data or {}).items() if isinstance(key, str)}
    context['data'] = dict(data or {})
    try:
      text = _jinja_env().from_string(text).render(**context)
    except TemplateError as exc:
      raise FormRenderError(f'Szablon formularza nie renderuje się: {exc}') from exc

  try:
    parsed = json.loads(text)
  except ValueError as exc:
    raise FormRenderError(f'Formularz nie jest poprawnym JSON: {exc}') from exc
  if not isinstance(parsed, dict):
    raise FormRenderError('Formularz musi być obiektem JSON')
  return parsed


def _form_entry(forms: Mapping[str, Any], form_files: Mapping[str, Any],
                bpmn_task_id: str | None, kind: str) -> Any:
  """Treść formularza: plik wskazany w rozszerzeniu BPMN, potem klucz = id."""
  if bpmn_task_id:
    mapping = form_files.get(bpmn_task_id) or {}
    file_name = mapping.get(kind)
    if file_name:
      entry = forms.get(form_key(str(file_name)))
      if entry is not None:
        return entry
    return forms.get(bpmn_task_id)
  return None


def _try_parse_json(value: Any) -> dict[str, Any] | None:
  """Jak wnętrze `render_form_json`, ale bez renderowania Jinja i bez
  podnoszenia wyjątku - używane tylko do sprawdzenia/zastosowania filtra
  `x-permissions` (wf/services/form_permissions.py) na treści formularza
  zanim dojdzie do niej Jinja (zob. task_form)."""
  if isinstance(value, Mapping):
    return dict(value)
  if not isinstance(value, str):
    return None
  try:
    parsed = json.loads(value)
  except ValueError:
    return None
  return parsed if isinstance(parsed, dict) else None


class ProcessService:
  """Serwis procesów - jedyne miejsce, które spina silnik Spiff i bazę."""

  def __init__(self, engine: WorkflowEngine | None = None):
    self.engine = engine or WorkflowEngine()

  # --- specyfikacja -----------------------------------------------------

  def published_version(self, db: Session, process_model_key: str) -> ProcessModelVersion:
    """Najnowsza opublikowana wersja modelu procesu o podanym kluczu."""
    version = db.execute(
      select(ProcessModelVersion)
      .join(ProcessModel)
      .where(ProcessModel.key == process_model_key,
             ProcessModelVersion.status == ProcessModelVersionStatus.PUBLISHED)
      .order_by(ProcessModelVersion.version.desc())
    ).scalars().first()
    if version is None:
      raise ProcessServiceError(f"Brak opublikowanej wersji procesu '{process_model_key}'")
    return version

  def load_spec(self, version: ProcessModelVersion) -> ProcessSpec:
    """Buduje specyfikację Spiffa z plików BPMN wersji modelu.

    2026-11-13: procesy wywoływane przez `callActivity`, których nie ma
    w plikach tej wersji, pochodzą z wersji innych modeli (przypiętych przy
    publikacji - zob. `pin_called_versions`)."""
    files = _bpmn_files(version)
    if not files:
      raise SpecError(f"Wersja procesu {version.version} nie zawiera plików BPMN")
    db = object_session(version)
    called = self.called_versions(db, version) if db is not None else {}
    called_files = {str(v.id): _bpmn_files(v) for v in called.values()}
    try:
      return self.engine.build_spec(
        files, version.bpmn_process_id, called_files=called_files,
        called_versions={pid: str(v.id) for pid, v in called.items()})
    except SpecError as exc:
      raise ProcessServiceError(str(exc)) from exc

  # BEGIN 2026-11-13: callActivity między modelami
  def called_versions(self, db: Session, version: ProcessModelVersion, *,
                      pinned: bool = True,
                      _visited: frozenset | None = None) -> dict[str, ProcessModelVersion]:
    """{id procesu wywoływanego: wersja modelu} dla `callActivity` wskazujących
    procesy spoza plików `version` - także pośrednio (podproces podprocesu).

    Najpierw wersja przypięta w `spec_json['called_versions']` (`pinned`),
    potem opublikowana wersja modelu, który ten proces definiuje."""
    visited = (_visited or frozenset()) | {version.id}
    files = _bpmn_files(version)
    own = set().union(*(defined_processes(c) for c in files.values())) if files else set()
    wanted = set().union(*(called_elements(c) for c in files.values())) - own if files else set()
    pins = ((version.spec_json or {}).get('called_versions') or {}) if pinned else {}
    result: dict[str, ProcessModelVersion] = {}
    for process_id in sorted(wanted):
      target = db.get(ProcessModelVersion, _as_uuid(pins[process_id])) if process_id in pins else None
      if target is None:
        target = self._published_defining(db, process_id, exclude_model_id=version.process_model_id)
      result[process_id] = target
      if target.id not in visited:
        result.update({pid: v for pid, v in self.called_versions(
          db, target, pinned=pinned, _visited=visited).items() if pid not in result})
    return result

  def _published_defining(self, db: Session, process_id: str, *,
                          exclude_model_id) -> ProcessModelVersion:
    """Opublikowana wersja innego modelu definiująca proces `process_id`."""
    published = (select(ProcessModelVersion)
                 .where(ProcessModelVersion.status == ProcessModelVersionStatus.PUBLISHED,
                        ProcessModelVersion.process_model_id != exclude_model_id))
    found = db.execute(published.where(ProcessModelVersion.bpmn_process_id == process_id)
                       ).scalars().all()
    if not found:
      candidates = db.execute(
        published.join(ProcessModelFile, ProcessModelFile.version_id == ProcessModelVersion.id)
        .where(ProcessModelFile.file_type == ProcessFileType.BPMN,
               ProcessModelFile.content.contains(f'id="{process_id}"'))
      ).scalars().unique().all()
      found = [v for v in candidates
               if any(process_id in defined_processes(c) for c in _bpmn_files(v).values())]
    if not found:
      raise ProcessServiceError(
        f"Proces wywoływany '{process_id}' (callActivity) nie ma opublikowanej wersji "
        f"w żadnym modelu - opublikuj najpierw model, który go definiuje")
    if len(found) > 1:
      keys = ', '.join(sorted(v.process_model.key for v in found))
      raise ProcessServiceError(
        f"Proces wywoływany '{process_id}' jest zdefiniowany w kilku opublikowanych "
        f"modelach ({keys}) - nazwy procesów BPMN muszą być unikalne")
    return found[0]

  def pin_called_versions(self, db: Session, version: ProcessModelVersion) -> list[str]:
    """Przy publikacji: przypina bieżące opublikowane wersje procesów
    wywoływanych (`spec_json['called_versions']`), żeby późniejsza publikacja
    modelu wywoływanego nie zmieniała procesu wywołującego. Zwraca
    ostrzeżenia (proces wywoływany bez `ioSpecification` dostaje WSZYSTKIE
    dane rodzica - istotne dla RODO)."""
    called = self.called_versions(db, version, pinned=False)
    direct = set().union(*(called_elements(c) for c in _bpmn_files(version).values()))
    spec_json = {key: value for key, value in (version.spec_json or {}).items()
                 if key != 'called_versions'}
    if called:
      spec_json['called_versions'] = {pid: str(v.id) for pid, v in called.items() if pid in direct}
    version.spec_json = spec_json
    warnings = []
    for pid, target in sorted(called.items()):
      if not any(has_io_specification(c, pid) for c in _bpmn_files(target).values()):
        warnings.append(
          f"Proces wywoływany '{pid}' (model {target.process_model.key}) nie deklaruje "
          f"danych wejścia/wyjścia (ioSpecification) - dostanie wszystkie dane procesu wywołującego")
    return warnings

  @staticmethod
  def task_version(human_task: HumanTask) -> ProcessModelVersion:
    """Wersja modelu, z której pochodzi formularz zadania: dla zadania
    podprocesu z innego modelu - jego wersja, inaczej wersja instancji."""
    return human_task.form_version or human_task.process_instance.version
  # -END

  def form_spec(self, version: ProcessModelVersion) -> dict[str, Any]:
    """Schematy formularzy wersji: pliki wersji + `spec_json` (nakładka).

    Pliki JSON_SCHEMA/UI_SCHEMA i rozszerzenia BPMN (`formJsonSchemaFilename`)
    liczone są zawsze, a `ProcessModelVersion.spec_json` nakłada się na wierzch -
    dzięki temu wersje tworzone bez plików (np. w testach) działają po staremu.
    """
    derived = build_spec_json(
      (file.file_name, file.file_type, file.content) for file in version.files)
    stored = version.spec_json if isinstance(version.spec_json, Mapping) else {}

    def merged(section: str) -> dict[str, Any]:
      result = dict(derived.get(section) or {})
      result.update(stored.get(section) or {})
      return result

    return {
      'forms': merged('forms'),
      'uischemas': merged('uischemas'),
      'form_files': merged('form_files'),
      'decisions': merged('decisions'),
      'start_form': stored.get('start_form') or derived.get('start_form'),
    }

  def form_schema(self, version: ProcessModelVersion, bpmn_task_id: str | None,
                  data: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
    """JSON Schema formularza zadania, wyrenderowany danymi (Jinja).

    `None` = zadanie nie ma formularza (klient pokazuje zapasowy edytor JSON).
    """
    spec = self.form_spec(version)
    entry = _form_entry(spec['forms'], spec['form_files'], bpmn_task_id, 'schema')
    return render_form_json(entry, data) if entry is not None else None

  def form_ui_schema(self, version: ProcessModelVersion, bpmn_task_id: str | None,
                     data: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
    """UI Schema (react-jsonschema-form) formularza zadania."""
    spec = self.form_spec(version)
    entry = _form_entry(spec['uischemas'], spec['form_files'], bpmn_task_id, 'ui')
    return render_form_json(entry, data) if entry is not None else None

  def task_form(self, version: ProcessModelVersion, human_task: HumanTask,
               data: Mapping[str, Any] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """JSON Schema + UI Schema formularza zadania (`GET /tasks/{id}/form`),
    przefiltrowane wg macierzy `x-permissions` dla kroku `human_task.name` -
    zob. wf/services/form_permissions.py - i wynik
    tego filtrowania zbuforowany w Redis (core/redis_cache.py), żeby nie
    przeliczać go przy każdym wejściu w zadanie (ten sam wynik dla
    wszystkich instancji tego procesu w tym kroku).

    Formularze bez `x-permissions` (Spiff-Arena/Jinja) nie są filtrowane ani buforowane 
    - zachowanie identyczne jak dawne `form_schema()`/`form_ui_schema()` wywołane osobno.
    """
    spec = self.form_spec(version)
    schema_entry = _form_entry(spec['forms'], spec['form_files'], human_task.bpmn_task_id, 'schema')
    if schema_entry is None:
      # Zadanie bez formularza: nadal może mieć decyzję gatewaya, więc nie
      # wychodzimy wcześnie - niżej wstrzykniemy schemat z samym wyborem.
      schema, ui_schema = {}, {}
    else:
      ui_entry = _form_entry(spec['uischemas'], spec['form_files'], human_task.bpmn_task_id, 'ui')
      schema_obj = _try_parse_json(schema_entry)
      if schema_obj is None or not has_x_permissions(schema_obj):
        # Nie da się sparsować bez renderowania Jinja (szablon Spiff-Arena),
        # albo formularz w ogóle nie niesie macierzy `x-permissions` - to samo
        # zachowanie jak dawne form_schema()/form_ui_schema() wywołane osobno.
        schema = render_form_json(schema_entry, data)
        ui_schema = render_form_json(ui_entry, data) if ui_entry is not None else {}
      else:
        ui_obj = _try_parse_json(ui_entry) or {}
        mapping = (spec['form_files'].get(human_task.bpmn_task_id) or {})
        schema_file_key = form_key(str(mapping.get('schema') or human_task.bpmn_task_id or ''))
        # `version.content_hash` w kluczu: nowa (re)publikacja procesu dostaje
        # inny hash, więc stary wpis w Redis po prostu nigdy nie trafia (nie
        # trzeba go jawnie unieważniać).
        cache_key = (f"wf:task-form:{version.content_hash}:{schema_file_key}:"
                    f"{human_task.bpmn_task_id}")

        cached = cache_get_json(cache_key)
        if isinstance(cached, Mapping) and 'schema' in cached:
          filtered_schema, filtered_ui = cached['schema'], cached.get('ui_schema') or {}
        else:
          filtered_schema, filtered_ui = filter_form_for_step(schema_obj, ui_obj, human_task.name)
          cache_set_json(cache_key, {'schema': filtered_schema, 'ui_schema': filtered_ui})

        schema = render_form_json(filtered_schema, data)
        ui_schema = render_form_json(filtered_ui, data)

    # 2026-10-17: wybór ścieżki generowany z BPMN (wf/decisions.py).
    # Wstrzykujemy po filtrze `x-permissions` i po renderowaniu Jinja, żeby
    # pole nie zniknęło i żeby nie nadpisywać formularza biznesowego, który
    # ma już własne `chosen_path`. Działa też dla zadań bez formularza.
    decision = (spec.get('decisions') or {}).get(human_task.bpmn_task_id or '')
    if decision and decisions_enabled():
      if decision.get('blocked'):
        logger.warning('Decyzja %s pominięta: %s', human_task.bpmn_task_id,
                       decision.get('block_reason'))
      else:
        schema, ui_schema = inject_decision(schema, ui_schema, decision)
    return schema, ui_schema

  def start_form(self, version: ProcessModelVersion,
                 data: Mapping[str, Any] | None = None
                 ) -> tuple[str | None, dict[str, Any] | None, dict[str, Any] | None]:
    """Formularz startowy procesu (startEvent): (id elementu, JSON Schema, ui)."""
    spec = self.form_spec(version)
    start = spec.get('start_form')
    if not isinstance(start, Mapping):
      return None, None, None
    task_id = start.get('task_id') or start.get('bpmn_task_id')
    schema = _form_entry(spec['forms'], spec['form_files'], task_id, 'schema')
    if start.get('schema'):
      schema = spec['forms'].get(form_key(str(start['schema'])), schema)
    ui_schema = _form_entry(spec['uischemas'], spec['form_files'], task_id, 'ui')
    if start.get('ui'):
      ui_schema = spec['uischemas'].get(form_key(str(start['ui'])), ui_schema)
    # 2026-11-13: x-permissions jak w formularzu zadania - krok = nazwa
    # zdarzenia startowego (dawne zadanie "Start" przeniesione na startEvent).
    schema_obj = _try_parse_json(schema) if schema is not None else None
    if schema_obj is not None and has_x_permissions(schema_obj):
      step = start_event_name(_bpmn_files(version).values(), task_id) or task_id
      schema, ui_schema = filter_form_for_step(schema_obj, _try_parse_json(ui_schema) or {}, step)
    return (task_id,
            render_form_json(schema, data) if schema is not None else None,
            render_form_json(ui_schema, data) if ui_schema is not None else None)

  def validate_start_data(self, version: ProcessModelVersion,
                          data: Mapping[str, Any] | None) -> None:
    """2026-11-13: dane startu z formularza (POST /process-instances)
    względem formularza startowego - jak `_validate_form_data` zadania."""
    try:
      _task_id, schema, _ui = self.start_form(version, data=dict(data or {}))
    except FormRenderError as exc:
      raise InvalidTaskDataError(f"Formularz startowy nie renderuje się: {exc}") from exc
    if not schema:
      return
    import jsonschema
    try:
      jsonschema.validate(instance=dict(data or {}), schema=schema)
    except jsonschema.ValidationError as exc:
      raise InvalidTaskDataError(f"Dane startowe są nieprawidłowe: {exc.message}") from exc

  def decision_for(self, version: ProcessModelVersion,
                   bpmn_task_id: str | None) -> Mapping[str, Any] | None:
    """Decyzja gatewaya przypisana do zadania (dict z `spec_json['decisions']`)."""
    if not bpmn_task_id:
      return None
    return (self.form_spec(version).get('decisions') or {}).get(bpmn_task_id)

  def _prefill_decision(self, data: dict[str, Any],
                        human_task: HumanTask) -> dict[str, Any]:
    """Zaznacza domyślną opcję od razu w formularzu (bez klikania)."""
    if not decisions_enabled():
      return data
    decision = self.decision_for(self.task_version(human_task), human_task.bpmn_task_id)
    if not decision or decision.get('blocked'):
      return data
    field = str(decision.get('field') or DEFAULT_FIELD)
    value = default_value(decision)
    # 2026-10-20 (odtworzone testem na żywym silniku: wniosek-zakupowy.bpmn,
    # 4 kolejne po sobie decyzje Zatwierdź/Odrzuć): `data[field]` może już
    # istnieć z wartością `None` - nie dlatego, że formularz biznesowy
    # świadomie coś wybrał, ale bo `engine.complete()` zostawia
    # `chosen_path=None` na zadaniu BEZ wyboru (komentarz w wf/engine.py:
    # "zostaje dotychczasowe None"), a ta wartość propaguje się do
    # kolejnego zadania w tej samej gałęzi (ten sam field name dla
    # WSZYSTKICH decyzji procesu). `setdefault` by to potraktował jako
    # "formularz biznesowy już coś wybrał" i nie podstawił domyślnej opcji
    # TEGO gatewaya - stąd `None` liczy się tu jak "brak", nie jak "wybrano
    # None". Realny, NIE-None wybór formularza biznesowego wciąż wygrywa.
    if value is not None and data.get(field) is None:
      data[field] = value
    return data

  def task_form_data(self, workflow, human_task: HumanTask) -> dict[str, Any]:
    """Dane wstępne formularza.

    SpiffWorkflow trzyma dane w trzech miejscach: `workflow.data` (dane startowe
    i podsumowanie instancji), `task.data` ukończonego zadania (wynik jego
    formularza) oraz `task.data` bieżącego zadania. Ścieżka przodków bieżącego
    zadania to gałąź procesu, więc dane z niej (od korzenia do zadania)
    nakładamy na dane instancji. Gdy instancji nie da się odtworzyć, zostaje
    `submitted_data` albo podsumowanie instancji.

    2026-10-17: dokładamy domyślną opcję decyzji gatewaya, żeby w formularzu
    była zaznaczona od razu (`chosen_path = "__default__"`).
    """
    data: dict[str, Any] = dict(getattr(workflow, 'data', None) or {})
    try:
      spiff_task = self.engine.get_task(workflow, human_task.id)
    except KeyError:
      data = {**data, **dict(human_task.submitted_data
                             or human_task.process_instance.summary or {})}
      return self._prefill_decision(data, human_task)
    # 2026-11-13: zadanie podprocesu widzi tylko dane podprocesu - bez
    # danych rodzica, których podproces nie dostał (ioSpecification).
    data = dict(getattr(spiff_task.workflow, 'data', None) or {})
    chain: list[Any] = []
    node = spiff_task
    while node is not None:
      chain.append(node)
      node = node.parent
    for node in reversed(chain):
      data.update(node.data or {})
    return self._prefill_decision(data, human_task)

  # --- instancje --------------------------------------------------------

  def start_instance(self, db: Session, version: ProcessModelVersion, *,
                     title: str, business_key: str | None,
                     data: Mapping[str, Any] | None,
                     actor: CurrentUser,
                     parent: ProcessInstance | None = None,
                     parent_task_id=None) -> ProcessInstance:
    """Startuje instancję procesu i zapisuje jej stan w bazie.

    2026-11-13: `parent`/`parent_task_id` - instancja uruchomiona przez
    inną (sendTask `startProcess` albo szybka akcja zadania)."""
    process_spec = self.load_spec(version)
    try:
      workflow = self.engine.start(process_spec, dict(data or {}))
    except WorkflowTaskException as exc:
      # 2026-11-13: np. brak danych wejścia podprocesu (ioSpecification)
      raise ProcessServiceError(f"Nie udało się uruchomić procesu: {exc}") from exc

    instance = ProcessInstance(
      process_model_version_id=version.id,
      business_key=business_key,
      title=title,
      status=(ProcessInstanceStatus.RUNNING
              if self.engine.ready_user_tasks(workflow)
              else (ProcessInstanceStatus.COMPLETED
                    if self.engine.is_completed(workflow)
                    else ProcessInstanceStatus.WAITING)),
      summary=dict(data or {}),
      started_by_id=actor.id,
      parent_instance_id=parent.id if parent is not None else None,
      parent_task_id=parent_task_id,
    )
    db.add(instance)
    db.flush()  # potrzebne instance.id dla stanu, zadań i zdarzeń

    completed = self.engine.is_completed(workflow)
    if completed:
      instance.completed_at = datetime.now(UTC)

    self._save_state(db, instance, workflow)
    self._add_event(db, instance, EventType.INSTANCE_STARTED, actor=actor,
                    payload={'parentInstanceId': str(parent.id)} if parent is not None else None)
    self.sync_tasks(db, instance, workflow, process_spec, actor=actor)
    if completed:
      self._add_event(db, instance, EventType.INSTANCE_COMPLETED, actor=actor)
    db.commit()
    process_hooks.emit(db, process_hooks.ProcessHookEvent(
      process_hooks.INSTANCE_STARTED, instance, actor=actor, form_data=dict(data or {})))
    self.advance_system(db, instance, workflow, process_spec, actor)  # 2026-11-13
    return instance

  # BEGIN 2026-11-13: kroki systemowe (serviceTask) i budzenie instancji
  def advance_system(self, db: Session, instance: ProcessInstance, workflow,
                     process_spec: ProcessSpec, actor: CurrentUser, *,
                     now: datetime | None = None) -> None:
    """Wykonuje czekające `serviceTask` przez obsługę z service_tasks, dopóki
    proces posuwa się naprzód; potem uruchamia instancje potomne (sendTask).
    Krok, którego wynik nie jest gotowy, ustawia `next_wakeup_at`."""
    now = now or datetime.now(UTC)
    for _round in range(50):
      next_wakeup: datetime | None = None
      errors: list[str] = []
      progressed = False
      for task in self.engine.waiting_service_tasks(workflow):
        call = (process_spec.service_calls.get(self.engine.task_process_id(task)) or {}).get(
          self.engine.bpmn_task_id(task))
        if call is None:
          continue
        fn = service_tasks.get_handler(call.handler)
        if fn is None:
          errors.append(f"Krok '{call.name}': brak obsługi '{call.handler}' w hubie")
          continue
        ctx = service_tasks.ServiceContext(
          db=db, instance=instance, actor=actor, element_id=call.element_id, name=call.name,
          data=dict(task.data or {}), params=dict(call.params))
        try:
          result = fn(ctx)
        except Exception as exc:  # obsługa woła systemy zewnętrzne - każdy błąd do dziennika
          logger.exception('Instancja %s: krok %s (%s)', instance.id, call.element_id, call.handler)
          errors.append(f"Krok '{call.name}': {exc}")
          retry_at = now + ctx.retry_after()
          next_wakeup = min(next_wakeup or retry_at, retry_at)
          self._add_event(db, instance, EventType.SERVICE_TASK_FAILED, actor=actor, payload={
            'element': call.element_id, 'handler': call.handler, 'error': str(exc)})
          continue
        if result.completed:
          self.engine.complete_service_task(workflow, task, result.data)
          self._add_event(db, instance, EventType.SERVICE_TASK_COMPLETED, actor=actor, payload={
            'element': call.element_id, 'handler': call.handler})
          progressed = True
          break  # proces się zmienił - lista czekających kroków od nowa
        retry_at = now + (result.retry_after or service_tasks.DEFAULT_RETRY)
        next_wakeup = min(next_wakeup or retry_at, retry_at)
      if progressed:
        was_completed = instance.status == ProcessInstanceStatus.COMPLETED
        self._save_state(db, instance, workflow)
        self.refresh_status(db, instance, workflow)
        self.sync_tasks(db, instance, workflow, process_spec, actor=actor)
        if instance.status == ProcessInstanceStatus.COMPLETED and not was_completed:
          self._add_event(db, instance, EventType.INSTANCE_COMPLETED, actor=actor)
        db.commit()
        continue
      instance.next_wakeup_at = next_wakeup
      if errors:
        instance.error_message = '; '.join(errors)
      elif (instance.error_message or '').startswith("Krok '"):
        instance.error_message = None  # tylko komunikaty kroków systemowych
      db.commit()
      break
    self.start_pending_children(db, instance, workflow, process_spec, actor)

  def wake(self, db: Session, instance: ProcessInstance, actor: CurrentUser) -> ProcessInstance:
    """Ponownie wykonuje czekające kroki systemowe instancji (np. sprawdza
    status podpisu w Signius)."""
    if instance.status in (ProcessInstanceStatus.COMPLETED, ProcessInstanceStatus.CANCELLED,
                           ProcessInstanceStatus.SUSPENDED):
      return instance
    workflow, process_spec = self.load_workflow(instance)
    self.advance_system(db, instance, workflow, process_spec, actor)
    return instance

  def wake_due(self, db: Session, actor: CurrentUser,
               now: datetime | None = None) -> list[ProcessInstance]:
    """Budzi instancje z minionym `next_wakeup_at` (scripts/wf_wake.py)."""
    now = now or datetime.now(UTC)
    due = db.execute(select(ProcessInstance).where(
      ProcessInstance.next_wakeup_at.is_not(None), ProcessInstance.next_wakeup_at <= now,
      ProcessInstance.status.not_in((ProcessInstanceStatus.COMPLETED,
                                     ProcessInstanceStatus.CANCELLED,
                                     ProcessInstanceStatus.SUSPENDED)))).scalars().all()
    woken = []
    for instance in due:
      try:
        woken.append(self.wake(db, instance, actor))
      except ProcessServiceError as exc:
        db.rollback()
        logger.warning('Budzenie instancji %s: %s', instance.id, exc)
    return woken
  # -END

  # BEGIN 2026-11-13: instancje uruchamiane przez inne instancje
  def start_pending_children(self, db: Session, instance: ProcessInstance, workflow,
                             process_spec: ProcessSpec, actor: CurrentUser) -> list[ProcessInstance]:
    """Uruchamia instancje dla wykonanych `sendTask` z `startProcess`
    (jedna na wykonanie elementu - powtórne wywołanie nic nie robi).
    Błąd startu dziecka trafia do dziennika rodzica, nie cofa jego kroku."""
    if not process_spec.start_links:
      return []
    started: list[ProcessInstance] = []
    for task in self.engine.completed_tasks(workflow):
      link = (process_spec.start_links.get(self.engine.task_process_id(task)) or {}).get(
        self.engine.bpmn_task_id(task))
      if link is None or not link.automatic:
        continue
      exists = db.execute(select(ProcessInstance.id).where(
        ProcessInstance.parent_instance_id == instance.id,
        ProcessInstance.parent_task_id == task.id)).first()
      if exists:
        continue
      data = {**dict(getattr(task.workflow, 'data', None) or {}), **dict(task.data or {})}
      for key in link.process_keys:
        child = self._start_child(db, instance, link, key, data, task.id, actor)
        if child is not None:
          started.append(child)
    return started

  def quick_actions(self, db: Session, human_task: HumanTask,
                    process_spec: ProcessSpec) -> list[dict[str, str]]:
    """Szybkie akcje otwartego zadania (`quickAction` w BPMN): [{klucz, etykieta}]."""
    link = self._quick_link(human_task, process_spec)
    if link is None:
      return []
    names = dict(db.execute(select(ProcessModel.key, ProcessModel.name)
                            .where(ProcessModel.key.in_(link.process_keys))).all())
    return [{'process_model_key': key, 'label': names.get(key) or key} for key in link.process_keys]

  def run_quick_action(self, db: Session, human_task: HumanTask, process_model_key: str,
                       actor: CurrentUser) -> ProcessInstance:
    """Uruchamia instancję z szybkiej akcji zadania - z uprawnieniami do zadania."""
    instance = human_task.process_instance
    workflow, process_spec = self.load_workflow(instance)
    self.authorize(human_task, process_spec, actor)
    link = self._quick_link(human_task, process_spec)
    if link is None or process_model_key not in link.process_keys:
      raise ProcessServiceError(
        f"Zadanie '{human_task.name}' nie ma akcji uruchamiającej '{process_model_key}'")
    data = self.task_form_data(workflow, human_task)
    return self._start_child(db, instance, link, process_model_key, data, human_task.id,
                             actor, raise_errors=True)

  @staticmethod
  def _quick_link(human_task: HumanTask, process_spec: ProcessSpec):
    if human_task.status not in (HumanTaskStatus.OPEN, HumanTaskStatus.CLAIMED):
      return None
    process_id = human_task.bpmn_process_id or process_spec.process_id
    link = (process_spec.start_links.get(process_id) or {}).get(human_task.bpmn_task_id)
    return link if link is not None and not link.automatic else None

  def _start_child(self, db: Session, parent: ProcessInstance, link, process_model_key: str,
                   data: Mapping[str, Any], parent_task_id, actor: CurrentUser, *,
                   raise_errors: bool = False) -> ProcessInstance | None:
    try:
      version = self.published_version(db, process_model_key)
      child_data = _map_inputs(link.inputs, data)
      title = _child_title(link, version, parent, child_data)
      child = self.start_instance(db, version, title=title, business_key=parent.business_key,
                                  data=child_data, actor=actor, parent=parent,
                                  parent_task_id=parent_task_id)
    except (ProcessServiceError, FormRenderError) as exc:
      db.rollback()
      if raise_errors:
        raise
      logger.warning('Instancja %s: nie uruchomiono %s (%s): %s', parent.id,
                     process_model_key, link.element_id, exc)
      self._add_event(db, parent, EventType.CHILD_INSTANCE_FAILED, actor=actor, payload={
        'processModelKey': process_model_key, 'element': link.element_id, 'error': str(exc)})
      db.commit()
      return None
    self._add_event(db, parent, EventType.CHILD_INSTANCE_STARTED, actor=actor, payload={
      'childInstanceId': str(child.id), 'processModelKey': process_model_key,
      'element': link.element_id})
    db.commit()
    return child
  # -END

  def load_workflow(self, instance: ProcessInstance) -> tuple[Any, ProcessSpec]:
    """Odtwarza instancję Spiffa zapisaną w ProcessInstanceState."""
    state = instance.state
    if state is None:
      raise ProcessServiceError(
        f"Instancja {instance.id} nie ma zapisanego stanu silnika - "
        f"proces nie został uruchomiony lub stan został usunięty")
    if state.serializer_version != SERIALIZER_VERSION:
      raise ProcessServiceError(
        f"Stan instancji {instance.id} zapisano w formacie "
        f"'{state.serializer_version}', a serwis używa '{SERIALIZER_VERSION}' - "
        f"instancję trzeba uruchomić ponownie")
    version = instance.version
    workflow = self.engine.load_state(state.state)
    return workflow, self.load_spec(version)

  def _save_state(self, db: Session, instance: ProcessInstance, workflow) -> None:
    """Zapisuje stan silnika (ProcessInstanceState)."""
    state = instance.state
    if state is None:
      state = ProcessInstanceState(process_instance_id=instance.id,
                                   serializer_version=SERIALIZER_VERSION,
                                   state={})
      db.add(state)
      instance.state = state
    state.serializer_version = SERIALIZER_VERSION
    state.state = self.engine.dump_state(workflow)
    state.updated_at = datetime.now(UTC)

  def refresh_status(self, db: Session, instance: ProcessInstance, workflow) -> None:
    """Ustawia status instancji zgodnie ze stanem silnika."""
    if instance.status in (ProcessInstanceStatus.CANCELLED, ProcessInstanceStatus.SUSPENDED):
      return  # decyzja użytkownika - silnik jej nie nadpisuje
    if self.engine.is_completed(workflow):
      instance.status = ProcessInstanceStatus.COMPLETED
      instance.completed_at = instance.completed_at or datetime.now(UTC)
    elif self.engine.ready_user_tasks(workflow):
      instance.status = ProcessInstanceStatus.RUNNING
    else:
      instance.status = ProcessInstanceStatus.WAITING

  # --- zadania ----------------------------------------------------------

  def sync_tasks(self, db: Session, instance: ProcessInstance, workflow, process_spec: ProcessSpec,
                 *, actor: CurrentUser | None = None) -> list[HumanTask]:
    """Synchronizuje tabelę `human_task` z zadaniami gotowymi w Spiffie.

    - nowe zadanie -> wpis z OPEN i kandydatami z BPMN,
    - zadanie, które przestało być gotowe (a nie zostało wykonane) -> CANCELLED,
    - wpisy już wykonane pozostają bez zmian.
    """
    engine = self.engine
    ready = engine.ready_user_tasks(workflow)
    ready_by_id = {str(task.id): task for task in ready}

    for task_id, spiff_task in ready_by_id.items():
      human_task = db.get(HumanTask, _as_uuid(task_id))
      if human_task is None:
        # 2026-11-13: zadanie podprocesu z innego modelu pamięta swój
        # proces i wersję modelu (formularz, x-permissions, decyzje).
        process_id = engine.task_process_id(spiff_task)
        sub_version = process_spec.called_versions.get(process_id)
        human_task = HumanTask(
          id=_as_uuid(task_id),
          process_instance_id=instance.id,
          bpmn_task_id=engine.bpmn_task_id(spiff_task),
          name=engine.task_name(spiff_task),
          lane=engine.task_lane(spiff_task),
          status=HumanTaskStatus.OPEN,
          bpmn_process_id=process_id if process_id != process_spec.process_id else None,
          form_version_id=_as_uuid(sub_version) if sub_version else None,
        )
        db.add(human_task)
        self._add_event(db, instance, EventType.TASK_CREATED, actor=actor,
                        human_task_id=human_task.id,
                        payload={'bpmnTaskId': engine.bpmn_task_id(spiff_task)})
      # 2026-11-06: przypisania `${zmienna}` rozwiązywane danymi
      # instancji (workflow.data + dane zadania, jak w task_form_data).
      # 2026-11-13: dane podprocesu, nie całej instancji (ioSpecification).
      task_data = {**dict(getattr(spiff_task.workflow, 'data', None) or {}),
                   **dict(spiff_task.data or {})}
      assignment = assignment_for(process_spec, human_task.bpmn_task_id,
                                  human_task.bpmn_process_id).resolve(task_data)
      self._sync_candidates(db, human_task, assignment)

    # zadania, ktore wylaczono ze Spiffa (np. gałąź skasowana)
    for human_task in instance.tasks:
      if human_task.status in (HumanTaskStatus.OPEN, HumanTaskStatus.CLAIMED) \
              and str(human_task.id) not in ready_by_id:
        human_task.status = HumanTaskStatus.CANCELLED
        self._add_event(db, instance, EventType.TASK_CANCELLED, actor=actor,
                        human_task_id=human_task.id)
    db.flush()
    return [db.get(HumanTask, _as_uuid(task_id)) for task_id in ready_by_id]

  def _sync_candidates(self, db: Session, human_task: HumanTask, assignment) -> None:
    """Kandydaci zadania: `assignee`/`candidateUsers`/`candidateGroups` z BPMN."""
    wanted: set[tuple[PrincipalType, str]] = set()
    if assignment.assignee:
      wanted.add((PrincipalType.USER, assignment.assignee))
    for username in assignment.candidate_users:
      wanted.add((PrincipalType.USER, username))
    for group in assignment.candidate_groups:
      wanted.add((PrincipalType.GROUP, group))

    existing = {(c.principal_type, c.principal_value) for c in human_task.candidates}
    for missing in wanted - existing:
      human_task.candidates.append(HumanTaskCandidate(
        principal_type=missing[0], principal_value=missing[1]))
    for stale in existing - wanted:
      db.query(HumanTaskCandidate).filter(
        HumanTaskCandidate.human_task_id == human_task.id,
        HumanTaskCandidate.principal_type == stale[0],
        HumanTaskCandidate.principal_value == stale[1],
      ).delete()

  def authorize(self, human_task: HumanTask, process_spec: ProcessSpec, actor: CurrentUser) -> None:
    """Sprawdza uprawnienia do zadania (przypisanie z BPMN + role Keycloak)."""
    assignment = assignment_for(process_spec, human_task.bpmn_task_id, human_task.bpmn_process_id)
    if assignment.is_dynamic:
      # 2026-11-06: wartości zmiennych zna tylko instancja - używamy
      # kandydatów zapisanych przy sync_tasks.
      assignment = self._stored_assignment(human_task)
    groups = frozenset(actor.groups) | frozenset(actor.roles)
    if not assignment.allows(actor.username, groups):
      raise NotAuthorizedError(
        f"Zadanie '{human_task.name}' nie jest przypisane do użytkownika "
        f"'{actor.username}' ani do jego grup")
    if assignment.assignee and human_task.claimed_by_id not in (None, actor.id) \
            and assignment.assignee == actor.username:
      raise NotAuthorizedError(
        f"Zadanie '{human_task.name}' jest już podjęte przez innego użytkownika")

  @staticmethod
  def _stored_assignment(human_task: HumanTask) -> TaskAssignment:
    """Przypisanie z kandydatów zadania (HumanTaskCandidate)."""
    users = tuple(c.principal_value for c in human_task.candidates
                  if c.principal_type == PrincipalType.USER)
    groups = tuple(c.principal_value for c in human_task.candidates
                   if c.principal_type == PrincipalType.GROUP)
    return TaskAssignment(candidate_users=users,
                          candidate_groups=groups or (() if users else (UNRESOLVED_PRINCIPAL,)))

  def claim_task(self, db: Session, human_task: HumanTask, actor: CurrentUser,
                 process_spec: ProcessSpec) -> HumanTask:
    """Podjęcie zadania (claim) przez zalogowanego użytkownika."""
    self.authorize(human_task, process_spec, actor)
    if human_task.status != HumanTaskStatus.OPEN:
      raise ProcessServiceError(f"Zadanie nie jest otwarte (status={human_task.status})")
    human_task.status = HumanTaskStatus.CLAIMED
    human_task.claimed_by_id = actor.id
    human_task.claimed_at = datetime.now(UTC)
    self._add_event(db, human_task.process_instance, EventType.TASK_CLAIMED,
                    actor=actor, human_task_id=human_task.id)
    db.commit()
    return human_task

  def complete_task(self, db: Session, human_task: HumanTask, actor: CurrentUser,
                    form_data: Mapping[str, Any]) -> ProcessInstance:
    """Wykonuje zadanie: autoryzacja -> walidacja -> Spiff -> zapis stanu."""
    instance = human_task.process_instance
    workflow, process_spec = self.load_workflow(instance)
    self.authorize(human_task, process_spec, actor)

    if human_task.status == HumanTaskStatus.CLAIMED and human_task.claimed_by_id not in (None, actor.id):
      raise ProcessServiceError("Zadanie jest podjęte przez innego użytkownika")
    if human_task.status == HumanTaskStatus.COMPLETED:
      raise ProcessServiceError("Zadanie zostało już wykonane")

    render_data = {**self.task_form_data(workflow, human_task),
                   **dict(form_data or {})}
    self._validate_form_data(human_task, self.task_version(human_task), form_data, render_data)

    # 2026-11-06: zadanie bez własnej decyzji czyści `chosen_path`.
    # Inaczej wybór z poprzedniego okrążenia pętli (np. edu-pismo:
    # "Do uzupełnienia" -> uzupełnienie -> ponowne "Rozpatrzenie") zostaje w
    # danych i _prefill_decision zaznacza go z góry przy następnej decyzji.
    # Decyzja jest zawsze wstrzykiwana do zadania tuż przed gatewayem
    # (wf/decisions.py), więc zadanie bez decyzji nie przekazuje wyboru dalej.
    if (decisions_enabled() and DEFAULT_FIELD not in (form_data or {})
            and self.decision_for(self.task_version(human_task), human_task.bpmn_task_id) is None):
      form_data = {**dict(form_data or {}), DEFAULT_FIELD: None}

    human_task.status = HumanTaskStatus.COMPLETED
    human_task.completed_by_id = actor.id
    human_task.completed_at = datetime.now(UTC)
    human_task.submitted_data = dict(form_data or {})

    try:
      self.engine.complete(workflow, human_task.id, form_data)
    except WorkflowTaskException as exc:
      # 2026-10-17: błąd ewaluacji warunku (np. brak zmiennej guardu) ma
      # być 422 z komunikatem, a nie 500. Guardy w definicjach są usunięte,
      # ale zostawiamy to jako bezpiecznik dla przyszłych modeli.
      raise InvalidTaskDataError(
        f"Nie udało się wykonać zadania '{human_task.name}': {exc}") from exc
    self._save_state(db, instance, workflow)
    self.refresh_status(db, instance, workflow)
    self._add_event(db, instance, EventType.TASK_COMPLETED, actor=actor,
                    human_task_id=human_task.id, payload=dict(form_data or {}))
    self.sync_tasks(db, instance, workflow, process_spec, actor=actor)
    if instance.status == ProcessInstanceStatus.COMPLETED:
      self._add_event(db, instance, EventType.INSTANCE_COMPLETED, actor=actor)
    db.commit()
    process_hooks.emit(db, process_hooks.ProcessHookEvent(
      process_hooks.TASK_COMPLETED, instance, actor=actor, human_task=human_task,
      form_data=dict(form_data or {})))
    self.advance_system(db, instance, workflow, process_spec, actor)  # 2026-11-13
    return instance

  def _validate_form_data(self, human_task: HumanTask, version: ProcessModelVersion,
                          form_data: Mapping[str, Any],
                          render_data: Mapping[str, Any] | None = None) -> None:
    """Waliduje dane formularza względem JSON Schema (po renderowaniu Jinja).

    2026-10-15: `task_form()` (ten sam, który serwuje `GET
    /tasks/{id}/form`) - nie `form_schema()` wprost - żeby walidacja
    zawsze odpowiadała temu, co użytkownik faktycznie widział (schemat
    przefiltrowany wg `x-permissions` dla tego kroku: pola "none" nie są
    wymagane, bo ich w ogóle nie było na formularzu). Zob. docstring
    wf/routers/human_task_router.py::get_task_form - "ten sam schemat,
    który później waliduje complete".
    """
    try:
      schema, _ui_schema = self.task_form(
        version, human_task, render_data if render_data is not None else form_data)
    except FormRenderError as exc:
      # Błędny szablon/schemat to problem danych zadania, nie błąd serwera.
      raise InvalidTaskDataError(
        f"Formularz zadania '{human_task.name}' nie renderuje się: {exc}") from exc
    if not schema:
      return
    try:
      import jsonschema
    except ImportError:  # pragma: no cover - jsonschema jest zależnością
      logger.warning('jsonschema nie jest zainstalowane - pomijam walidację formularza')
      return
    try:
      jsonschema.validate(instance=dict(form_data or {}), schema=schema)
    except jsonschema.ValidationError as exc:
      raise InvalidTaskDataError(f"Dane zadania '{human_task.name}' są nieprawidłowe: {exc.message}") from exc

  # --- operacje na instancji -------------------------------------------

  def cancel(self, db: Session, instance: ProcessInstance, actor: CurrentUser,
             reason: str | None = None) -> ProcessInstance:
    """Anuluje instancję i jej aktywne zadania."""
    instance.status = ProcessInstanceStatus.CANCELLED
    instance.error_message = reason
    for human_task in instance.tasks:
      if human_task.status in (HumanTaskStatus.OPEN, HumanTaskStatus.CLAIMED):
        human_task.status = HumanTaskStatus.CANCELLED
        self._add_event(db, instance, EventType.TASK_CANCELLED, actor=actor,
                        human_task_id=human_task.id)
    self._add_event(db, instance, EventType.INSTANCE_CANCELLED, actor=actor,
                    payload={'reason': reason} if reason else {})
    db.commit()
    return instance

  def set_suspended(self, db: Session, instance: ProcessInstance, actor: CurrentUser,
                    reason: str | None = None) -> ProcessInstance:
    """Wstrzymuje instancję; stan silnika zostaje bez zmian (wznowienie wraca
    do stanu sprzed wstrzymania)."""
    instance.status = ProcessInstanceStatus.SUSPENDED
    if reason:
      instance.error_message = reason
    self._add_event(db, instance, EventType.INSTANCE_SUSPENDED, actor=actor,
                    payload={'reason': reason} if reason else {})
    db.commit()
    return instance

  def resume(self, db: Session, instance: ProcessInstance, actor: CurrentUser) -> ProcessInstance:
    """Wznawia wstrzymaną instancję i synchronizuje zadania."""
    workflow, process_spec = self.load_workflow(instance)
    instance.status = ProcessInstanceStatus.RUNNING
    self._add_event(db, instance, EventType.INSTANCE_RESUMED, actor=actor)
    self.sync_tasks(db, instance, workflow, process_spec, actor=actor)
    self.refresh_status(db, instance, workflow)
    db.commit()
    return instance

  # --- dziennik zdarzeń -------------------------------------------------

  def record_event(self, db: Session, instance: ProcessInstance, event_type: EventType, *,
                   actor: CurrentUser | None = None,
                   payload: Mapping[str, Any] | None = None) -> ProcessEvent:
    """Jak `_add_event`, ale publiczne i z własnym commitem - do użycia przez
    integracje, które dopisują zdarzenie do instancji bez przechodzenia przez
    pełny cykl silnika Spiff (np. wf/routers/signius_api.py: żądanie/odebranie
    podpisu nie jest krokiem BPMN w tym silniku - zob. wf/engine.py, brak
    obsługi serviceTask/scriptTask)."""
    event = self._add_event(db, instance, event_type, actor=actor, payload=payload)
    db.commit()
    return event

  def _add_event(self, db: Session, instance: ProcessInstance, event_type: EventType, *,
                 actor: CurrentUser | None = None, human_task_id=None,
                 payload: Mapping[str, Any] | None = None) -> ProcessEvent:
    """Dopisuje zdarzenie do dziennika instancji (ProcessEvent)."""
    event = ProcessEvent(
      process_instance_id=instance.id,
      human_task_id=human_task_id,
      event_type=str(event_type),
      actor_id=actor.id if actor else None,
      payload=dict(payload or {}),
    )
    db.add(event)
    return event


# BEGIN 2026-11-13
def _get_path(data: Mapping[str, Any], path: str) -> tuple[bool, Any]:
  """Wartość `a.b.c` z zagnieżdżonych danych formularza (klucz z kropką
  wprost ma pierwszeństwo)."""
  if path in data:
    return True, data[path]
  node: Any = data
  for part in path.split('.'):
    if not isinstance(node, Mapping) or part not in node:
      return False, None
    node = node[part]
  return True, node


def _map_inputs(inputs, data: Mapping[str, Any]) -> dict[str, Any]:
  """Tylko jawnie wskazane dane (`startInput`) - bez deklaracji dziecko
  nie dostaje danych rodzica (RODO)."""
  result: dict[str, Any] = {}
  for source, target in inputs:
    found, value = _get_path(data, source)
    if not found:
      continue
    # cel z kropką = pole w sekcji formularza dziecka ("Kogo dotyczy.Wydział")
    *sections, field = target.split('.')
    node = result
    for section in sections:
      node = node.setdefault(section, {})
      if not isinstance(node, dict):
        break
    else:
      node[field] = value
  return result


def _child_title(link, version: ProcessModelVersion, parent: ProcessInstance,
                 data: Mapping[str, Any]) -> str:
  if link.title:
    try:
      return _jinja_env().from_string(link.title).render(
        **data, data=data, parent_title=parent.title).strip() or parent.title
    except TemplateError as exc:
      raise FormRenderError(f"Tytuł instancji (startTitle) nie renderuje się: {exc}") from exc
  return f"{version.process_model.name}: {parent.title}"
# -END


def _bpmn_files(version: ProcessModelVersion) -> dict[str, str]:
  """2026-11-13: {nazwa: treść} plików BPMN wersji modelu."""
  return {file.file_name: file.content for file in version.files
          if file.file_type == ProcessFileType.BPMN}


def _as_uuid(value: str):
  import uuid as _uuid
  return value if isinstance(value, _uuid.UUID) else _uuid.UUID(str(value))


_service: ProcessService | None = None


def get_service() -> ProcessService:
  """Współdzielona instancja serwisu (silnik Spiff tworzymy raz)."""
  global _service
  if _service is None:
    _service = ProcessService()
  return _service
