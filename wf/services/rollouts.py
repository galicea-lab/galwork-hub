# wf/services/rollouts.py
# 2026-11-17:  wdrożenia obiegów - tryb użycia (testowy / pomocniczy /
# produkcyjny) obiegu w jednostkach organizacyjnych, na podstawie aktu.
"""
Wdrożenia obiegów.

Tryb dotyczy obiegu (modelu procesu), nie jego wersji:
  * `test` - obieg działa równolegle, obowiązuje procedura dotychczasowa
    (`previous_procedure`); zapisy nie są dokumentacją; okres zamknięty,
  * `auxiliary` - obieg pomocniczy; dokument źródłowy poza systemem
    (`previous_procedure` - gdzie jest),
  * `production` - obieg jest trybem obowiązującym.

Wdrożenie powstaje tylko z aktu (krok systemowy `obiegi.wdrozenie` w obiegu
"Wdrożenie obiegów"); jeden akt może wprowadzić kilka obiegów naraz. Nowe
wdrożenie tego samego obiegu w tych samych jednostkach zamyka poprzednie
(np. test -> produkcja). Zakres: jednostki organizacyjne (`OrgUnit`, z
podległymi); pusta lista = cała organizacja.

Sprawa zapamiętuje tryb przy starcie (`ProcessInstance.usage_mode`,
`rollout_id`); sprawa uruchomiona przez sprawę testową jest testowa.

Start obiegu (`[wf] rollout_enforcement`):
  * `off` - bez kontroli,
  * `warn` (domyślnie) - start dozwolony; bez wdrożenia zdarzenie
    `rollout_missing` w dzienniku sprawy,
  * `on` - bez wdrożenia obejmującego użytkownika obieg uruchamiają tylko
    administratorzy procesów (tryb deweloperski).

Dziennik wdrożenia: publikacja wersji modelu obiegu objętego wdrożeniem
zapisuje zdarzenie `rollout_version_published` w sprawie, która wdrożenie
utworzyła (wersja, skrót treści, tag git, publikujący).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Iterable, Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from wf.dependencies import is_process_admin
from wf.models import (
    Document,
    OrgMembership,
    OrgUnit,
    ProcessInstance,
    ProcessModel,
    ProcessModelVersion,
    ProcessRollout,
    RolloutMode,
)
from wf.schemas.wf_schemas import CurrentUser, EventType
from wf.services import service_tasks
from wf.services.service_tasks import ServiceContext, ServiceResult, ServiceTaskError

MODE_LABELS = {
    RolloutMode.TEST: 'testowy',
    RolloutMode.AUXILIARY: 'pomocniczy',
    RolloutMode.PRODUCTION: 'produkcyjny',
}
_MODE_ALIASES = {
    'test': RolloutMode.TEST, 'testowy': RolloutMode.TEST,
    'auxiliary': RolloutMode.AUXILIARY, 'pomocniczy': RolloutMode.AUXILIARY,
    'production': RolloutMode.PRODUCTION, 'produkcyjny': RolloutMode.PRODUCTION,
}


class RolloutError(Exception):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


def enforcement() -> str:
    from wf.services.process_files import _config
    value = (_config().get('rollout_enforcement') or 'warn').strip().lower()
    return value if value in ('off', 'warn', 'on') else 'warn'


# --- jednostki --------------------------------------------------------------

def _unit_id(raw: Any) -> uuid.UUID:
    text = str(raw.get('value') if isinstance(raw, Mapping) else raw).strip()
    if text.startswith('group:'):
        text = text[len('group:'):]
    try:
        return uuid.UUID(text)
    except ValueError as exc:
        raise RolloutError(f"Nieprawidłowa jednostka organizacyjna: {raw!r}") from exc


def user_units(db: Session, user: CurrentUser) -> set[uuid.UUID]:
    """Jednostki użytkownika (członkostwo i grupy Keycloak) wraz z nadrzędnymi."""
    direct = set(db.execute(select(OrgMembership.org_unit_id)
                            .where(OrgMembership.user_id == user.id)).scalars())
    if user.groups:
        direct |= set(db.execute(select(OrgUnit.id)
                                 .where(OrgUnit.keycloak_group.in_(list(user.groups)))).scalars())
    parents = dict(db.execute(select(OrgUnit.id, OrgUnit.parent_id)).all())
    result: set[uuid.UUID] = set()
    for unit in direct:
        while unit is not None and unit not in result:
            result.add(unit)
            unit = parents.get(unit)
    return result


# --- wyszukiwanie -----------------------------------------------------------

def _active(rollout: ProcessRollout, on: date) -> bool:
    return rollout.valid_from <= on and (rollout.valid_to is None or on < rollout.valid_to)


def rollouts_for_model(db: Session, process_model_id: uuid.UUID) -> list[ProcessRollout]:
    return db.execute(select(ProcessRollout)
                      .where(ProcessRollout.process_model_id == process_model_id)
                      .order_by(ProcessRollout.valid_from, ProcessRollout.created_at)).scalars().all()


def _covers(rollout: ProcessRollout, units: set[uuid.UUID]) -> bool:
    scope = {uuid.UUID(u) for u in rollout.org_unit_ids or []}
    return not scope or bool(scope & units)


def rollout_for_user(db: Session, process_model_id: uuid.UUID, user: CurrentUser,
                     on: date | None = None) -> ProcessRollout | None:
    """Aktywne wdrożenie obiegu obejmujące użytkownika; przy kilku - najwęższe
    (jednostkowe przed całą organizacją), potem najnowsze."""
    on = on or date.today()
    units = user_units(db, user)
    candidates = [r for r in rollouts_for_model(db, process_model_id)
                  if _active(r, on) and _covers(r, units)]
    if not candidates:
        return None
    return sorted(candidates, key=lambda r: (not r.org_unit_ids, -r.valid_from.toordinal()))[0]


@dataclass
class StartDecision:
    rollout: ProcessRollout | None
    usage_mode: str | None
    allowed: bool
    warning: str | None = None


def decide_start(db: Session, version: ProcessModelVersion, user: CurrentUser, *,
                 parent: ProcessInstance | None = None, check: bool = True) -> StartDecision:
    """Tryb nowej sprawy i (dla startu przez użytkownika) zgoda na start."""
    rollout = rollout_for_user(db, version.process_model_id, user)
    mode = rollout.mode.value if rollout is not None else None
    if parent is not None and parent.usage_mode == RolloutMode.TEST.value:
        mode = RolloutMode.TEST.value  # sprawa z testu pozostaje testowa
    if rollout is not None or not check:
        return StartDecision(rollout, mode, True)
    policy = enforcement()
    if policy == 'off':
        return StartDecision(None, mode, True)
    message = (f"Obieg '{version.process_model.name}' nie jest wdrożony w Twojej jednostce "
               f"(brak aktu wdrożeniowego)")
    if policy == 'on' and not is_process_admin(user):
        return StartDecision(None, mode, False, message)
    return StartDecision(None, mode, True, message)


# --- tworzenie i zamykanie ---------------------------------------------------

def _overlaps(a_units: list[str], b_units: list[str]) -> bool:
    return not a_units or not b_units or bool(set(a_units) & set(b_units))


def create_rollout(db: Session, *, process_model: ProcessModel, mode: RolloutMode,
                   org_unit_ids: list[uuid.UUID], valid_from: date, valid_to: date | None,
                   act_document: Document, act_version_no: int, previous_procedure: str | None,
                   actor: CurrentUser, process_instance: ProcessInstance | None) -> ProcessRollout:
    if mode == RolloutMode.TEST and valid_to is None:
        raise RolloutError("Wdrożenie testowe musi mieć datę zakończenia")
    if valid_to is not None and valid_to < valid_from:
        raise RolloutError("Data zakończenia wdrożenia jest wcześniejsza niż rozpoczęcia")
    if mode in (RolloutMode.TEST, RolloutMode.AUXILIARY) and not (previous_procedure or '').strip():
        raise RolloutError("Dla trybu testowego i pomocniczego podaj procedurę dotychczasową "
                           "albo miejsce dokumentu źródłowego")
    known = set(db.execute(select(OrgUnit.id).where(OrgUnit.id.in_(org_unit_ids))).scalars())
    missing = [str(u) for u in org_unit_ids if u not in known]
    if missing:
        raise RolloutError(f"Nieznane jednostki organizacyjne: {', '.join(missing)}")
    units = sorted(str(u) for u in org_unit_ids)
    now = datetime.now(UTC)
    # nowe wdrożenie zamyka dotychczasowe w tych samych jednostkach (test -> produkcja)
    for previous in rollouts_for_model(db, process_model.id):
        if previous.closed_at is not None or not _overlaps(previous.org_unit_ids or [], units):
            continue
        if previous.valid_to is not None and previous.valid_to <= valid_from:
            continue
        if previous.valid_from >= valid_from:
            raise RolloutError(
                f"Obieg '{process_model.name}' ma już wdrożenie od {previous.valid_from} "
                f"w tych jednostkach - nowe musi zaczynać się później")
        previous.valid_to = valid_from
        previous.closed_at = now
        previous.closed_by_id = actor.id
        previous.close_reason = f"zastąpione wdrożeniem od {valid_from} (akt {act_document.title})"
    rollout = ProcessRollout(
        process_model_id=process_model.id, mode=mode, org_unit_ids=units,
        valid_from=valid_from, valid_to=valid_to, act_document_id=act_document.id,
        act_version_no=act_version_no, previous_procedure=(previous_procedure or '').strip() or None,
        process_instance_id=process_instance.id if process_instance is not None else None,
        created_by_id=actor.id)
    db.add(rollout)
    db.flush()
    return rollout


def close_rollouts(db: Session, *, process_model: ProcessModel, org_unit_ids: list[uuid.UUID],
                   on: date, reason: str, actor: CurrentUser) -> list[ProcessRollout]:
    """Zakończenie wdrożenia (bez następcy) od dnia `on` w podanych jednostkach."""
    units = [str(u) for u in org_unit_ids]
    closed = []
    for rollout in rollouts_for_model(db, process_model.id):
        if rollout.closed_at is not None or not _overlaps(rollout.org_unit_ids or [], units):
            continue
        if rollout.valid_to is not None and rollout.valid_to <= on:
            continue
        rollout.valid_to = max(on, rollout.valid_from)
        rollout.closed_at = datetime.now(UTC)
        rollout.closed_by_id = actor.id
        rollout.close_reason = reason
        closed.append(rollout)
    db.flush()
    return closed


# --- dziennik wdrożenia ------------------------------------------------------

def log_publication(db: Session, version: ProcessModelVersion, actor: CurrentUser) -> int:
    """Zdarzenie `rollout_version_published` w sprawach, które utworzyły
    aktywne (dziś lub w przyszłości) wdrożenia obiegu."""
    from wf.services.process_service import get_service
    today = date.today()
    count = 0
    for rollout in rollouts_for_model(db, version.process_model_id):
        if rollout.valid_to is not None and rollout.valid_to <= today:
            continue
        instance = db.get(ProcessInstance, rollout.process_instance_id) if rollout.process_instance_id else None
        if instance is None:
            continue
        get_service()._add_event(db, instance, EventType.ROLLOUT_VERSION_PUBLISHED, actor=actor, payload={
            'rollout_id': str(rollout.id), 'mode': rollout.mode.value,
            'process_model_key': version.process_model.key, 'version': version.version,
            'content_hash': version.content_hash, 'git_tag': version.git_tag})
        count += 1
    return count


# --- krok systemowy ------------------------------------------------------------

def _get_path(data: Mapping[str, Any], path: str | None) -> Any:
    node: Any = data
    for part in (path or '').split('.'):
        if not part:
            return None
        if not isinstance(node, Mapping) or part not in node:
            return None
        node = node[part]
    return node


def _date(value: Any, label: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError as exc:
        raise ServiceTaskError(f"Pole '{label}' nie jest datą: {value!r}") from exc


def _list(value: Any) -> list:
    return value if isinstance(value, list) else ([value] if value else [])


@service_tasks.handler('obiegi.wdrozenie')
def rollout_step(ctx: ServiceContext) -> ServiceResult:
    """Wdrożenia (albo zakończenia wdrożeń) obiegów z aktu sprawy.

    Właściwości: `modelsField` (lista kluczy modeli), `modeField`
    (testowy/pomocniczy/produkcyjny), `unitsField` (jednostki), `fromField`,
    `toField`, `procedureField`, `actionField` (wdrożenie / zakończenie).
    Akt: `dokument_id` (bieżąca wersja - zwykle opieczętowana).
    Ponowne wykonanie w tej samej sprawie nie dubluje wdrożeń."""
    from wf.services.legal_acts import _context_document

    params, data = ctx.params, ctx.data
    document = _context_document(ctx)
    if not document.current_version_no:
        raise ServiceTaskError(f"Akt '{document.title}' nie ma wersji pliku")
    keys = [str(k.get('value') if isinstance(k, Mapping) else k)
            for k in _list(_get_path(data, params.get('modelsField')))]
    if not keys:
        raise ServiceTaskError("Akt nie wskazuje żadnego obiegu")
    models = {m.key: m for m in ctx.db.execute(
        select(ProcessModel).where(ProcessModel.key.in_(keys))).scalars()}
    unknown = [k for k in keys if k not in models]
    if unknown:
        raise ServiceTaskError(f"Nieznane obiegi: {', '.join(unknown)}")
    try:
        units = [_unit_id(u) for u in _list(_get_path(data, params.get('unitsField')))]
    except RolloutError as exc:
        raise ServiceTaskError(str(exc)) from exc
    valid_from = _date(_get_path(data, params.get('fromField')), params.get('fromField') or 'od')
    if valid_from is None:
        raise ServiceTaskError("Podaj datę rozpoczęcia wdrożenia")
    action = str(_get_path(data, params.get('actionField')) or 'wdrożenie').strip().lower()

    done_here = {r.process_model_id for r in ctx.db.execute(select(ProcessRollout).where(
        ProcessRollout.process_instance_id == ctx.instance.id)).scalars()}
    created, closed = [], []
    try:
        if action.startswith('zako'):
            for key in keys:
                for rollout in close_rollouts(ctx.db, process_model=models[key], org_unit_ids=units,
                                              on=valid_from, actor=ctx.actor,
                                              reason=f"zakończone aktem {document.title}"):
                    closed.append({'obieg': key, 'wdrozenie': str(rollout.id)})
        else:
            mode = _MODE_ALIASES.get(str(_get_path(data, params.get('modeField')) or '').strip().lower())
            if mode is None:
                raise ServiceTaskError("Wybierz tryb: testowy, pomocniczy albo produkcyjny")
            valid_to = _date(_get_path(data, params.get('toField')), params.get('toField') or 'do')
            procedure = _get_path(data, params.get('procedureField'))
            for key in keys:
                if models[key].id in done_here:
                    continue
                rollout = create_rollout(
                    ctx.db, process_model=models[key], mode=mode, org_unit_ids=units,
                    valid_from=valid_from, valid_to=valid_to, act_document=document,
                    act_version_no=document.current_version_no, previous_procedure=procedure,
                    actor=ctx.actor, process_instance=ctx.instance)
                created.append({'obieg': key, 'wdrozenie': str(rollout.id), 'tryb': mode.value})
    except RolloutError as exc:
        raise ServiceTaskError(str(exc)) from exc

    from wf.services.process_service import get_service
    if created or closed:
        get_service()._add_event(ctx.db, ctx.instance, EventType.ROLLOUT_CHANGED, actor=ctx.actor,
                                 payload={'document_id': str(document.id),
                                          'act_version_no': document.current_version_no,
                                          'created': created, 'closed': closed})
    return service_tasks.done({'wdrozenia': created, 'zakonczenia': closed})


def is_test(instance: ProcessInstance | None) -> bool:
    return instance is not None and instance.usage_mode == RolloutMode.TEST.value


def describe(rollout: ProcessRollout | None, usage_mode: str | None) -> dict[str, Any] | None:
    if usage_mode is None:
        return None
    mode = RolloutMode(usage_mode)
    return {'usage_mode': mode.value, 'label': MODE_LABELS[mode],
            'previous_procedure': rollout.previous_procedure if rollout is not None else None,
            'rollout_id': str(rollout.id) if rollout is not None else None}


def by_ids(db: Session, ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, ProcessRollout]:
    ids = [i for i in ids if i]
    if not ids:
        return {}
    return {r.id: r for r in db.execute(select(ProcessRollout).where(
        ProcessRollout.id.in_(ids))).scalars()}
