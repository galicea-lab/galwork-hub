#!/usr/bin/env python3
# coding: utf-8

"""
Narzędzie administratora: usuwa pojedyncze, błędnie wystartowane instancje
procesów (np. uruchomione z generycznego ekranu "Modele procesów" bez
wymaganych danych wejściowych - zob. galwork-book, czesc-3-obszary/dzialy/
zakupy.md). W przeciwieństwie do `clear_process_data.py` (który czyści
CAŁE modele/wersje procesów - dane testowe przed wdrożeniem) ten skrypt
NIE dotyka definicji procesu (ProcessModel/ProcessModelVersion/pliki BPMN)
- usuwa tylko wskazane instancje i ich dane potomne.

Domyślnie pokazuje tylko, co zostałoby usunięte (dry-run). Aby wykonać:

    python3 scripts/admin_delete_process_instances.py --id <uuid> --execute --yes

Wybór instancji (warunki łączone przez OR - usuwana jest każda instancja
spełniająca CHOCIAŻ JEDEN z podanych warunków; trzeba podać co najmniej
jeden):

  --id UUID            instancja o tym id (można powtórzyć)
  --business-key KEY   instancja o tym business_key (można powtórzyć)
  --model-key KEY --status STATUS
                        wszystkie instancje danego modelu w danym statusie
                        (np. "wniosek-zakupowy" + "running" - błędne testy)

Dokumenty podpięte do zadań usuwanych instancji: domyślnie tylko odpinane
(`document_version.human_task_id = NULL`), sam `Document`/`DocumentVersion`
zostaje. Użyj `--delete-documents`, żeby usunąć je razem z instancją.

Rekordy domenowe odwołujące się do instancji przez `ON DELETE SET NULL`
(np. `PurchaseRequest.process_instance_id/tender_process_instance_id`)
baza zeruje sama - skrypt ich nie dotyka.

UWAGA: to operacja nieodwracalna. Zrób kopię bazy przed `--execute`.
"""
from __future__ import annotations

import argparse
import os
import sys
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, func, select, update

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


@dataclass
class Step:
    label: str
    model: Any
    where: Any = None
    action: str = "delete"
    values: dict[str, Any] | None = None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Usuwa wskazane, błędne instancje procesów (nie dotyka definicji procesu).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--id", dest="ids", action="append", default=[],
                        help="id instancji (UUID) - można podać wiele razy")
    parser.add_argument("--business-key", dest="business_keys", action="append",
                        default=[], help="business_key instancji - można podać wiele razy")
    parser.add_argument("--model-key", default=None,
                        help="klucz ProcessModel (łącznie z --status)")
    parser.add_argument("--status", default=None,
                        help="status instancji, np. running/waiting/error "
                             "(łącznie z --model-key)")
    parser.add_argument("--execute", action="store_true",
                        help="naprawdę usuń dane (bez tego tylko raport)")
    parser.add_argument("--yes", action="store_true",
                        help="potwierdzenie dla --execute")
    parser.add_argument("--delete-documents", action="store_true",
                        help="usuń też dokumenty podpięte do zadań tych instancji "
                             "(domyślnie są tylko odpinane)")
    parser.add_argument("--database-url", default=None,
                        help="wskaż bazę zamiast config.ini/DATABASE_URL")
    return parser.parse_args(argv)


def resolve_instance_ids(session: Any, args: argparse.Namespace, ProcessInstance: Any,
                         ProcessModelVersion: Any, ProcessModel: Any) -> list[Any]:
    conditions = []
    if args.ids:
        try:
            parsed_ids = [uuid.UUID(v) for v in args.ids]
        except ValueError as exc:
            raise SystemExit(f"Błąd: niepoprawny UUID w --id ({exc})")
        conditions.append(ProcessInstance.id.in_(parsed_ids))
    if args.business_keys:
        conditions.append(ProcessInstance.business_key.in_(args.business_keys))
    if args.model_key and args.status:
        conditions.append(
            (ProcessModel.key == args.model_key) & (ProcessInstance.status == args.status))
    elif args.model_key or args.status:
        raise SystemExit("Błąd: --model-key i --status trzeba podać razem.")

    if not conditions:
        raise SystemExit(
            "Błąd: podaj co najmniej jeden warunek wyboru "
            "(--id / --business-key / --model-key+--status).")

    combined = conditions[0]
    for cond in conditions[1:]:
        combined = combined | cond

    stmt = (
        select(ProcessInstance.id)
        .join(ProcessModelVersion, ProcessInstance.process_model_version_id == ProcessModelVersion.id)
        .join(ProcessModel, ProcessModelVersion.process_model_id == ProcessModel.id)
        .where(combined)
    )
    return list(session.scalars(stmt).all())


def build_plan(instance_ids: list[Any], args: argparse.Namespace, models: dict[str, Any]) -> list[Step]:
    Comment = models["Comment"]
    DocumentVersion = models["DocumentVersion"]
    EngineJob = models["EngineJob"]
    HumanTask = models["HumanTask"]
    HumanTaskCandidate = models["HumanTaskCandidate"]
    ProcessEvent = models["ProcessEvent"]
    ProcessInstance = models["ProcessInstance"]
    ProcessInstanceDocument = models["ProcessInstanceDocument"]
    ProcessInstanceState = models["ProcessInstanceState"]

    task_ids = models["task_ids"]

    plan: list[Step] = []

    def add(label: str, model: Any, where: Any = None, action: str = "delete",
            values: dict[str, Any] | None = None) -> None:
        plan.append(Step(label=label, model=model, where=where, action=action, values=values))

    # Kolejność jak w clear_process_data.py (dzieci przed rodzicami; część
    # FK do process_instance/human_task nie ma kaskady - process_event i
    # document_version.human_task_id).
    add("comment", Comment, Comment.process_instance_id.in_(instance_ids))
    if args.delete_documents:
        add("document_version", DocumentVersion, DocumentVersion.human_task_id.in_(task_ids or (uuid.uuid4(),)))
    elif task_ids:
        add("document_version (odpięcie zadania)", DocumentVersion,
            DocumentVersion.human_task_id.in_(task_ids), "update", {"human_task_id": None})
    add("process_instance_document", ProcessInstanceDocument,
        ProcessInstanceDocument.process_instance_id.in_(instance_ids))
    add("human_task_candidate", HumanTaskCandidate,
        HumanTaskCandidate.human_task_id.in_(task_ids or (uuid.uuid4(),)))
    add("human_task", HumanTask, HumanTask.process_instance_id.in_(instance_ids))
    add("process_event", ProcessEvent, ProcessEvent.process_instance_id.in_(instance_ids))
    add("engine_job", EngineJob, EngineJob.process_instance_id.in_(instance_ids))
    add("process_instance_state", ProcessInstanceState,
        ProcessInstanceState.process_instance_id.in_(instance_ids))
    add("process_instance", ProcessInstance, ProcessInstance.id.in_(instance_ids))
    return plan


def _count(session: Any, model: Any, where: Any) -> int:
    stmt = select(func.count()).select_from(model)
    if where is not None:
        stmt = stmt.where(where)
    return int(session.scalar(stmt) or 0)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.execute and not args.yes:
        print("Błąd: --execute wymaga --yes (operacja nieodwracalna).", file=sys.stderr)
        return 2

    from sqlalchemy.orm import sessionmaker
    if args.database_url:
        from sqlalchemy import create_engine
        connect_args = ({"check_same_thread": False}
                        if args.database_url.startswith("sqlite") else {})
        engine = create_engine(args.database_url, connect_args=connect_args)
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    else:
        from db.session import SessionLocal, engine
    from wf.models import (
        Comment,
        DocumentVersion,
        EngineJob,
        HumanTask,
        HumanTaskCandidate,
        ProcessEvent,
        ProcessInstance,
        ProcessInstanceDocument,
        ProcessInstanceState,
        ProcessModel,
        ProcessModelVersion,
    )

    print(f"Baza: {engine.url.render_as_string(hide_password=True)}")
    print(f"Tryb: {'EXECUTE' if args.execute else 'DRY-RUN'}")

    session = SessionLocal()
    try:
        instance_ids = resolve_instance_ids(session, args, ProcessInstance, ProcessModelVersion, ProcessModel)
        if not instance_ids:
            print("Brak instancji spełniających podane warunki.")
            return 0

        rows = session.execute(
            select(ProcessInstance.id, ProcessInstance.business_key, ProcessInstance.status,
                   ProcessModel.key, ProcessModel.name)
            .join(ProcessModelVersion, ProcessInstance.process_model_version_id == ProcessModelVersion.id)
            .join(ProcessModel, ProcessModelVersion.process_model_id == ProcessModel.id)
            .where(ProcessInstance.id.in_(instance_ids))
        ).all()
        print(f"\nInstancje do usunięcia ({len(rows)}):")
        for row in rows:
            print(f"  - {row[0]}  business_key={row[1] or '-'}  status={row[2]}  model={row[3]} ({row[4]})")

        task_ids = list(session.scalars(
            select(HumanTask.id).where(HumanTask.process_instance_id.in_(instance_ids))).all())

        models = {
            "Comment": Comment, "DocumentVersion": DocumentVersion, "EngineJob": EngineJob,
            "HumanTask": HumanTask, "HumanTaskCandidate": HumanTaskCandidate,
            "ProcessEvent": ProcessEvent, "ProcessInstance": ProcessInstance,
            "ProcessInstanceDocument": ProcessInstanceDocument,
            "ProcessInstanceState": ProcessInstanceState, "task_ids": task_ids,
        }
        plan = build_plan(instance_ids, args, models)
        counts = [(step, _count(session, step.model, step.where)) for step in plan]
        total = sum(count for _step, count in counts)
        print("\nTabela / operacja                              Liczba")
        print("-" * 62)
        for step, count in counts:
            action = "DELETE" if step.action == "delete" else "UPDATE"
            print(f"{step.label:<44} {action:>6} {count:>7}")
        print("-" * 62)
        print(f"Razem rekordów do zmiany: {total}")

        if not args.execute:
            print("\nTo był dry-run. Aby wykonać: --execute --yes")
            return 0

        print("\nWykonuję...")
        for step, count in counts:
            if step.action == "delete":
                stmt = delete(step.model)
                if step.where is not None:
                    stmt = stmt.where(step.where)
            else:
                stmt = update(step.model).where(step.where).values(**(step.values or {}))
            session.execute(stmt)
            print(f"  {step.label}: {count}")
        session.commit()
        print("\nCommit OK.")

        left = _count(session, ProcessInstance, ProcessInstance.id.in_(instance_ids))
        print(f"Pozostało instancji z wybranych: {left}")
        return 0 if left == 0 else 1
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
