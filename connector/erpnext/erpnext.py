# coding: utf-8

"""
Konektor do ERPNext (https://docs.erpnext.com/, REST API Frappe).

Szkielet zgodny z wzorcem connector/base.py 
Uwierzytelnianie: token API (api_key:api_secret) wysyłany w nagłówku
Authorization - zob. https://frappeframework.com/docs/user/en/api/rest.

Konfiguracja - sekcja [erpnext] w config/config.ini:
    base_url=https://erp.example.com
    api_key=...
    api_secret=...
"""

from __future__ import annotations

import json
import threading
from typing import Any

import requests

from connector.base import BaseConnector, ConnectorError


class ErpNextConnector(BaseConnector):
    name = "erpnext"

    def connect(self) -> requests.Session:
        if self._client is None:
            base_url = self.config.require("base_url")
            api_key = self.config.require("api_key")
            api_secret = self.config.require("api_secret")
            session = requests.Session()
            session.headers.update({
                "Authorization": f"token {api_key}:{api_secret}",
                "Accept": "application/json",
            })
            session.base_url = base_url.rstrip("/")  # type: ignore[attr-defined]
            self._client = session
        return self._client

    def test_connection(self) -> bool:
        try:
            self.get_list("User", limit=1)
            return True
        except Exception as e:
            self.logger.warning("ERPNext test_connection nieudany: %s", e)
            return False

    def _url(self, path: str) -> str:
        session = self.connect()
        return f"{session.base_url}/{path.lstrip('/')}"  # type: ignore[attr-defined]

    def get_list(self, doctype: str, *, filters: list | dict | None = None,
                 fields: list[str] | None = None, limit: int = 20) -> list[dict]:
        """GET /api/resource/<doctype> - lista rekordów danego typu dokumentu."""
        session = self.connect()
        params: dict[str, Any] = {"limit_page_length": limit}
        if filters is not None:
            # Frappe REST (frappe/api/v1.py::document_list) robi
            # `json.loads(frappe.form_dict["filters"])`.
            # UWAGA! `str(filters)` daje Python-owy repr (apostrofy) nie będący poprawnym JSON-em
            params["filters"] = json.dumps(filters)
        if fields is not None:
            params["fields"] = json.dumps(fields)
        resp = session.get(self._url(f"/api/resource/{doctype}"), params=params, timeout=30)
        if not resp.ok:
            raise ConnectorError(f"ERPNext get_list({doctype}) -> {resp.status_code}: {resp.text}")
        return resp.json().get("data", [])

    def get_doc(self, doctype: str, name: str) -> dict:
        """GET /api/resource/<doctype>/<name> - pojedynczy dokument."""
        session = self.connect()
        resp = session.get(self._url(f"/api/resource/{doctype}/{name}"), timeout=30)
        if not resp.ok:
            raise ConnectorError(f"ERPNext get_doc({doctype}, {name}) -> {resp.status_code}: {resp.text}")
        return resp.json().get("data", {})

    def create_doc(self, doctype: str, data: dict) -> dict:
        """POST /api/resource/<doctype> - utworzenie dokumentu."""
        session = self.connect()
        resp = session.post(self._url(f"/api/resource/{doctype}"), json=data, timeout=30)
        if not resp.ok:
            raise ConnectorError(f"ERPNext create_doc({doctype}) -> {resp.status_code}: {resp.text}")
        return resp.json().get("data", {})

    def update_doc(self, doctype: str, name: str, data: dict) -> dict:
        """PUT /api/resource/<doctype>/<name> - aktualizacja dokumentu."""
        session = self.connect()
        resp = session.put(self._url(f"/api/resource/{doctype}/{name}"), json=data, timeout=30)
        if not resp.ok:
            raise ConnectorError(f"ERPNext update_doc({doctype}, {name}) -> {resp.status_code}: {resp.text}")
        return resp.json().get("data", {})

    def delete_doc(self, doctype: str, name: str) -> None:
        """DELETE /api/resource/<doctype>/<name>."""
        session = self.connect()
        resp = session.delete(self._url(f"/api/resource/{doctype}/{name}"), timeout=30)
        if not resp.ok:
            raise ConnectorError(f"ERPNext delete_doc({doctype}, {name}) -> {resp.status_code}: {resp.text}")

    def apply_workflow_action(self, doctype: str, name: str, action: str) -> dict:
        """POST /api/method/frappe.model.workflow.apply_workflow - wykonuje
        akcję przejścia workflow (np. "Zatwierdź") zdefiniowaną w Frappe dla
        danego Doctype, bez potrzeby własnej (custom) aplikacji Frappe -
        standardowy generyczny workflow engine Frappe to już wystawia.

        `apply_workflow(doc, action)` po stronie Frappe (frappe/model/
        workflow.py) chce w `doc` CAŁY aktualny dokument jako JSON (tak jak
        robi to klient Frappe Desk - `cur_frm.doc`), nie `{doctype, name}` -
        rekonstruuje z tego w pamięci `Document` (`frappe.get_doc(dict)` z
        już wypełnionymi polami NIE doczytuje reszty z bazy) i porównuje
        `modified` dla optymistycznej blokady (odpowiednik naszego
        `expected_lock_version`) - stąd `get_doc()` tuż przed wysłaniem,
        żeby mieć świeży snapshot, nie ten z chwili wylistowania w widgecie.

        Uwaga: wykonywane w kontekście użytkownika API (`api_key`/`api_secret`
        z sekcji [erpnext]), NIE zalogowanego usera galwork/Keycloak - Frappe
        sprawdzi uprawnienia tego konta serwisowego do akcji `action`, a nie
        konkretnej osoby klikającej w galwork. Kto w galwork MOŻE wywołać tę
        metodę kontroluje `require_invoice_approver` w
        wf/routers/erpnext_invoice_api.py.
        """
        current_doc = self.get_doc(doctype, name)
        session = self.connect()
        resp = session.post(
            self._url("/api/method/frappe.model.workflow.apply_workflow"),
            json={"doc": json.dumps(current_doc), "action": action},
            timeout=30,
        )
        if not resp.ok:
            raise ConnectorError(
                f"ERPNext apply_workflow_action({doctype}, {name}, {action}) -> "
                f"{resp.status_code}: {resp.text}"
            )
        return resp.json().get("message", resp.json().get("data", {}))


_connector: ErpNextConnector | None = None
_connector_lock = threading.Lock()


def get_connector() -> ErpNextConnector:
    """Konektor ERPNext współdzielony w obrębie procesu (singleton, jak
    `connector.paperless.connector.get_connector()`)."""
    global _connector
    if _connector is None:
        with _connector_lock:
            if _connector is None:
                _connector = ErpNextConnector()
    return _connector


def reset_connector() -> None:
    """Zamknij i zapomnij konektora (testy, przełączenie konfiguracji)."""
    global _connector
    with _connector_lock:
        if _connector is not None:
            _connector.close()
        _connector = None
