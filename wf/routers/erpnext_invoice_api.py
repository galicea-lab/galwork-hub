# wf/routers/erpnext_invoice_api.py
# coding: utf-8
"""
Widget "Faktury do zatwierdzenia" (galwork-dashboard) - faktury zakupu z
ERPNext w stanie(-ach) workflow poprzedzających "Zatwierdź", plus akcja
wywołująca samo "Zatwierdź" (2026-10-18).

Odczyt (`GET .../pending-approval`) jest dostępny dla każdego zalogowanego
(ten sam `get_current_wf_user` co reszta modułu wf - widoczność "mam coś do
zrobienia" nie jest tu niczym chronionym). Akcja (`POST .../approve`)
wymaga dodatkowo `require_invoice_approver`: połączenie z ERPNext idzie na
koncie serwisowym (`[erpnext] api_key`/`api_secret`), więc Frappe sprawdzi
uprawnienia TEGO konta, nie klikającego w galwork człowieka - musimy więc
sami sprawdzić w hubie, że dany użytkownik jest w grupie Keycloak z
`[erpnext] approver_groups` (domyślnie `faktury_zakupu` - podgrupa grupy
`erpnext`, dopasowanie po leściu ścieżki, jak `ADMIN_GROUPS` w
wf/dependencies.py), zanim wywołamy cokolwiek w ERPNext.

Błędy tłumaczone tak jak reszta modułu wf (zob. wf/routers/signius_api.py):
`ConnectorConfigError` -> 503 ("nieskonfigurowane"), `ConnectorError` -> 502
(ERPNext odpowiedział błędem/nieosiągalny).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from connector.base import ConnectorConfigError, ConnectorError
from connector.erpnext import get_connector
from wf.dependencies import get_current_wf_user
from wf.schemas.wf_schemas import CurrentUser, PurchaseInvoicePendingApprovalPage, PurchaseInvoiceSummary
from wf.services import erpnext_invoice_service

router = APIRouter(prefix="/erpnext", tags=["erpnext"])


def require_invoice_approver(current_user: CurrentUser = Depends(get_current_wf_user)) -> CurrentUser:
    """Zależność FastAPI: zalogowany użytkownik w jednej z `[erpnext]
    approver_groups` (zob. docstring modułu). Wzorowana na
    `wf.dependencies.require_process_admin` - tam grupy są konfigurowane w
    `[keycloak]` (ustawienie całego huba), tu w `[erpnext]` (konfiguracja
    jednego konektora), stąd osobna funkcja, nie reużycie tamtej."""
    raw = get_connector().config.get("approver_groups", "")
    approver_groups = {g.strip().lower() for g in raw.split(",") if g.strip()}
    if not approver_groups:
        raise HTTPException(
            status_code=503,
            detail="Zatwierdzanie faktur wyłączone: ustaw [erpnext] approver_groups w config/config.ini",
        )
    principals = {v.lstrip("/").split("/")[-1].lower() for v in current_user.groups | current_user.roles}
    if not approver_groups & principals:
        raise HTTPException(
            status_code=403,
            detail=f"Brak uprawnień do zatwierdzania faktur (wymagana grupa: {', '.join(sorted(approver_groups))})",
        )
    return current_user


@router.get("/purchase-invoices/pending-approval", response_model=PurchaseInvoicePendingApprovalPage)
def get_pending_approval_invoices(
    limit: int = Query(default=20, ge=1, le=100),
    _current_user: CurrentUser = Depends(get_current_wf_user),
):
    """`GET /v1/wf/erpnext/purchase-invoices/pending-approval?limit=...`"""
    try:
        return erpnext_invoice_service.list_pending_approval(limit=limit)
    except ConnectorConfigError as exc:
        raise HTTPException(status_code=503, detail=f"ERPNext nie skonfigurowany: {exc}") from exc
    except ConnectorError as exc:
        raise HTTPException(status_code=502, detail=f"ERPNext: {exc}") from exc


@router.post("/purchase-invoices/{name}/approve", response_model=PurchaseInvoiceSummary)
def approve_invoice(
    name: str,
    _current_user: CurrentUser = Depends(require_invoice_approver),
):
    """`POST /v1/wf/erpnext/purchase-invoices/{name}/approve` - wywołuje
    `[erpnext] invoice_approve_action` ("Zatwierdź") na fakturze `name`."""
    try:
        return erpnext_invoice_service.approve(name)
    except ConnectorConfigError as exc:
        raise HTTPException(status_code=503, detail=f"ERPNext nie skonfigurowany: {exc}") from exc
    except ConnectorError as exc:
        raise HTTPException(status_code=502, detail=f"ERPNext: {exc}") from exc
