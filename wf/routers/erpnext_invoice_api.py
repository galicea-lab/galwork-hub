# wf/routers/erpnext_invoice_api.py
# coding: utf-8
"""
Widget "Faktury do zatwierdzenia" (galwork-dashboard) - faktury zakupu z
ERPNext w stanie(-ach) workflow poprzedzających "Zatwierdź", plus akcja
wywołująca samo "Zatwierdź" (2026-10-18).

Odczyt (`GET .../pending-approval`) - 2026-11-25: tylko grupy
`[erpnext] approver_groups` i `invoice_describer_group` oraz administratorzy
procesów (lista zawiera kontrahentów i kwoty; dawniej każdy zalogowany). Akcja (`POST .../approve`)
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

import base64
import hashlib
import hmac
import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from api.dependencies import get_db

from connector.base import ConnectorConfigError, ConnectorError
from connector.erpnext import get_connector
from wf.dependencies import get_current_wf_user, in_groups, is_process_admin
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


# 2026-11-25: odczyt listy faktur - akceptujący, opisujący i administratorzy
def require_invoice_reader(current_user: CurrentUser = Depends(get_current_wf_user)) -> CurrentUser:
    config = get_connector().config
    groups = {g.strip().lstrip("/").split("/")[-1].lower()
              for key in ("approver_groups", "invoice_describer_group")
              for g in (config.get(key) or "").split(",") if g.strip()}
    if is_process_admin(current_user) or in_groups(current_user, frozenset(groups)):
        return current_user
    raise HTTPException(status_code=403, detail="Brak uprawnień do faktur zakupu")


@router.get("/purchase-invoices/pending-approval", response_model=PurchaseInvoicePendingApprovalPage)
def get_pending_approval_invoices(
    limit: int = Query(default=20, ge=1, le=100),
    _current_user: CurrentUser = Depends(require_invoice_reader),
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


# BEGIN: ZAK.1 - informacja o nowej fakturze zakupu uruchamia obieg
# (wf/services/purchase_invoice_flow.py). Wywołuje ją n8n (nagłówek
# `X-Galwork-Webhook-Token` = `[erpnext] invoice_webhook_secret`) albo - bez n8n -
# „Webhook” ERPNext na `after_insert` faktury (nagłówek
# `X-Frappe-Webhook-Signature` = base64(HMAC-SHA256(sekret, treść))). Treść JSON:
# `{"name": "<nazwa faktury w ERPNext>"}` (Frappe: dokument z polem `name`).
# Dane faktury hub czyta konektorem ERPNext - treść służy tylko jako wskazanie.
def _notification_authorized(raw_body: bytes, secret: str, token: str | None, signature: str | None) -> bool:
    if token and hmac.compare_digest(token.strip(), secret):
        return True
    if signature:
        expected = base64.b64encode(hmac.new(secret.encode('utf-8'), raw_body, hashlib.sha256).digest()).decode()
        return hmac.compare_digest(expected, signature.strip())
    return False


@router.post("/invoices/notify", status_code=202)
async def notify_purchase_invoice(request: Request, db: Session = Depends(get_db)):
    """`POST /v1/wf/erpnext/invoices/notify` - nowa faktura zakupu w ERPNext."""
    from wf.services import purchase_invoice_flow
    from wf.services.process_service import FormRenderError, ProcessServiceError
    from wf.services.system_actor import SystemActorError, get_system_actor
    try:
        connector = get_connector()
        cfg = purchase_invoice_flow.config(connector)
    except ConnectorConfigError as exc:
        raise HTTPException(status_code=503, detail=f"ERPNext nie skonfigurowany: {exc}") from exc
    if not cfg.webhook_secret:
        raise HTTPException(status_code=503, detail="Ustaw [erpnext] invoice_webhook_secret")
    raw_body = await request.body()
    if not _notification_authorized(raw_body, cfg.webhook_secret,
                                    request.headers.get("x-galwork-webhook-token"),
                                    request.headers.get("x-frappe-webhook-signature")):
        raise HTTPException(status_code=403, detail="Niepoprawny token albo podpis")
    try:
        payload = json.loads(raw_body or b"{}")
    except ValueError:
        raise HTTPException(status_code=400, detail="Treść nie jest poprawnym JSON") from None
    doc = payload.get("doc") if isinstance(payload, dict) and isinstance(payload.get("doc"), dict) else payload
    name = str((doc or {}).get("name") or "").strip() if isinstance(doc, dict) else ""
    if not name:
        raise HTTPException(status_code=400, detail="Brak nazwy faktury (pole name)")
    try:
        actor = get_system_actor(db, display_name="ERPNext – faktury")
        result = purchase_invoice_flow.start_for_invoice(db, name, actor, connector=connector)
    except SystemActorError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ConnectorError as exc:
        db.rollback()
        raise HTTPException(status_code=502, detail=f"ERPNext: {exc}") from exc
    except (ProcessServiceError, FormRenderError) as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"invoice": result.invoice, "status": result.status,
            "process_instance_id": result.instance_id, "detail": result.detail}
# END

