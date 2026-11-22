# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
# coding: utf-8
"""
Konektor do Taigi 6 (REST API, https://docs.taiga.io/api.html).

Uwierzytelnianie: konto techniczne (login i hasło) -> `POST /api/v1/auth`
zwraca `auth_token`, wysyłany potem jako `Authorization: Bearer ...`.
Po odpowiedzi 401 konektor loguje się ponownie, raz.

Konfiguracja - sekcja [taiga] w config/config.ini (hasło i sekrety
webhooków najlepiej w zmiennych środowiskowych TAIGA_PASSWORD,
TAIGA_WEBHOOK_SECRET_<id projektu>):
    base_url=https://taiga.example.com
    username=galwork-hub
    password=...
    technical_user_id=12          # id konta technicznego w Taidze
    webhook_url=https://hub.example.com/v1/integrations/taiga/webhook
    webhook_secret_7=...          # sekret webhooka projektu o id 7
    timeout=30

Ustalenia z instancji Taigi 6, na których opiera się konektor:
- lista bez stronicowania: nagłówek `x-disable-pagination: True`,
- lista zadań/US nie zawiera wartości atrybutów własnych - są pod
  `/{tasks|userstories}/custom-attributes-values/{id}`, kluczowane id
  atrybutu (webhook i timeline kluczują je nazwą),
- zmiana atrybutu własnego NIE zmienia `modified_date` obiektu, dlatego
  rekoncyliacja czyta timeline projektu, a nie listę zmienionych obiektów,
- timeline nie zawiera zdarzeń usunięcia,
- nagłówek `X-Pagination-Next` timeline'u bywa z `http://`, więc kolejne
  strony pobieramy parametrem `page`, a nie z tego adresu.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from typing import Any

import requests

from connector.base import BaseConnector, ConnectorError

# 2026-11-21: nazwy zasobów REST dla rodzajów obiektów obsługiwanych przez integrację
_KINDS = {
    "task": "tasks",
    "userstory": "userstories",
}


def _kind_resource(kind: str) -> str:
    try:
        return _KINDS[kind]
    except KeyError:
        raise ValueError(f"Nieobsługiwany rodzaj obiektu Taigi: {kind!r}") from None


class TaigaConnector(BaseConnector):
    name = "taiga"

    def __init__(self, config=None) -> None:
        super().__init__(config)
        self._token: str | None = None

    # --- połączenie -----------------------------------------------------

    def connect(self) -> requests.Session:
        if self._client is None:
            session = requests.Session()
            session.headers.update({"Accept": "application/json"})
            session.base_url = self.config.require("base_url").rstrip("/")  # type: ignore[attr-defined]
            self._client = session
        return self._client

    def close(self) -> None:
        self._token = None
        super().close()

    def test_connection(self) -> bool:
        try:
            self.request("GET", "/users/me")
            return True
        except Exception as e:
            self.logger.warning("Taiga test_connection nieudany: %s", e)
            return False

    @property
    def timeout(self) -> float:
        return float(self.config.get("timeout") or 30)

    @property
    def technical_user_id(self) -> int | None:
        value = self.config.get("technical_user_id")
        return int(value) if value else None

    def webhook_secret(self, taiga_project_id: int) -> str | None:
        """Sekret webhooka projektu (`webhook_secret_<id>`); None = brak."""
        return self.config.get(f"webhook_secret_{taiga_project_id}") or None

    def _url(self, path: str) -> str:
        session = self.connect()
        return f"{session.base_url}/api/v1/{path.lstrip('/')}"  # type: ignore[attr-defined]

    def _login(self) -> str:
        session = self.connect()
        resp = session.request(
            "POST", self._url("/auth"),
            json={"type": "normal",
                  "username": self.config.require("username"),
                  "password": self.config.require("password")},
            timeout=self.timeout,
        )
        if not resp.ok:
            raise ConnectorError(f"Taiga auth -> {resp.status_code}: {resp.text}")
        token = (resp.json() or {}).get("auth_token")
        if not token:
            raise ConnectorError("Taiga auth: brak auth_token w odpowiedzi")
        self._token = token
        return token

    def request(self, method: str, path: str, *, params: dict | None = None,
                json: Any = None, paginate: bool = False) -> Any:
        """Wywołanie REST z tokenem; `paginate=False` wyłącza stronicowanie list.

        Zwraca zdekodowany JSON (albo None dla pustej odpowiedzi, np. 204).
        """
        session = self.connect()
        headers = {}
        if not paginate:
            headers["x-disable-pagination"] = "True"
        for attempt in (1, 2):
            token = self._token or self._login()
            resp = session.request(
                method, self._url(path), params=params, json=json,
                headers={**headers, "Authorization": f"Bearer {token}"},
                timeout=self.timeout,
            )
            if resp.status_code == 401 and attempt == 1:
                # 2026-11-21: token wygasł - jedno ponowne logowanie
                self._token = None
                continue
            break
        if not resp.ok:
            raise ConnectorError(f"Taiga {method} {path} -> {resp.status_code}: {resp.text}")
        if resp.status_code == 204 or not resp.text:
            return None
        return resp.json()

    # --- projekty i osoby -------------------------------------------------

    def get_project_by_slug(self, slug: str) -> dict:
        return self.request("GET", "/projects/by_slug", params={"slug": slug})

    def get_project(self, project_id: int) -> dict:
        return self.request("GET", f"/projects/{project_id}")

    def list_memberships(self, project_id: int) -> list[dict]:
        """Członkowie projektu; z tokenem administratora projektu zawiera `user_email`."""
        return self.request("GET", "/memberships", params={"project": project_id}) or []

    # --- zadania i historyjki ---------------------------------------------

    def list_objects(self, kind: str, project_id: int, **filters: Any) -> list[dict]:
        """Lista zadań / US projektu (filtry Taigi, np. `modified_date__gte`)."""
        params = {"project": project_id, **filters}
        return self.request("GET", f"/{_kind_resource(kind)}", params=params) or []

    def get_object(self, kind: str, object_id: int) -> dict:
        return self.request("GET", f"/{_kind_resource(kind)}/{object_id}")

    def get_custom_attribute_values(self, kind: str, object_id: int) -> dict:
        """`{"attributes_values": {"<id atrybutu>": wartość}, "version": n, ...}`."""
        return self.request("GET", f"/{_kind_resource(kind)}/custom-attributes-values/{object_id}")

    def get_history(self, kind: str, object_id: int) -> list[dict]:
        """Historia zmian obiektu (404 dla obiektu usuniętego -> ConnectorError)."""
        return self.request("GET", f"/history/{kind}/{object_id}") or []

    def list_epics(self, project_id: int) -> list[dict]:
        return self.request("GET", "/epics", params={"project": project_id}) or []

    def list_epic_related_userstories(self, epic_id: int) -> list[dict]:
        return self.request("GET", f"/epics/{epic_id}/related_userstories") or []

    # --- atrybuty własne --------------------------------------------------

    def list_custom_attributes(self, kind: str, project_id: int) -> list[dict]:
        return self.request("GET", f"/{kind}-custom-attributes", params={"project": project_id}) or []

    def create_custom_attribute(self, kind: str, project_id: int, *, name: str,
                                attr_type: str = "number", description: str = "",
                                order: int | None = None) -> dict:
        body: dict[str, Any] = {"project": project_id, "name": name,
                                "type": attr_type, "description": description}
        if order is not None:
            body["order"] = order
        return self.request("POST", f"/{kind}-custom-attributes", json=body)

    # --- webhooki -------------------------------------------------------

    def list_webhooks(self, project_id: int) -> list[dict]:
        return self.request("GET", "/webhooks", params={"project": project_id}) or []

    def create_webhook(self, project_id: int, *, name: str, url: str, key: str) -> dict:
        return self.request("POST", "/webhooks",
                            json={"project": project_id, "name": name, "url": url, "key": key})

    def list_webhook_logs(self, webhook_id: int) -> list[dict]:
        """Log dostaw webhooka (Taiga trzyma tylko kilka ostatnich)."""
        return self.request("GET", "/webhooklogs", params={"webhook": webhook_id}) or []

    # --- timeline -------------------------------------------------------

    def iter_timeline(self, project_id: int, *, after_id: int | None = None,
                      max_pages: int = 50) -> Iterator[dict]:
        """Wpisy timeline'u projektu od najnowszego, aż do `after_id` (wyłącznie).

        Bez `after_id` czyta co najwyżej `max_pages` stron. Kolejność: od
        najnowszego - wywołujący, który zapisuje kursor, powinien zebrać
        wpisy i przetwarzać je od najstarszego.
        """
        for page in range(1, max_pages + 1):
            try:
                entries = self.request("GET", f"/timeline/project/{project_id}",
                                       params={"page": page}, paginate=True) or []
            except ConnectorError as exc:
                # 2026-11-21: Taiga zwraca 404 dla strony za ostatnią
                if " -> 404" in str(exc) and page > 1:
                    return
                raise
            if not entries:
                return
            for entry in entries:
                if after_id is not None and entry.get("id") is not None and entry["id"] <= after_id:
                    return
                yield entry
        self.logger.warning("Taiga timeline projektu %s: przerwano po %s stronach", project_id, max_pages)


_connector: TaigaConnector | None = None
_connector_lock = threading.Lock()


def get_connector() -> TaigaConnector:
    """Konektor Taigi współdzielony w obrębie procesu (jak connector.erpnext)."""
    global _connector
    if _connector is None:
        with _connector_lock:
            if _connector is None:
                _connector = TaigaConnector()
    return _connector


def reset_connector() -> None:
    """Zamknij i zapomnij konektor (testy, przełączenie konfiguracji)."""
    global _connector
    with _connector_lock:
        if _connector is not None:
            _connector.close()
        _connector = None
