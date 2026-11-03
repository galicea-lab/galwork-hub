# coding: utf-8

"""
Konektor do n8n (https://docs.n8n.io/api/).

Szkielet zgodny z wzorcem connector/base.py.
n8n udostępnia dwa niezależne sposoby integracji, oba wsparte tutaj:
  * REST API (zarządzanie: lista/uruchamianie workflow) - nagłówek
    `X-N8N-API-KEY`,
  * webhooki (wyzwalanie konkretnego workflow z zewnątrz) - zwykły POST
    na URL webhooka, bez tokenu API.

Konfiguracja - sekcja [n8n] w config/config.ini:
    base_url=https://n8n.example.com
    api_key=...
"""

from __future__ import annotations

import requests

from connector.base import BaseConnector, ConnectorError


class N8nConnector(BaseConnector):
    name = "n8n"

    def connect(self) -> requests.Session:
        if self._client is None:
            base_url = self.config.require("base_url")
            api_key = self.config.require("api_key")
            session = requests.Session()
            session.headers.update({
                "X-N8N-API-KEY": api_key,
                "Accept": "application/json",
            })
            session.base_url = base_url.rstrip("/")  # type: ignore[attr-defined]
            self._client = session
        return self._client

    def test_connection(self) -> bool:
        try:
            self.list_workflows(limit=1)
            return True
        except Exception as e:
            self.logger.warning("n8n test_connection nieudany: %s", e)
            return False

    def _url(self, path: str) -> str:
        session = self.connect()
        return f"{session.base_url}/{path.lstrip('/')}"  # type: ignore[attr-defined]

    def list_workflows(self, *, active: bool | None = None, limit: int = 50) -> list[dict]:
        """GET /api/v1/workflows."""
        session = self.connect()
        params: dict[str, object] = {"limit": limit}
        if active is not None:
            params["active"] = active
        resp = session.get(self._url("/api/v1/workflows"), params=params, timeout=30)
        if not resp.ok:
            raise ConnectorError(f"n8n list_workflows -> {resp.status_code}: {resp.text}")
        return resp.json().get("data", [])

    def run_workflow(self, workflow_id: str, data: dict | None = None) -> dict:
        """POST /api/v1/workflows/<id>/run - uruchomienie workflow z danymi wejściowymi."""
        session = self.connect()
        resp = session.post(self._url(f"/api/v1/workflows/{workflow_id}/run"),
                             json={"data": data or {}}, timeout=60)
        if not resp.ok:
            raise ConnectorError(f"n8n run_workflow({workflow_id}) -> {resp.status_code}: {resp.text}")
        return resp.json()

    def trigger_webhook(self, webhook_path: str, data: dict | None = None,
                         *, method: str = "POST") -> dict:
        """Wywołuje webhook n8n (ścieżka zdefiniowana w node Webhook danego
        workflow), np. webhook_path='galwork/wf-started'."""
        url = self._url(f"/webhook/{webhook_path.lstrip('/')}")
        resp = requests.request(method, url, json=data or {}, timeout=60)
        if not resp.ok:
            raise ConnectorError(f"n8n trigger_webhook({webhook_path}) -> {resp.status_code}: {resp.text}")
        return resp.json() if resp.content else {}
