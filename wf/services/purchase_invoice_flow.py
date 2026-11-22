# wf/services/purchase_invoice_flow.py
# coding: utf-8
"""
2026-11-22 (ZAK.1, galwork-dev-jw/wdrozenia/galicea/SCENARIUSZ_ZAK.md):
faktura zakupu z ERPNext uruchamia obieg w hubie.

KSeF obsługuje ERPNext - do huba trafia tylko informacja, że w ERPNext
powstała faktura zakupu (`Purchase Invoice`, szkic). Hub zakłada wtedy sprawę
modelu `[erpnext] invoice_process_key` (domyślnie `faktura-zakupu`,
galwork-models/zakupy/faktura-zakupu): opis merytoryczny (projekt, rodzaj
kosztu, opis) -> akceptacja -> zapis w ERPNext i akcja workflow.

Wyzwalacze (oba idempotentne - klucz sprawy = `name` faktury w ERPNext,
druga sprawa dla tej samej faktury nie powstaje, dopóki pierwsza nie jest
anulowana):
  * webhook ERPNext (`POST /v1/wf/erpnext/webhooks/purchase-invoice`,
    wf/routers/erpnext_invoice_api.py) - Frappe „Webhook” na `after_insert`
    z sekretem `[erpnext] invoice_webhook_secret`,
  * uzgadnianie (`scripts/erpnext_invoices.py sync`, cron) - szkice faktur
    bez sprawy, np. po utraconym webhooku albo bez webhooka w ogóle.

Kroki systemowe (serviceTask `handler`):
  * `erpnext.faktura_akceptacja` - zapis pól `[erpnext] invoice_write_fields`
    (domyślnie `remarks=opis_merytoryczny, project=projekt` - projekt jest
    wymiarem księgowym w NAGŁÓWKU `Purchase Invoice`) i akcja
    `[erpnext] invoice_accept` (`workflow` - `invoice_approve_action`,
    `submit` albo `none`),
  * `erpnext.faktura_odrzucenie` - akcja workflow `invoice_reject_action`
    (pusta = bez zmian w ERPNext).
Błąd techniczny ERPNext -> `ServiceTaskError`, krok jest ponawiany
(`retryMinutes` w BPMN).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from connector.base import ConnectorError
from wf.models import ProcessInstance, ProcessModel, ProcessModelVersion
from wf.schemas.wf_schemas import CurrentUser, ProcessInstanceStatus
from wf.services import service_tasks

logger = logging.getLogger(__name__)

DEFAULT_PROCESS_KEY = 'faktura-zakupu'
_FIELDS = ['name', 'docstatus', 'supplier', 'supplier_name', 'tax_id', 'bill_no', 'bill_date',
           'posting_date', 'due_date', 'net_total', 'grand_total', 'currency', 'company']


def _erp():
    from connector.erpnext import get_connector
    return get_connector()


@dataclass(frozen=True)
class InvoiceConfig:
    doctype: str
    process_key: str
    describer_group: str
    approver_group: str
    accept: str                 # workflow | submit | none
    approve_action: str
    reject_action: str
    write_fields: tuple[tuple[str, str], ...]   # (pole ERPNext, ścieżka w danych sprawy)
    ksef_field: str
    webhook_secret: str
    base_url: str


def config(connector=None) -> InvoiceConfig:
    cfg = (connector or _erp()).config
    approvers = [g.strip() for g in (cfg.get('approver_groups', 'faktury_zakupu') or '').split(',') if g.strip()]
    pairs = []
    for item in (cfg.get('invoice_write_fields', 'remarks=opis_merytoryczny, project=projekt') or '').split(','):
        if '=' in item:
            field, path = (part.strip() for part in item.split('=', 1))
            if field and path:
                pairs.append((field, path))
    return InvoiceConfig(
        doctype=cfg.get('invoice_doctype', 'Purchase Invoice'),
        process_key=cfg.get('invoice_process_key', DEFAULT_PROCESS_KEY),
        describer_group=cfg.get('invoice_describer_group', 'faktury_opis'),
        approver_group=approvers[0] if approvers else 'faktury_zakupu',
        accept=(cfg.get('invoice_accept', 'workflow') or 'workflow').strip().lower(),
        approve_action=cfg.get('invoice_approve_action', 'Zatwierdź'),
        reject_action=(cfg.get('invoice_reject_action', '') or '').strip(),
        write_fields=tuple(pairs),
        ksef_field=(cfg.get('invoice_ksef_field', '') or '').strip(),
        webhook_secret=(cfg.get('invoice_webhook_secret', '') or '').strip(),
        base_url=(cfg.get('base_url', '') or '').rstrip('/'),
    )


def start_data(doc: Mapping[str, Any], cfg: InvoiceConfig) -> dict[str, Any]:
    """Dane startowe sprawy z dokumentu ERPNext (pola formularza modelu)."""
    segment = cfg.doctype.strip().lower().replace(' ', '-')
    data = {
        'faktura_erp': doc.get('name'),
        'dostawca': doc.get('supplier_name') or doc.get('supplier'),
        'nip_dostawcy': doc.get('tax_id') or '',
        'numer_faktury': doc.get('bill_no') or '',
        'data_wystawienia': doc.get('bill_date') or doc.get('posting_date') or '',
        'termin_platnosci': doc.get('due_date') or '',
        'kwota_netto': doc.get('net_total'),
        'kwota_brutto': doc.get('grand_total'),
        'waluta': doc.get('currency') or '',
        'numer_ksef': (doc.get(cfg.ksef_field) or '') if cfg.ksef_field else '',
        'link_erp': f"{cfg.base_url}/app/{segment}/{doc.get('name')}" if cfg.base_url else '',
        'status_erp': 'w obiegu',
        # grupy zadań (candidateGroups="${...}" w BPMN) - z konfiguracji wdrożenia
        'grupa_opisu': cfg.describer_group,
        'grupa_akceptacji': cfg.approver_group,
    }
    return {key: value for key, value in data.items() if value is not None}


def existing_instance(db: Session, process_key: str, invoice_name: str) -> ProcessInstance | None:
    return db.execute(
        select(ProcessInstance)
        .join(ProcessModelVersion, ProcessInstance.process_model_version_id == ProcessModelVersion.id)
        .join(ProcessModel, ProcessModelVersion.process_model_id == ProcessModel.id)
        .where(ProcessModel.key == process_key, ProcessInstance.business_key == invoice_name,
               ProcessInstance.status != ProcessInstanceStatus.CANCELLED)
    ).scalars().first()


@dataclass(frozen=True)
class StartResult:
    invoice: str
    status: str                # started | exists | skipped
    instance_id: str | None = None
    detail: str = ''


def start_for_invoice(db: Session, invoice_name: str, actor: CurrentUser, *,
                      doc: Mapping[str, Any] | None = None, connector=None) -> StartResult:
    """Sprawa obiegu dla faktury `invoice_name` (dokument czytany z ERPNext)."""
    from wf.services.process_service import get_service
    connector = connector or _erp()
    cfg = config(connector)
    existing = existing_instance(db, cfg.process_key, invoice_name)
    if existing is not None:
        return StartResult(invoice_name, 'exists', str(existing.id))
    doc = doc or connector.get_doc(cfg.doctype, invoice_name)
    if int(doc.get('docstatus') or 0) != 0:
        return StartResult(invoice_name, 'skipped', detail='faktura nie jest szkicem (docstatus != 0)')
    service = get_service()
    version = service.published_version(db, cfg.process_key)
    data = start_data(doc, cfg)
    title = ' – '.join(p for p in (f"Faktura {data.get('numer_faktury') or invoice_name}",
                                   str(data.get('dostawca') or '')) if p)
    instance = service.start_instance(db, version, title=title, business_key=invoice_name,
                                      data=data, actor=actor)
    logger.info('Faktura %s: sprawa %s', invoice_name, instance.id)
    return StartResult(invoice_name, 'started', str(instance.id))


def sync_drafts(db: Session, actor: CurrentUser, *, limit: int = 200, dry_run: bool = False,
                connector=None) -> list[StartResult]:
    """Uzgadnianie: szkice faktur w ERPNext bez sprawy w hubie."""
    connector = connector or _erp()
    cfg = config(connector)
    rows = connector.get_list(cfg.doctype, filters=[['docstatus', '=', 0]], fields=_FIELDS,
                              limit=limit)
    results = []
    for row in rows:
        name = row['name']
        if existing_instance(db, cfg.process_key, name) is not None:
            results.append(StartResult(name, 'exists'))
        elif dry_run:
            results.append(StartResult(name, 'would-start'))
        else:
            results.append(start_for_invoice(db, name, actor, connector=connector))
    return results


# --- kroki systemowe ----------------------------------------------------------

def _value(data: Mapping[str, Any], path: str) -> Any:
    node: Any = data
    for part in path.split('.'):
        if not isinstance(node, Mapping):
            return None
        node = node.get(part)
    return node


@service_tasks.handler('erpnext.faktura_akceptacja')
def accept_invoice(ctx: service_tasks.ServiceContext) -> service_tasks.ServiceResult:
    name = ctx.data.get('faktura_erp')
    if not name:
        raise service_tasks.ServiceTaskError('brak faktura_erp w danych sprawy')
    connector = _erp()
    cfg = config(connector)
    try:
        doc = connector.get_doc(cfg.doctype, name)
        if int(doc.get('docstatus') or 0) == 0:
            values = {field: _value(ctx.data, path) for field, path in cfg.write_fields}
            values = {field: value for field, value in values.items() if value not in (None, '')}
            if values:
                connector.update_doc(cfg.doctype, name, values)
            if cfg.accept == 'workflow' and cfg.approve_action:
                connector.apply_workflow_action(cfg.doctype, name, cfg.approve_action)
            elif cfg.accept == 'submit':
                connector.submit_doc(cfg.doctype, name)
    except ConnectorError as exc:
        raise service_tasks.ServiceTaskError(f'ERPNext ({name}): {exc}') from exc
    return service_tasks.done({'status_erp': 'zaakceptowana'})


@service_tasks.handler('erpnext.faktura_odrzucenie')
def reject_invoice(ctx: service_tasks.ServiceContext) -> service_tasks.ServiceResult:
    name = ctx.data.get('faktura_erp')
    connector = _erp()
    cfg = config(connector)
    if name and cfg.reject_action:
        try:
            if int(connector.get_doc(cfg.doctype, name).get('docstatus') or 0) == 0:
                connector.apply_workflow_action(cfg.doctype, name, cfg.reject_action)
        except ConnectorError as exc:
            raise service_tasks.ServiceTaskError(f'ERPNext ({name}): {exc}') from exc
    return service_tasks.done({'status_erp': 'odrzucona'})
