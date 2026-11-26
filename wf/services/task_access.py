# wf/services/task_access.py
# 2026-11-25: kto może wykonać zadanie BPMN bez przypisania (bez assignee,
# candidateUsers i candidateGroups).
"""
Zadania bez przypisania - jedna reguła dla `ProcessService.authorize`
(podjęcie, wykonanie, szybkie akcje, formularz) i dla skrzynki zadań
(`GET /v1/wf/tasks/my`, warunek SQL).

Dawniej zadanie bez przypisania mógł wykonać każdy zalogowany użytkownik,
także spoza organizacji (student, kandydat). Teraz (`[wf] unassigned_tasks`
= `restricted`, domyślnie) może je wykonać:

  * administrator procesów (`[keycloak] admin_groups`),
  * inicjator sprawy - także osoba, która go zastępuje (`user_substitution`),
  * w repozytorium aktów (`dokumenty`) - redaktor aktów (`can_create_act`),
    czyli ta sama grupa, która może uruchomić obieg.

`unassigned_tasks=open` przywraca dawne zachowanie. To ustawienie przejściowe
dla wdrożeń, które nie przejrzały jeszcze modeli. Docelowo zadanie dla
konkretnej grupy powinno mieć w BPMN `candidateGroups`.
"""
from __future__ import annotations

from sqlalchemy import or_, true
from sqlalchemy.sql.elements import ColumnElement

from config.config import section
from wf.dependencies import ACT_REPOSITORY_MODEL_KEY, can_create_act, is_process_admin
from wf.models import HumanTask, ProcessInstance, ProcessModel, ProcessModelVersion
from wf.schemas.wf_schemas import CurrentUser

OPEN = 'open'
RESTRICTED = 'restricted'


def unassigned_mode() -> str:
  """`[wf] unassigned_tasks` (zmienna WF_UNASSIGNED_TASKS): `restricted` albo `open`."""
  value = (section('wf').get('unassigned_tasks') or RESTRICTED).strip().lower()
  return OPEN if value == OPEN else RESTRICTED


def _model_key(instance: ProcessInstance) -> str | None:
  version = instance.version
  model = version.process_model if version is not None else None
  return model.key if model is not None else None


def may_act_on_unassigned(human_task: HumanTask, actor: CurrentUser) -> bool:
  """Czy `actor` może wykonać zadanie bez przypisania w BPMN."""
  if unassigned_mode() == OPEN or is_process_admin(actor):
    return True
  instance = human_task.process_instance
  if instance.started_by_id is not None and instance.started_by_id in actor.acting_user_ids:
    return True
  return _model_key(instance) == ACT_REPOSITORY_MODEL_KEY and can_create_act(actor)


def unassigned_visible_clause(actor: CurrentUser) -> ColumnElement[bool]:
  """Warunek SQL na `HumanTask` odpowiadający `may_act_on_unassigned`."""
  if unassigned_mode() == OPEN or is_process_admin(actor):
    return true()
  conditions = [HumanTask.process_instance.has(
    ProcessInstance.started_by_id.in_(actor.acting_user_ids or (0,)))]
  if can_create_act(actor):
    conditions.append(HumanTask.process_instance.has(ProcessInstance.version.has(
      ProcessModelVersion.process_model.has(ProcessModel.key == ACT_REPOSITORY_MODEL_KEY))))
  return or_(*conditions)


__all__ = ['OPEN', 'RESTRICTED', 'may_act_on_unassigned', 'unassigned_mode',
           'unassigned_visible_clause']
