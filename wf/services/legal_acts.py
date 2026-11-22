# wf/services/legal_acts.py

# 2026-11-17: akty wewnętrzne (zarządzenia, uchwały, regulaminy) w
# repozytorium regulacji - wersje treści z datą wejścia w życie, relacje
# "zmienia"/"uchyla" i status prawny wyliczany na dany dzień.
"""
Akty wewnętrzne.

Akt to dokument huba (`Document`) dołączony jako główny do instancji modelu
repozytorium (`ACT_MODELS`, domyślnie `dokumenty`). Treść aktu ma wersje
(`DocumentVersion`); wersja z `effective_from` to redakcja obowiązująca od
tej daty (tekst pierwotny, tekst jednolity po zmianie). Relacje między aktami
(`DocumentRelation`): akt zmieniający / uchylający wskazuje akt docelowy
i datę, od której zmiana działa.

Status prawny na dzień - kody jak w polu "Status" formularzy regulacji:
  * wyliczane: 0 Nieustalony (brak obowiązującej redakcji), 1 Obowiązujący,
    5 Zmieniony (obowiązujący po zmianach), 3 Uchylony,
  * ustawiane ręcznie w repozytorium (krok "Zmiana statusu"): 2 Unieważniony,
    4 Wygasły - mają pierwszeństwo przed wyliczonymi.

Kroki systemowe (serviceTask):
  * `akty.obowiazuje_od` - data wejścia w życie bieżącej wersji dokumentu
    (z pól wskazanych właściwością `dateField`), powiązanie tej wersji
    z oczekującą zmianą (tekst jednolity),
  * `akty.relacje` - zapis relacji "zmienia"/"uchyla" dla publikowanego aktu
    (pola wskazane właściwościami `amendsField`, `repealsField`).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable, Mapping

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from wf.models import (
    Document,
    DocumentRelation,
    DocumentRelationType,
    DocumentVersion,
    HumanTask,
    ProcessInstance,
    ProcessInstanceDocument,
    ProcessModel,
    ProcessModelVersion,
)
from wf.schemas.wf_schemas import DocumentLinkRole, EventType, ProcessInstanceStatus
from wf.services import service_tasks
from wf.services.service_tasks import ServiceContext, ServiceResult, ServiceTaskError

#: modele procesów, których dokument główny jest aktem w repozytorium
ACT_MODELS: tuple[str, ...] = ('dokumenty',)

STATUS_LABELS: dict[str, str] = {
    '0': 'Nieustalony',
    '1': 'Obowiązujący',
    '2': 'Unieważniony',
    '3': 'Uchylony',
    '4': 'Wygasły',
    '5': 'Zmieniony',
}
#: statusy ustawiane ręcznie w repozytorium - nie wynikają z dat i relacji
MANUAL_STATUSES = frozenset({'2', '4'})
#: pole statusu w formularzu repozytorium
STATUS_FIELD = 'Status'


class ActError(Exception):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


@dataclass
class ActRelationInfo:
    document_id: uuid.UUID
    title: str
    number: str | None
    relation_type: str
    effective_from: date
    target_version_no: int | None


@dataclass
class ActVersionInfo:
    version_no: int
    effective_from: date | None
    valid_to: date | None
    file_name: str
    sha256: str
    label: str | None
    sealed: bool


@dataclass
class ActState:
    document_id: uuid.UUID
    title: str
    number: str | None
    on: date
    status_code: str
    status: str
    status_manual: bool
    valid_version_no: int | None
    valid_from: date | None
    valid_to: date | None
    repository_instance_id: uuid.UUID | None
    versions: list[ActVersionInfo] = field(default_factory=list)
    amended_by: list[ActRelationInfo] = field(default_factory=list)
    repealed_by: ActRelationInfo | None = None
    amends: list[ActRelationInfo] = field(default_factory=list)
    repeals: list[ActRelationInfo] = field(default_factory=list)


# --- repozytorium ----------------------------------------------------------

def _repository_links(db: Session, document_ids: Iterable[uuid.UUID] | None = None):
    """(dokument, instancja repozytorium) dla aktów (najnowsza nieanulowana instancja)."""
    stmt = (select(ProcessInstanceDocument.document_id, ProcessInstance)
            .join(ProcessInstance, ProcessInstance.id == ProcessInstanceDocument.process_instance_id)
            .join(ProcessModelVersion, ProcessInstance.process_model_version_id == ProcessModelVersion.id)
            .join(ProcessModel, ProcessModelVersion.process_model_id == ProcessModel.id)
            .where(ProcessModel.key.in_(ACT_MODELS),
                   ProcessInstanceDocument.role == DocumentLinkRole.MAIN,
                   ProcessInstance.status != ProcessInstanceStatus.CANCELLED)
            .order_by(ProcessInstance.started_at))
    if document_ids is not None:
        stmt = stmt.where(ProcessInstanceDocument.document_id.in_(list(document_ids)))
    result: dict[uuid.UUID, ProcessInstance] = {}
    for document_id, instance in db.execute(stmt).all():
        result[document_id] = instance  # najnowsza wygrywa
    return result


def repository_instance(db: Session, document_id: uuid.UUID) -> ProcessInstance | None:
    return _repository_links(db, [document_id]).get(document_id)


def latest_form_data(db: Session, instance: ProcessInstance | None) -> dict[str, Any]:
    """Dane formularza repozytorium: ostatnie wykonane zadanie, inaczej dane startowe."""
    if instance is None:
        return {}
    task = db.execute(
        select(HumanTask).where(HumanTask.process_instance_id == instance.id,
                                HumanTask.submitted_data.is_not(None))
        .order_by(HumanTask.completed_at.desc().nulls_last())
    ).scalars().first()
    data = dict(instance.summary or {})
    if task is not None:
        data.update(task.submitted_data or {})
    return data


def _number(data: Mapping[str, Any]) -> str | None:
    parts = [str(data.get(k) or '').strip() for k in ('Typ dokumentu', 'Nr dokumentu')]
    text = ' '.join(p for p in parts if p)
    return text or None


def _title(data: Mapping[str, Any], document: Document) -> str:
    """Tytuł aktu z formularza repozytorium, inaczej tytuł dokumentu (np. nazwa pliku)."""
    return str(data.get('Tytuł dokumentu') or '').strip() or document.title


def is_restricted(data: Mapping[str, Any]) -> bool:
    """Akt z "Ograniczeniami dostępu" w repozytorium."""
    limits = data.get('Ograniczenia dostępu')
    if isinstance(limits, Mapping):
        return any(bool(v) for v in limits.values())
    return bool(limits)


# --- status ----------------------------------------------------------------

def _relation_info(relation: DocumentRelation, other: Document,
                   data: Mapping[str, Any]) -> ActRelationInfo:
    return ActRelationInfo(document_id=other.id, title=_title(data, other), number=_number(data),
                           relation_type=relation.relation_type.value,
                           effective_from=relation.effective_from,
                           target_version_no=relation.target_version_no)


def act_state(db: Session, document: Document, on: date | None = None) -> ActState:
    """Status prawny aktu na dzień `on` (domyślnie dziś) i historia redakcji."""
    on = on or date.today()
    instance = repository_instance(db, document.id)
    data = latest_form_data(db, instance)

    # redakcje: wersje z datą; przy tej samej dacie wygrywa późniejsza (np. opieczętowana)
    metadata = document.doc_metadata or {}
    sealed_nos = set(metadata.get('pieczec_wersje') or [])
    sealed_nos.add((metadata.get('pieczec') or {}).get('version_no'))
    dated = sorted((v for v in document.versions if v.effective_from is not None),
                   key=lambda v: (v.effective_from, v.version_no))
    versions: list[ActVersionInfo] = []
    for v in document.versions:
        later = [d.effective_from for d in dated
                 if v.effective_from is not None and d.effective_from > v.effective_from]
        versions.append(ActVersionInfo(
            version_no=v.version_no, effective_from=v.effective_from,
            valid_to=min(later) if later else None, file_name=v.file_name, sha256=v.sha256,
            label=v.label, sealed=v.version_no in sealed_nos))
    in_force = [v for v in dated if v.effective_from <= on]
    current = in_force[-1] if in_force else None

    relations = db.execute(select(DocumentRelation).where(or_(
        DocumentRelation.source_document_id == document.id,
        DocumentRelation.target_document_id == document.id))).scalars().all()
    other_ids = {r.source_document_id if r.target_document_id == document.id else r.target_document_id
                 for r in relations}
    others = {d.id: d for d in db.execute(select(Document).where(Document.id.in_(other_ids))).scalars()}
    other_instances = _repository_links(db, other_ids) if other_ids else {}
    other_data = {doc_id: latest_form_data(db, inst) for doc_id, inst in other_instances.items()}

    amended_by, amends, repeals = [], [], []
    repealed_by = None
    for r in sorted(relations, key=lambda r: r.effective_from):
        incoming = r.target_document_id == document.id
        other_id = r.source_document_id if incoming else r.target_document_id
        info = _relation_info(r, others[other_id], other_data.get(other_id, {}))
        if incoming and r.relation_type == DocumentRelationType.AMENDS:
            amended_by.append(info)
        elif incoming and r.relation_type == DocumentRelationType.REPEALS:
            if repealed_by is None or r.effective_from < repealed_by.effective_from:
                repealed_by = info
        elif r.relation_type == DocumentRelationType.AMENDS:
            amends.append(info)
        else:
            repeals.append(info)

    manual = str(data.get(STATUS_FIELD) or '')
    valid_to = None
    if manual in MANUAL_STATUSES:
        code = manual
    elif repealed_by is not None and repealed_by.effective_from <= on:
        code = '3'
    elif current is None:
        code = '0'
    elif any(a.effective_from <= on for a in amended_by):
        code = '5'
    else:
        code = '1'
    if current is not None:
        later = [d.effective_from for d in dated if d.effective_from > current.effective_from]
        ends = later + ([repealed_by.effective_from] if repealed_by else [])
        valid_to = min(ends) if ends else None

    return ActState(
        document_id=document.id, title=_title(data, document), number=_number(data), on=on,
        status_code=code, status=STATUS_LABELS[code], status_manual=manual in MANUAL_STATUSES,
        valid_version_no=current.version_no if current is not None and code not in ('3', '2') else None,
        valid_from=current.effective_from if current is not None else None, valid_to=valid_to,
        repository_instance_id=instance.id if instance is not None else None,
        versions=versions, amended_by=amended_by, repealed_by=repealed_by,
        amends=amends, repeals=repeals)


def act_options(db: Session, query: str | None, limit: int, *, can_see_restricted) -> list[dict]:
    """Opcje wyboru aktu (`AsyncSelectWidget`): value = id dokumentu."""
    links = _repository_links(db)
    documents = {d.id: d for d in db.execute(
        select(Document).where(Document.id.in_(list(links)))).scalars()} if links else {}
    needle = (query or '').strip().lower()
    options = []
    for document_id, instance in links.items():
        document = documents.get(document_id)
        if document is None:
            continue
        data = latest_form_data(db, instance)
        if is_restricted(data) and not can_see_restricted(instance):
            continue
        number, title = _number(data), _title(data, document)
        label = f"{number} - {title}" if number else title
        if needle and needle not in label.lower():
            continue
        options.append({'value': str(document_id), 'label': label})
    options.sort(key=lambda o: o['label'])
    return options[:limit]


# --- kroki systemowe --------------------------------------------------------

def _get_path(data: Mapping[str, Any], path: str) -> Any:
    node: Any = data
    for part in path.split('.'):
        if not isinstance(node, Mapping) or part not in node:
            return None
        node = node[part]
    return node


def _first_date(data: Mapping[str, Any], paths: str | None) -> date | None:
    for path in (p.strip() for p in (paths or '').split(',')):
        if not path:
            continue
        value = _get_path(data, path)
        if not value:
            continue
        try:
            return date.fromisoformat(str(value)[:10])
        except ValueError as exc:
            raise ServiceTaskError(f"Pole '{path}' nie jest datą: {value!r}") from exc
    return None


def _ids(value: Any) -> list[uuid.UUID]:
    items = value if isinstance(value, list) else ([value] if value else [])
    result = []
    for item in items:
        raw = item.get('value') if isinstance(item, Mapping) else item
        if not raw:
            continue
        try:
            result.append(uuid.UUID(str(raw)))
        except ValueError as exc:
            raise ServiceTaskError(f"Nieprawidłowy identyfikator aktu: {raw!r}") from exc
    return result


def _context_document(ctx: ServiceContext) -> Document:
    raw = ctx.data.get('dokument_id')
    document = None
    if raw:
        try:
            document = ctx.db.get(Document, uuid.UUID(str(raw)))
        except ValueError:
            document = None
    else:
        links = sorted(ctx.instance.document_links,
                       key=lambda link: (link.role != DocumentLinkRole.MAIN, -link.added_at.timestamp()))
        document = links[0].document if links else None
    if document is None:
        raise ServiceTaskError("Brak dokumentu aktu (dokument_id) - dodaj dokument do sprawy")
    return document


def _event(ctx: ServiceContext, instance: ProcessInstance, event_type: EventType, payload: dict) -> None:
    from wf.services.process_service import get_service  # bez cyklu importów
    # bez commita - zapis razem z postępem procesu (ProcessService.advance_system)
    get_service()._add_event(ctx.db, instance, event_type, actor=ctx.actor, payload=payload)


@service_tasks.handler('akty.obowiazuje_od')
def effective_from_step(ctx: ServiceContext) -> ServiceResult:
    """Data wejścia w życie bieżącej wersji aktu. Wersja, która ma już datę,
    nie jest zmieniana. Gdy akt ma oczekującą zmianę (relacja "zmienia" bez
    tekstu jednolitego), wersja staje się jej tekstem jednolitym i dostaje
    datę zmiany; inaczej datę z pól `dateField`."""
    document = _context_document(ctx)
    version = document.current_version or (document.versions[-1] if document.versions else None)
    if version is None:
        raise ServiceTaskError(f"Dokument '{document.title}' nie ma żadnej wersji pliku")
    if version.effective_from is not None:
        return service_tasks.done({'dokument_id': str(document.id),
                                   'obowiazuje_od': version.effective_from.isoformat()})

    pending = ctx.db.execute(
        select(DocumentRelation).where(DocumentRelation.target_document_id == document.id,
                                       DocumentRelation.relation_type == DocumentRelationType.AMENDS,
                                       DocumentRelation.target_version_no.is_(None))
        .order_by(DocumentRelation.effective_from)).scalars().all()
    # oczekująca zmiana ma pierwszeństwo - pole formularza może jeszcze
    # zawierać datę pierwotnej redakcji
    effective = (pending[-1].effective_from if pending
                 else _first_date(ctx.data, ctx.params.get('dateField')))
    if effective is None:
        raise ServiceTaskError("Podaj datę wejścia w życie (pole "
                               f"{ctx.params.get('dateField') or 'dateField'}) i użyj „Sprawdź teraz”")
    previous = [v.effective_from for v in document.versions
                if v.effective_from is not None and v.version_no != version.version_no]
    if previous and effective <= max(previous):
        raise ServiceTaskError(
            f"Nowa redakcja musi wchodzić w życie później niż poprzednia ({max(previous)}), "
            f"a podano {effective} - popraw datę i użyj „Sprawdź teraz”")
    version.effective_from = effective
    linked = [r for r in pending if r.effective_from <= effective]
    for relation in linked:
        relation.target_version_no = version.version_no
    ctx.db.flush()
    _event(ctx, ctx.instance, EventType.DOCUMENT_VERSION_ADDED, {
        'document_id': str(document.id), 'version_no': version.version_no,
        'effective_from': effective.isoformat(),
        'consolidates': [str(r.source_document_id) for r in linked]})
    return service_tasks.done({'dokument_id': str(document.id), 'obowiazuje_od': effective.isoformat()})


@service_tasks.handler('akty.relacje')
def relations_step(ctx: ServiceContext) -> ServiceResult:
    """Relacje publikowanego aktu: "zmienia" (`amendsField`) i "uchyla"
    (`repealsField`) od daty jego wejścia w życie (`dateField`). Ponowne
    wykonanie nie dubluje relacji. Instancje repozytorium aktów docelowych
    dostają zdarzenie w dzienniku."""
    document = _context_document(ctx)
    effective = _first_date(ctx.data, ctx.params.get('dateField'))
    if effective is None and document.current_version is not None:
        effective = document.current_version.effective_from
    wanted = [(DocumentRelationType.AMENDS, target)
              for target in _ids(_get_path(ctx.data, ctx.params.get('amendsField') or ''))]
    wanted += [(DocumentRelationType.REPEALS, target)
               for target in _ids(_get_path(ctx.data, ctx.params.get('repealsField') or ''))]
    if not wanted:
        return service_tasks.done({'relacje': []})
    if effective is None:
        raise ServiceTaskError("Akt zmieniający/uchylający wymaga daty wejścia w życie")

    targets = _repository_links(ctx.db, [t for _k, t in wanted])
    created = []
    for kind, target_id in wanted:
        if target_id == document.id:
            raise ServiceTaskError("Akt nie może zmieniać ani uchylać samego siebie")
        if target_id not in targets:
            raise ServiceTaskError(f"Akt {target_id} nie jest zarejestrowany w repozytorium")
        exists = ctx.db.execute(select(DocumentRelation.id).where(
            DocumentRelation.source_document_id == document.id,
            DocumentRelation.target_document_id == target_id,
            DocumentRelation.relation_type == kind)).first()
        if exists:
            continue
        ctx.db.add(DocumentRelation(
            source_document_id=document.id, target_document_id=target_id, relation_type=kind,
            effective_from=effective, process_instance_id=ctx.instance.id,
            created_by_id=ctx.actor.id))
        created.append({'typ': kind.value, 'akt': str(target_id)})
        _event(ctx, targets[target_id], EventType.DOCUMENT_RELATION_ADDED, {
            'relation_type': kind.value, 'source_document_id': str(document.id),
            'source_title': document.title, 'effective_from': effective.isoformat(),
            'source_instance_id': str(ctx.instance.id)})
    ctx.db.flush()
    if created:
        _event(ctx, ctx.instance, EventType.DOCUMENT_RELATION_ADDED, {
            'document_id': str(document.id), 'relations': created,
            'effective_from': effective.isoformat()})
    return service_tasks.done({'relacje': created})
