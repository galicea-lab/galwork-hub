# wf/services/instance_access.py
# coding: utf-8
"""
2026-11-22: widoczność instancji procesu na liście (`GET /process-instances`).

Instancję widzi:
  * administrator procesów (`is_process_admin`) - wszystkie,
  * osoba, która ją uruchomiła (także zastępca - `acting_user_ids`),
  * osoba, która podjęła albo wykonała którekolwiek zadanie instancji,
  * kandydat zadania instancji (login, grupa albo rola w `human_task_candidate`).

Otwarte zadanie bez kandydatów NIE odsłania instancji: jest w skrzynce każdego
(`GET /tasks/my`), ale sprawę widać dopiero po jego podjęciu. Inaczej pętla
rejestru bez kandydatów (np. "Repozytorium dokumentów") ujawniałaby każdemu
akty z "Ograniczeniami dostępu". Reguła = dawne `instance_print.can_print`.

Rejestry jawne: wersja modelu z `galwork.visibility = "all"` (process_model.json,
`spec_json['galwork']`) pozwala każdemu zalogowanemu CZYTAĆ sprawę (lista,
szczegóły, komentarze). Działania na sprawie (`write=True`: budzenie,
anulowanie, wstrzymanie, wznowienie, nowy komentarz, EDR, Signius) oraz
dokumenty, dyskusje i wydruki (`is_participant`, dawne `can_print`) nadal
wymagają uczestnictwa - akty z "Ograniczeniami dostępu" pozostają chronione.

Warunek liczony w SQL, więc `total` zgadza się z `items` przy limit/offset.
Ta sama reguła w Pythonie (`can_view`) chroni endpointy pojedynczej instancji
(`get_visible_instance`): szczegóły, budzenie, anulowanie, wstrzymanie,
wznowienie, komentarze, dokumenty, dyskusje, wydruki, EDR, Signius.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from wf.dependencies import is_process_admin
from wf.models import HumanTask, HumanTaskCandidate, ProcessInstance, ProcessModelVersion
from wf.schemas.wf_schemas import CurrentUser


def principals(user: CurrentUser) -> tuple[str, ...]:
    """Wartości porównywane z `HumanTaskCandidate.principal_value`."""
    return tuple({user.username} | set(user.groups) | set(user.roles)) or ('',)


PUBLIC = 'all'


def is_public(instance: ProcessInstance) -> bool:
    """Wersja modelu sprawy ma `galwork.visibility = "all"`."""
    spec = instance.version.spec_json if instance.version is not None else None
    return ((spec or {}).get('galwork') or {}).get('visibility') == PUBLIC


def visible_clause(user: CurrentUser) -> ColumnElement[bool] | None:
    """Warunek WHERE dla `ProcessInstance` (odczyt); None = bez ograniczeń (administrator)."""
    if is_process_admin(user):
        return None
    acting = tuple(user.acting_user_ids) or (0,)
    task_rule = or_(
        HumanTask.claimed_by_id.in_(acting),
        HumanTask.completed_by_id.in_(acting),
        HumanTask.candidates.any(HumanTaskCandidate.principal_value.in_(principals(user))),
    )
    public = ProcessInstance.version.has(
        ProcessModelVersion.spec_json['galwork']['visibility'].as_string() == PUBLIC)
    return or_(ProcessInstance.started_by_id.in_(acting), ProcessInstance.tasks.any(task_rule), public)


def can_view(instance: ProcessInstance, user: CurrentUser, *, write: bool = False) -> bool:
    """`visible_clause` dla jednej instancji; `write=True` - działanie na sprawie
    (rejestr jawny nie wystarcza, potrzebne uczestnictwo)."""
    if not write and is_public(instance):
        return True
    return is_participant(instance, user)


def is_participant(instance: ProcessInstance, user: CurrentUser) -> bool:
    """Administrator albo uczestnik sprawy (bez reguły rejestru jawnego)."""
    if is_process_admin(user):
        return True
    acting = user.acting_user_ids
    if instance.started_by_id in acting:
        return True
    names = set(principals(user))
    for task in instance.tasks or []:
        if task.claimed_by_id in acting or task.completed_by_id in acting:
            return True
        if any(c.principal_value in names for c in (task.candidates or [])):
            return True
    return False


def get_visible_instance(db: Session, instance_id: UUID, user: CurrentUser, *options,
                         write: bool = False,
                         detail: str = "Brak uprawnień do tej sprawy") -> ProcessInstance:
    """Instancja albo 404 (nie istnieje) / 403 (użytkownik jej nie widzi)."""
    stmt = select(ProcessInstance).where(ProcessInstance.id == instance_id)
    if options:
        stmt = stmt.options(*options)
    instance = db.execute(stmt).scalars().first()
    if instance is None:
        raise HTTPException(status_code=404, detail="Instancja procesu nie znaleziona")
    if not can_view(instance, user, write=write):
        raise HTTPException(status_code=403, detail=detail)
    return instance
