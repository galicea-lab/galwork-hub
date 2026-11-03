# wf/services/erpnext_invoice_service.py
# coding: utf-8
"""
Faktury zakupu z ERPNext oczekujące na akcję workflow "Zatwierdź" - widget
"Faktury do zatwierdzenia" na pulpicie galwork-dashboard.

Decyzja architektoniczna (2026-10-18): decyzja "Zatwierdź" zostaje
WYŁĄCZNIE w ERPNext (ten workflow i jego konsekwencje księgowe już tam
istnieją i działają) - ten moduł tylko odpytuje ERPNext o listę faktur w
stanie(-ach) poprzedzających "Zatwierdź" (`[erpnext] invoice_pending_states`
w config/config.ini), wywołuje tę samą akcję przez standardowy workflow
engine Frappe (`approve()` -> `ErpNextConnector.apply_workflow_action`) i
buduje link do konkretnego dokumentu w ERPNext.

Autoryzacja (2026-10-18): połączenie z ERPNext idzie na koncie
serwisowym (`[erpnext] api_key`/`api_secret`), więc Frappe sprawdzi
uprawnienia TEGO konta, nie zalogowanego w galwork człowieka - kto w galwork
MOŻE wywołać `approve()` kontroluje więc `require_invoice_approver` w
wf/routers/erpnext_invoice_api.py (grupa Keycloak z `[erpnext]
approver_groups`, domyślnie `faktury_zakupu`), zanim prośba dotrze do tego
modułu.

Nazwy stanu/pola/akcji workflow są w ERPNext konfigurowalne per-instalacja
(ten sam Frappe, różne workflow), więc nie są zahardkodowane w kodzie - zob.
`[erpnext]` w config/config.ini.
"""

from __future__ import annotations

from connector.base import translate_network_errors
from connector.erpnext import get_connector
from wf.schemas.wf_schemas import PurchaseInvoicePendingApprovalPage, PurchaseInvoiceSummary

_STANDARD_FIELDS = [
    "name",
    "supplier",
    "supplier_name",
    "bill_no",
    "grand_total",
    "currency",
    "posting_date",
    "due_date",
]


def _invoice_config() -> tuple[str, str, list[str], str, list[str]]:
    """(doctype, workflow_field, pending_states, approve_action, extra_fields)
    - czytane z sekcji [erpnext] przez ten sam `ConnectorConfig`, z którego
    korzysta `ErpNextConnector` (env > config.ini, zob. connector/base.py)."""
    connector = get_connector()
    cfg = connector.config
    doctype = cfg.get("invoice_doctype", "Purchase Invoice")
    workflow_field = cfg.get("invoice_workflow_field", "workflow_state")
    pending_raw = cfg.get("invoice_pending_states", "Pending Approval")
    pending_states = [s.strip() for s in pending_raw.split(",") if s.strip()]
    approve_action = cfg.get("invoice_approve_action", "Zatwierdź")
    extra_raw = cfg.get("invoice_extra_fields", "")
    extra_fields = [s.strip() for s in extra_raw.split(",") if s.strip()]
    return doctype, workflow_field, pending_states, approve_action, extra_fields


def _doctype_url_segment(doctype: str) -> str:
    """"Purchase Invoice" -> "purchase-invoice" (konwencja URL Frappe Desk:
    `/app/<doctype-w-kebab-case>/<name>`)."""
    return doctype.strip().lower().replace(" ", "-")


def list_pending_approval(limit: int = 20) -> PurchaseInvoicePendingApprovalPage:
    """Faktury zakupu w stanie(-ach) poprzedzających "Zatwierdź".

    Podnosi `connector.base.ConnectorConfigError` (sekcja [erpnext] bez
    `base_url`/`api_key`/`api_secret`) albo `ConnectorError` (ERPNext
    nieosiągalny/odpowiedział błędem) - router tłumaczy to na 503/502, tak
    jak reszta modułu wf (zob. wf/routers/signius_api.py).
    """
    doctype, workflow_field, pending_states, _approve_action, extra_fields = _invoice_config()
    if not pending_states:
        return PurchaseInvoicePendingApprovalPage(items=[], total=0)

    connector = get_connector()
    fields = [*_STANDARD_FIELDS, workflow_field, *extra_fields]
    base_url = connector.config.require("base_url").rstrip("/")

    with translate_network_errors(f"ERPNext {doctype}"):
        rows = connector.get_list(
            doctype,
            filters=[[workflow_field, "in", pending_states]],
            fields=fields,
            limit=limit,
        )
    items = [_to_summary(row, doctype, workflow_field, extra_fields, base_url) for row in rows]
    return PurchaseInvoicePendingApprovalPage(items=items, total=len(items))


def approve(name: str) -> PurchaseInvoiceSummary:
    """Wywołuje akcję workflow `[erpnext] invoice_approve_action` ("Zatwierdź")
    na fakturze `name` przez standardowy engine Frappe
    (`frappe.model.workflow.apply_workflow`) i zwraca jej stan po akcji.

    UWAGA: brak tu sprawdzenia, że `name` jest akurat w jednym z
    `invoice_pending_states` - Frappe i tak odrzuci nielegalne przejście
    workflow (np. próbę zatwierdzenia już zatwierdzonej faktury) własnym
    `ConnectorError`; nie duplikujemy tej walidacji po stronie huba.
    Autoryzacja KTO może to wywołać jest sprawdzana wcześniej, w routerze
    (`require_invoice_approver`), nie tutaj.
    """
    doctype, workflow_field, _pending_states, approve_action, extra_fields = _invoice_config()
    connector = get_connector()
    base_url = connector.config.require("base_url").rstrip("/")

    with translate_network_errors(f"ERPNext {doctype}"):
        connector.apply_workflow_action(doctype, name, approve_action)
        row = connector.get_doc(doctype, name)
    return _to_summary(row, doctype, workflow_field, extra_fields, base_url)


def _to_summary(
    row: dict,
    doctype: str,
    workflow_field: str,
    extra_fields: list[str],
    base_url: str,
) -> PurchaseInvoiceSummary:
    name = row["name"]
    supplier_name = row.get("supplier_name") or row.get("supplier")
    bill_no = row.get("bill_no")
    title = f"{supplier_name or name}" + (f" ({bill_no})" if bill_no else "")
    return PurchaseInvoiceSummary(
        name=name,
        title=title,
        supplier=row.get("supplier"),
        supplier_name=supplier_name,
        bill_no=bill_no,
        grand_total=row.get("grand_total"),
        currency=row.get("currency"),
        posting_date=row.get("posting_date"),
        due_date=row.get("due_date"),
        workflow_state=row.get(workflow_field, ""),
        url=f"{base_url}/app/{_doctype_url_segment(doctype)}/{name}",
        extra={field: row.get(field) for field in extra_fields},
    )
