#!/usr/bin/env python3
# coding: utf-8
"""
Czyści dane procesów z bazy (dane testowe).

Domyślnie pokazuje tylko, co zostałoby usunięte (dry-run). Aby wykonać:

    python3 scripts/clear_process_data.py --execute --yes

Zakres:

  --scope all      (domyślnie) wszystkie modele/wersje/instancje procesów,
  --scope legacy   tylko modele, których BPMN zawiera stare warunki
                    `chosen_path == <liczba>` (czyli definicje sprzed
                    normalizacji na `Flow_*`).

Dokumenty:

  Domyślnie usuwa też dokumenty i ich wersje (dane testowe) - wpisy w bazie,
  nie pliki w Paperless-ngx. `--keep-documents` zostawia rejestr dokumentów,
  a jedynie odpina je od usuwanych zadań
  (`document_version.human_task_id = NULL`). W zakresie `legacy` dokumenty
  są zawsze zachowywane (usuwane są tylko powiązania z instancjami).

Dodatkowo:

  --reset-sequences        czyści liczniki numerów dokumentów,
  --include-notifications  usuwa wszystkie powiadomienia.

Skrypt usuwa dane w kolejności zgodnej z kluczami obcymi (dzieci przed
rodzicami), w jednej transakcji. Nie rusza użytkowników, jednostek
organizacyjnych ani globalnych zastępstw (te bez `process_model_id`).

UWAGA: to operacja nieodwracalna. Zrób kopię bazy przed `--execute`.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import create_engine, delete, func, select, update

OLD_CHOSEN_PATH_RE = re.compile(r'chosen_path\s*==\s*\d+')

# Skrypt leży w scripts/, a importuje pakiety z korzenia repo (db, wf, ...).
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
  sys.path.insert(0, _ROOT)


@dataclass
class Scope:
  name: str
  model_ids: list[Any] | None = None      # None = wszystkie
  version_ids: list[Any] | None = None    # None = wszystkie
  instance_ids: list[Any] | None = None   # None = wszystkie
  task_ids: list[Any] | None = None       # None = wszystkie
  legacy_models: list[Any] = field(default_factory=list)


@dataclass
class Step:
  label: str
  model: Any
  where: Any = None
  action: str = 'delete'                  # delete | update
  values: dict[str, Any] | None = None
  note: str = ''


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
  parser = argparse.ArgumentParser(
    description='Usuwa dane procesów z bazy (domyślnie dry-run).',
    formatter_class=argparse.RawDescriptionHelpFormatter,
    epilog=__doc__,
  )
  parser.add_argument('--scope', choices=('all', 'legacy'), default='all',
                      help='all = wszystko; legacy = tylko modele ze starymi chosen_path')
  parser.add_argument('--execute', action='store_true',
                      help='naprawdę usuń dane (bez tego tylko raport)')
  parser.add_argument('--yes', action='store_true',
                      help='potwierdzenie dla --execute')
  parser.add_argument('--dry-run', action='store_true',
                      help='wymuś tryb raportu (domyślne)')
  parser.add_argument('--keep-documents', action='store_true',
                      help='zachowaj dokumenty, tylko odpisz je od zadań')
  parser.add_argument('--reset-sequences', action='store_true',
                      help='wyczyść document_number_sequence')
  parser.add_argument('--include-notifications', action='store_true',
                      help='usuń też wszystkie powiadomienia')
  parser.add_argument('--database-url', default=None,
                      help='wskaż bazę zamiast config.ini/DATABASE_URL '
                           '(np. sqlite:////tmp/test.db)')
  return parser.parse_args(argv)


def _where(column: Any, ids: list[Any] | None) -> Any:
  """None = bez warunku (wszystko); lista = IN (lista)."""
  if ids is None:
    return None
  return column.in_(ids)


def load_scope(session: Any, scope_name: str, models: dict[str, Any]) -> Scope:
  if scope_name == 'all':
    return Scope(name='all', model_ids=None, version_ids=None,
                 instance_ids=None, task_ids=None)

  ProcessModel = models['ProcessModel']
  ProcessModelVersion = models['ProcessModelVersion']
  ProcessInstance = models['ProcessInstance']
  HumanTask = models['HumanTask']
  ProcessFileType = models['ProcessFileType']

  legacy_models = []
  for model in session.scalars(select(ProcessModel)).all():
    for version in model.versions:
      for model_file in version.files:
        if (model_file.file_type == ProcessFileType.BPMN
            and OLD_CHOSEN_PATH_RE.search(model_file.content or '')):
          legacy_models.append(model)
          break
      else:
        continue
      break

  model_ids = [model.id for model in legacy_models]
  version_ids: list[Any] = []
  instance_ids: list[Any] = []
  task_ids: list[Any] = []
  if model_ids:
    version_ids = list(session.scalars(
      select(ProcessModelVersion.id).where(
        ProcessModelVersion.process_model_id.in_(model_ids))).all())
    if version_ids:
      instance_ids = list(session.scalars(
        select(ProcessInstance.id).where(
          ProcessInstance.process_model_version_id.in_(version_ids))).all())
      if instance_ids:
        task_ids = list(session.scalars(
          select(HumanTask.id).where(
            HumanTask.process_instance_id.in_(instance_ids))).all())

  return Scope(name='legacy', model_ids=model_ids, version_ids=version_ids,
               instance_ids=instance_ids, task_ids=task_ids,
               legacy_models=legacy_models)


def build_plan(session: Any, scope: Scope, args: argparse.Namespace,
               models: dict[str, Any]) -> list[Step]:
  Comment = models['Comment']
  Document = models['Document']
  DocumentNumberSequence = models['DocumentNumberSequence']
  DocumentVersion = models['DocumentVersion']
  EngineJob = models['EngineJob']
  HumanTask = models['HumanTask']
  HumanTaskCandidate = models['HumanTaskCandidate']
  Notification = models['Notification']
  ProcessEvent = models['ProcessEvent']
  ProcessInstance = models['ProcessInstance']
  ProcessInstanceDocument = models['ProcessInstanceDocument']
  ProcessInstanceState = models['ProcessInstanceState']
  ProcessModel = models['ProcessModel']
  ProcessModelFile = models['ProcessModelFile']
  ProcessModelVersion = models['ProcessModelVersion']
  UserSubstitution = models['UserSubstitution']

  plan: list[Step] = []

  def add(label: str, model: Any, where: Any = None,
          action: str = 'delete', values: dict[str, Any] | None = None,
          note: str = '') -> None:
    plan.append(Step(label=label, model=model, where=where, action=action,
                     values=values, note=note))

  # 1. Komentarze (FK do instancji, zadania i dokumentu - bez kaskady).
  add('comment', Comment, _where(Comment.process_instance_id, scope.instance_ids))

  # 2. Wersje dokumentów. Przy zachowaniu dokumentów odpinamy tylko zadania;
  #    w pełnym czyszczeniu usuwamy wszystkie wersje.
  keep_documents = args.keep_documents or scope.name == 'legacy'
  if keep_documents:
    if scope.name == 'all':
      add('document_version (odpięcie zadania)', DocumentVersion,
          DocumentVersion.human_task_id.isnot(None), 'update',
          {'human_task_id': None})
    elif scope.task_ids:
      add('document_version (odpięcie zadania)', DocumentVersion,
          DocumentVersion.human_task_id.in_(scope.task_ids), 'update',
          {'human_task_id': None})
  else:
    add('document_version', DocumentVersion)

  # 3. Powiązania instancja <-> dokument (FK do dokumentu bez kaskady).
  add('process_instance_document', ProcessInstanceDocument,
      _where(ProcessInstanceDocument.process_instance_id, scope.instance_ids))

  # 4. Kandydaci zadań (FK do human_task z kaskadą, ale usuwamy jawnie).
  add('human_task_candidate', HumanTaskCandidate,
      _where(HumanTaskCandidate.human_task_id, scope.task_ids))

  # 5-9. Dzieci instancji; ProcessEvent nie ma ON DELETE CASCADE.
  add('human_task', HumanTask, _where(HumanTask.process_instance_id, scope.instance_ids))
  add('process_event', ProcessEvent, _where(ProcessEvent.process_instance_id, scope.instance_ids))
  add('engine_job', EngineJob, _where(EngineJob.process_instance_id, scope.instance_ids))
  add('process_instance_state', ProcessInstanceState,
      _where(ProcessInstanceState.process_instance_id, scope.instance_ids))
  add('process_instance', ProcessInstance, _where(ProcessInstance.id, scope.instance_ids))

  # 10-13. Definicje: pliki -> wersje -> modele; zastępstwa przed modelem.
  add('process_model_file', ProcessModelFile,
      _where(ProcessModelFile.version_id, scope.version_ids))
  if scope.name == 'all':
    add('user_substitution (powiązane z modelem)', UserSubstitution,
        UserSubstitution.process_model_id.isnot(None))
  elif scope.model_ids:
    add('user_substitution (powiązane z modelem)', UserSubstitution,
        UserSubstitution.process_model_id.in_(scope.model_ids))
  add('process_model_version', ProcessModelVersion,
      _where(ProcessModelVersion.id, scope.version_ids))
  add('process_model', ProcessModel, _where(ProcessModel.id, scope.model_ids))

  # 14. Dokumenty - tylko w pełnym czyszczeniu i bez --keep-documents.
  if not keep_documents:
    add('document', Document)

  if args.include_notifications:
    add('notification', Notification)
  if args.reset_sequences:
    add('document_number_sequence', DocumentNumberSequence)
  return plan


def _count(session: Any, model: Any, where: Any) -> int:
  stmt = select(func.count()).select_from(model)
  if where is not None:
    stmt = stmt.where(where)
  return int(session.scalar(stmt) or 0)


def main(argv: list[str] | None = None) -> int:
  args = parse_args(argv)
  if args.execute and args.dry_run:
    print('Błąd: --execute i --dry-run wykluczają się.', file=sys.stderr)
    return 2
  if args.execute and not args.yes:
    print('Błąd: --execute wymaga --yes (operacja nieodwracalna).',
          file=sys.stderr)
    return 2

  # Importy aplikacji. Przy --database-url tworzymy własny engine, żeby
  # nadpisać także ewentualny `url` z config.ini (nie tylko DATABASE_URL).
  from sqlalchemy.orm import sessionmaker
  if args.database_url:
    connect_args = ({'check_same_thread': False}
                    if args.database_url.startswith('sqlite') else {})
    engine = create_engine(args.database_url, connect_args=connect_args)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
  else:
    from db.session import SessionLocal, engine
  from wf.models import (
    Comment,
    Document,
    DocumentNumberSequence,
    DocumentVersion,
    EngineJob,
    HumanTask,
    HumanTaskCandidate,
    Notification,
    ProcessEvent,
    ProcessInstance,
    ProcessInstanceDocument,
    ProcessInstanceState,
    ProcessModel,
    ProcessModelFile,
    ProcessModelVersion,
    UserSubstitution,
  )
  from wf.schemas.wf_schemas import ProcessFileType

  model_map = {cls.__name__: cls for cls in (
    Comment, Document, DocumentNumberSequence, DocumentVersion, EngineJob,
    HumanTask, HumanTaskCandidate, Notification, ProcessEvent, ProcessInstance,
    ProcessInstanceDocument, ProcessInstanceState, ProcessModel,
    ProcessModelFile, ProcessModelVersion, UserSubstitution,
  )}
  model_map['ProcessFileType'] = ProcessFileType

  print(f'Baza: {engine.url.render_as_string(hide_password=True)}')
  print(f'Zakres: {args.scope}')
  print(f'Tryb: {"EXECUTE" if args.execute else "DRY-RUN"}')
  if args.keep_documents:
    print('Dokumenty: zachowane (tylko odpięcie od zadań)')
  elif args.scope == 'legacy':
    print('Dokumenty: zachowane (zakres legacy)')
  else:
    print('Dokumenty: usuwane')

  session = SessionLocal()
  try:
    scope = load_scope(session, args.scope, model_map)
    if args.scope == 'legacy':
      print(f'Modele ze starymi warunkami: {len(scope.legacy_models)}')
      for model in scope.legacy_models[:20]:
        print(f'  - {model.key}: {model.name} ({len(model.versions)} wersji)')
      if len(scope.legacy_models) > 20:
        print(f'  ... i {len(scope.legacy_models) - 20} więcej')
      if not scope.legacy_models:
        print('Brak modeli do usunięcia.')
        return 0

    plan = build_plan(session, scope, args, model_map)
    counts = [(step, _count(session, step.model, step.where)) for step in plan]
    total = sum(count for _step, count in counts)
    print('\nTabela / operacja                              Liczba')
    print('-' * 62)
    for step, count in counts:
      action = 'DELETE' if step.action == 'delete' else 'UPDATE'
      label = step.label
      print(f'{label:<44} {action:>6} {count:>7}')
    print('-' * 62)
    print(f'Razem rekordów do zmiany: {total}')

    if not args.execute:
      print('\nTo był dry-run. Aby wykonać: --execute --yes')
      return 0
    if total == 0:
      print('\nNic do usunięcia.')
      return 0

    print('\nWykonuję...')
    for step, count in counts:
      if step.action == 'delete':
        stmt = delete(step.model)
        if step.where is not None:
          stmt = stmt.where(step.where)
      else:
        stmt = update(step.model).where(step.where).values(**(step.values or {}))
      session.execute(stmt)
      print(f'  {step.label}: {count}')
    session.commit()
    print('\nCommit OK.')

    # Weryfikacja: po usunięciu nie powinno zostać nic w zakresie.
    left = sum(_count(session, step.model, step.where)
               for step in plan if step.action == 'delete')
    print(f'Pozostało rekordów w usuniętych zakresach: {left}')
    keep_documents = args.keep_documents or args.scope == 'legacy'
    if keep_documents:
      print(f'Dokumenty pozostałe w bazie: {_count(session, Document, None)}')
    return 0 if left == 0 else 1
  except Exception:
    session.rollback()
    raise
  finally:
    session.close()


if __name__ == '__main__':
  raise SystemExit(main())
