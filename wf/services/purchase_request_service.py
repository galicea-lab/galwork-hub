# wf/services/purchase_request_service.py
# coding: utf-8
"""
Wniosek zakupowy (`PurchaseRequest`, zob. wf/models/wf.py) - implementacja
projektu z galwork-book, czesc-3-obszary/dzialy/zakupy.md.

Reguły akceptacji ("Reguły akceptacji" w zakupy.md) są wyliczane TU, przy
złożeniu wniosku - nie w BPMN. Silnik w tym hubie nie ma scriptTasków
(wf/engine.py), więc proces `wniosek-zakupowy.bpmn` nie może sam
przeliczyć "czy inwestycja", "czy kwota przekracza próg" itd.

2026-10-20 (odtworzone testem na żywym silniku): `start_instance(...,
data=...)` zapisuje `data` do `workflow.data` - WIDOCZNEGO w formularzach
(`task_form_data()` czyta `workflow.data`), ale NIEWIDOCZNEGO dla
`sequenceFlow`/`conditionExpression`, które czytają wyłącznie `task.data`
BIEŻĄCEGO zadania. `task.data` zadania, które jeszcze nie zostało
wykonane (`spiff_task.run()`), nie przeżywa cyklu
zapis-stanu/odtworzenie-stanu między dwoma wywołaniami API (potwierdzone:
ręczna mutacja `task.data` gotowego zadania i ponowne wczytanie stanu
gubi tę zmianę - SpiffWorkflow liczy deltę względem `task.parent.data` w
chwili `run()`, nie wcześniej). Stąd flagi `requires_*` są w schemacie
formularza KAŻDEGO zadania akceptującego/`Task_Realizacja`
(`wniosek-review-schema.json`, `realizacja-schema.json`) jako pola
`ui:widget: hidden` - frontend odsyła je razem z resztą `formData` przy
każdym zatwierdzeniu, więc `engine.complete()` (`spiff_task.data.update
(form_data)` tuż przed `spiff_task.run()`) wnosi je na nowo do KAŻDEGO
kroku - tym samym mechanizmem, którym silnik już propaguje `chosen_path`.

Progi/flaga PZP są w `[zakupy]` config.ini (2026-10-20: "zakładamy
odrębną instancję na organizację"), nie w bazie - zob. config/config.py::section.

UWAGA (uproszczenie): progi kwotowe porównywane są z `amount_estimated`
zakładając, że `currency` wniosku == `[zakupy] domyslna_waluta` - przeliczanie
walut jest domeną ERP (zakupy.md, "Co zostaje poza Nodavią"), tu nie jest
zaimplementowane. Wniosek w innej walucie dostaje `requires_additional_approval`
wyliczone bez konwersji (do czasu realnej potrzeby wielowalutowych wniosków).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from config.config import section as config_section
from wf.models import Contact, ProcessInstance, ProcessModelVersion, PurchaseRequest
from wf.schemas.wf_schemas import CurrentUser, PurchaseExpenseType, PurchaseKind, PurchaseRequestCreate
from wf.services.process_service import ProcessServiceError, get_service

PROCESS_MODEL_KEY = "wniosek-zakupowy"


class PurchaseRequestNotFoundError(Exception):
    pass


class PurchaseRequestConfigError(Exception):
    """[zakupy] w config.ini brak albo zawiera niepoprawną wartość liczbową."""


@dataclass(frozen=True)
class RoutingFlags:
    """Wynik reguły akceptacji (zakupy.md) - zob. docstring modułu."""
    requires_investment_approval: bool
    requires_project_owner_approval: bool
    requires_additional_approval: bool
    requires_tender: bool
    requires_warehouse_receipt: bool

    def as_dict(self) -> dict[str, bool]:
        return {
            "requires_investment_approval": self.requires_investment_approval,
            "requires_project_owner_approval": self.requires_project_owner_approval,
            "requires_additional_approval": self.requires_additional_approval,
            "requires_tender": self.requires_tender,
            "requires_warehouse_receipt": self.requires_warehouse_receipt,
        }


def _decimal(values: dict, key: str, default: str) -> Decimal:
    raw = values.get(key, default) or default
    try:
        return Decimal(str(raw))
    except InvalidOperation as exc:
        raise PurchaseRequestConfigError(
            f"[zakupy] {key}='{raw}' nie jest liczbą") from exc


def _bool(values: dict, key: str, default: bool) -> bool:
    raw = values.get(key)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def compute_routing_flags(payload: PurchaseRequestCreate) -> RoutingFlags:
    """Wylicza flagi routingu z pól wniosku i `[zakupy]` (zob. docstring modułu)."""
    values = config_section("zakupy")
    prog_dodatkowej_akceptacji = _decimal(values, "prog_dodatkowej_akceptacji", "10000")
    prog_pzp = _decimal(values, "prog_pzp", "10000")
    pzp_wlaczone = _bool(values, "pzp_wlaczone", True)

    amount = payload.amount_estimated
    return RoutingFlags(
        requires_investment_approval=payload.expense_type == PurchaseExpenseType.INVESTMENT,
        requires_project_owner_approval=bool(payload.project_code),
        requires_additional_approval=amount >= prog_dodatkowej_akceptacji,
        requires_tender=pzp_wlaczone and amount >= prog_pzp,
        requires_warehouse_receipt=payload.purchase_kind == PurchaseKind.STOCK,
    )


_WITH_RELATIONS = (
    selectinload(PurchaseRequest.contact),
    selectinload(PurchaseRequest.process_instance)
    .selectinload(ProcessInstance.version)
    .selectinload(ProcessModelVersion.process_model),
)


def get_purchase_request(db: Session, purchase_request_id: UUID) -> PurchaseRequest:
    row = db.execute(
        select(PurchaseRequest).options(*_WITH_RELATIONS)
        .where(PurchaseRequest.id == purchase_request_id)
    ).scalars().first()
    if row is None:
        raise PurchaseRequestNotFoundError(f"Wniosek zakupowy {purchase_request_id} nie istnieje")
    return row


def list_purchase_requests(
    db: Session, *, contact_id: UUID | None = None, limit: int = 50, offset: int = 0
) -> tuple[list[PurchaseRequest], int]:
    stmt = select(PurchaseRequest)
    if contact_id is not None:
        stmt = stmt.where(PurchaseRequest.contact_id == contact_id)
    total = db.execute(select(func.count()).select_from(stmt.order_by(None).subquery())).scalar_one()
    rows = (
        db.execute(
            stmt.options(*_WITH_RELATIONS)
            .order_by(PurchaseRequest.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        .scalars()
        .all()
    )
    return rows, total


def create_and_start(
    db: Session, payload: PurchaseRequestCreate, *, actor: CurrentUser
) -> PurchaseRequest:
    """Tworzy `PurchaseRequest` i startuje obieg `wniosek-zakupowy` z
    wyliczonymi flagami routingu jako danymi startowymi procesu."""
    # 2026-10-20: dostawca jest opcjonalny - składający nie musi go znać
    # w chwili złożenia wniosku (może zostać przypisany później, np. po
    # obiegu "Wybór dostawcy" - zob. zakupy.md).
    contact: Contact | None = None
    if payload.contact_id is not None:
        contact = db.get(Contact, payload.contact_id)
        if contact is None:
            raise PurchaseRequestNotFoundError(f"Kontrahent {payload.contact_id} nie istnieje")

    flags = compute_routing_flags(payload)

    purchase_request = PurchaseRequest(
        contact_id=payload.contact_id,
        title=payload.title,
        justification=payload.justification,
        amount_estimated=payload.amount_estimated,
        currency=payload.currency,
        expense_type=payload.expense_type,
        purchase_kind=payload.purchase_kind,
        project_code=payload.project_code,
        created_by_id=actor.id,
        **flags.as_dict(),
    )
    db.add(purchase_request)
    db.flush()  # potrzebne purchase_request.id jako business_key procesu

    service = get_service()
    try:
        version = service.published_version(db, PROCESS_MODEL_KEY)
        instance = service.start_instance(
            db, version,
            title=f"Wniosek zakupowy: {payload.title}",
            business_key=str(purchase_request.id),
            data={
                **flags.as_dict(),
                "dostawca": (
                    (contact.company_name or f"{contact.first_name or ''} {contact.last_name or ''}".strip())
                    if contact is not None else "(do wyboru)"
                ),
                "tytul": payload.title,
                "uzasadnienie": payload.justification or "",
                "kwota_szacowana": str(payload.amount_estimated),
                "waluta": payload.currency,
                "projekt_mpk": payload.project_code or "",
            },
            actor=actor,
        )
    except ProcessServiceError:
        db.rollback()
        raise

    purchase_request.process_instance_id = instance.id
    db.commit()
    db.refresh(purchase_request)
    return get_purchase_request(db, purchase_request.id)


def start_tender(db: Session, purchase_request_id: UUID, *, actor: CurrentUser) -> PurchaseRequest:
    """Startuje osobny obieg `zamowienie-publiczne`, powiązany z wnioskiem
    przez `business_key` (nie Call Activity - zob. zakupy.md, "Otwarte
    decyzje": silnik w tym hubie wiąże obiegi pomocnicze (callActivity)
    wyłącznie w ramach JEDNEGO ProcessModelVersion - zob. wf/services/
    process_service.py::load_spec - więc dwa NIEZALEŻNIE publikowane i
    uruchamiane obiegi, jak chciał użytkownik dla "Zamówienie publiczne",
    łączy się tu identyfikatorem, tak jak "Wybór dostawcy")."""
    purchase_request = get_purchase_request(db, purchase_request_id)
    if not purchase_request.requires_tender:
        raise ProcessServiceError(
            f"Wniosek {purchase_request_id} nie wymaga procedury PZP (requires_tender=False)")

    service = get_service()
    version = service.published_version(db, "zamowienie-publiczne")
    instance = service.start_instance(
        db, version,
        title=f"Zamówienie publiczne: {purchase_request.title}",
        business_key=str(purchase_request.id),
        data={"wniosek_zakupowy_id": str(purchase_request.id), "tytul": purchase_request.title},
        actor=actor,
    )
    purchase_request.tender_process_instance_id = instance.id
    db.commit()
    db.refresh(purchase_request)
    return get_purchase_request(db, purchase_request.id)
