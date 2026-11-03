# coding: utf-8

"""
Konektor do Paperless-ngx (https://docs.paperless-ngx.com/) - magazynu
dokumentów (OCR, pełny tekst, taksonomia korespondent/typ/tagi), REST API.

Kontekst: alternatywa dla WEBCON w obsłudze e-Doręczeń (zob. `analiza_edr.md`)
- `wf/models/wf.py::Document`/`DocumentVersion` już mają zaprojektowany,
  obecny w bazie cykl ingestu (`IngestStatus`: staged -> submitted ->
  done/failed/duplicate, `paperless_task_id`, `paperless_document_id`), ale
  do tej pory żaden kod nie wołał realnie Paperless API - ten moduł jest
  tym brakującym klientem. Implementuje wzorzec `connector/base.py::BaseConnector`
  - zob. `connector/frappe_education/connector.py` dla analogicznego
  konektora REST w tym repo (ten moduł jest budowany na tamtym wzorcu:
  `requests.Session` z nagłówkiem auth, `_url`/`_raise_for_status`).

Uwierzytelnianie: token API Paperless-ngx (Django REST Framework,
nagłówek `Authorization: Token <token>`) - to jest osobne poświadczenie
serwer-do-serwera, NIE to samo, co SSO Keycloak do interfejsu Paperless dla
ludzi (zob. `analiza_edr.md`, sekcja 3.4).

Konfiguracja - sekcja `[paperless]` w `config/config.ini`: `base_url`,
`token` (wymagane), `timeout` (opcjonalnie, sekundy, domyślnie 30),
`verify_tls` (opcjonalnie, domyślnie `true`), `staging_dir` (opcjonalnie,
domyślnie `./data/paperless_staging` - katalog na pliki czekające na
wysyłkę, odpowiadający `DocumentVersion.staging_path`), `api_version`
(opcjonalnie - numer wersji API Paperless-ngx wysyłany w nagłówku
`Accept: application/json; version=<N>`; Paperless-ngx wspiera tylko
pewien zakres wersji i przesuwa go w nowszych wydaniach - zbyt niski lub
zbyt wysoki numer skończy się odpowiedzią
`{"detail": "Invalid version in \"Accept\" header."}`; gdy nieustawione,
nagłówek nie przypina wersji i serwer używa swojej domyślnej) - albo
zmienne środowiskowe `PAPERLESS_BASE_URL`, `PAPERLESS_TOKEN`,
`PAPERLESS_TIMEOUT`, `PAPERLESS_VERIFY_TLS`, `PAPERLESS_STAGING_DIR`,
`PAPERLESS_API_VERSION`.

Nazwy pól odpowiedzi `/api/tasks/` (`related_document` vs parsowanie
`result`) nie są zweryfikowane na żywym Paperless-ngx - przy pierwszym
użyciu sprawdź rzeczywistą odpowiedź i dopasuj `_task_document_id()`
(zob. komentarz przy tej metodzie); to samo dotyczy nazwy pola czasu
dokumentu w `upload_document` (`created`).

Przepływ ingestu dokumentu (odpowiada `IngestStatus` w `wf/models/wf.py`):
  1. `stage_bytes(content, filename)` -> ścieżka lokalna (`staging_path`),
     stan `staged`.
  2. `upload_document(content, filename, ...)` -> `task_id` (stan `submitted`).
  3. `get_task(task_id)` - polling do czasu `status in ('SUCCESS', 'FAILURE')`.
     Sukces -> `paperless_document_id` (stan `done`); Paperless-ngx odrzuca
     duplikaty treści (ten sam plik) jako `FAILURE` z komunikatem
     zawierającym "existing document" - zob. `is_duplicate_task()` (stan
     `duplicate`).
  4. `get_document`/`update_document`/`download_document` - dalsza obsługa.

Korzystanie:

    from connector.paperless.connector import get_connector

    connector = get_connector()
    path = connector.stage_bytes(pdf_bytes, "pismo.pdf")
    task_id = connector.upload_document(
        connector.read_staged(path), "pismo.pdf", title="Pismo EDR 123",
        document_type_id=connector.get_or_create_document_type("e-Doręczenie"),
        tag_ids=[connector.get_or_create_tag("EDR")],
    )
    # ... później (polling) ...
    task = connector.get_task(task_id)
"""
from __future__ import annotations

import hashlib
import logging
import os
import threading
import uuid
from typing import Any

from connector.base import BaseConnector, ConnectorConfigError, ConnectorError

log = logging.getLogger("connector.paperless")

_DEFAULT_STAGING_DIR = os.path.join("data", "paperless_staging")

# Uwaga: Paperless-ngx zwraca "FAILURE" dla duplikatów treści, z
# komunikatem zawierającym jeden z tych fragmentów (zależnie od wersji) -
# rozróżniamy to od realnego błędu ingestu, żeby DocumentVersion dostał
# `duplicate`, a nie `failed` (zob. docstring modułu, pkt 3).
_DUPLICATE_MARKERS = ("existing document", "duplicate")


def _as_bool(value: str | None, default: bool) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


class PaperlessConnector(BaseConnector):
    """Sesja REST do Paperless-ngx (token API) + lokalny staging plików
    przed wysyłką (`DocumentVersion.staging_path`)."""

    name = "paperless"

    def __init__(self, config=None) -> None:
        super().__init__(config)
        self._lock = threading.Lock()
        self.base_url: str = ""
        self._type_cache: dict[tuple[str, str], int] = {}

    # ------------------------------------------------------------------
    # kontrakt BaseConnector
    # ------------------------------------------------------------------
    def connect(self) -> Any:
        """Zwraca (i tworzy przy pierwszym wywołaniu) `requests.Session`.

        Idempotentne - jak w `connector/frappe_education/connector.py`."""
        if self._client is not None:
            return self._client
        try:
            import requests  # import leniwy - jak w innych konektorach REST
        except ImportError as exc:  # pragma: no cover - zależność środowiskowa
            raise ConnectorError(
                "Brak pakietu requests - zainstaluj `requests`."
            ) from exc
        base_url = self.config.require("base_url")
        token = self.config.require("token")
        timeout_raw = self.config.get("timeout", "30")
        try:
            self._timeout = int(timeout_raw)
        except (TypeError, ValueError) as exc:
            raise ConnectorConfigError(
                f"[paperless] timeout musi być liczbą, jest {timeout_raw!r}"
            ) from exc
        self._verify_tls = _as_bool(self.config.get("verify_tls"), True)
        self._staging_dir = self.config.get("staging_dir", _DEFAULT_STAGING_DIR)
        api_version = self.config.get("api_version")
        accept = f"application/json; version={api_version}" if api_version else "application/json"
        with self._lock:
            if self._client is None:
                session = requests.Session()
                session.headers.update({
                    "Authorization": f"Token {token}",
                    "Accept": accept,
                })
                session.verify = self._verify_tls
                self.base_url = base_url.rstrip("/")
                self._client = session
        return self._client

    def test_connection(self) -> bool:
        """Health-check: pierwsza strona listy dokumentów (nie podnosi
        wyjątku)."""
        try:
            self._request("get", "/api/documents/", params={"page_size": 1})
            return True
        except Exception as exc:
            log.warning("paperless: test_connection() nieudany: %s", exc)
            return False

    def close(self) -> None:
        """Zamyka sesję `requests` (jeśli istnieje)."""
        client, self._client = self._client, None
        if client is None:
            return
        try:
            client.close()
        except Exception as exc:  # pragma: no cover
            log.warning("paperless: błąd przy zamykaniu sesji: %s", exc)

    # ------------------------------------------------------------------
    # transport
    # ------------------------------------------------------------------
    def _timeout_s(self) -> int:
        return getattr(self, "_timeout", 30)

    def _request(self, method: str, path: str, *, params: dict | None = None,
                 json_body: dict | None = None, files: dict | None = None,
                 data: dict | None = None, stream: bool = False) -> Any:
        session = self.connect()
        url = f"{self.base_url}{path}"
        resp = session.request(
            method, url, params=params, json=json_body, files=files, data=data,
            stream=stream, timeout=self._timeout_s(),
        )
        if not resp.ok:
            raise ConnectorError(
                f"paperless {method.upper()} {path} -> {resp.status_code}: {resp.text[:500]}"
            )
        return resp

    # ------------------------------------------------------------------
    # staging plików (DocumentVersion.staging_path)
    # ------------------------------------------------------------------
    def stage_bytes(self, content: bytes, filename: str) -> str:
        """Zapisuje `content` lokalnie i zwraca ścieżkę - do
        `DocumentVersion.staging_path` (stan `staged`, przed wysyłką)."""
        self.connect()  # upewnij się, że `_staging_dir` jest ustawione
        os.makedirs(self._staging_dir, exist_ok=True)
        safe_name = f"{uuid.uuid4()}_{os.path.basename(filename)}"
        path = os.path.join(self._staging_dir, safe_name)
        with open(path, "wb") as f:
            f.write(content)
        return path

    def read_staged(self, path: str) -> bytes:
        with open(path, "rb") as f:
            return f.read()

    def discard_staged(self, path: str) -> None:
        """Usuwa plik tymczasowy po udanym/ostatecznym przetworzeniu."""
        try:
            os.remove(path)
        except OSError as exc:
            log.warning("paperless: nie można usunąć %s: %s", path, exc)

    @staticmethod
    def sha256_of(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    # ------------------------------------------------------------------
    # ingest (`POST /api/documents/post_document/` + `/api/tasks/`)
    # ------------------------------------------------------------------
    def upload_document(self, content: bytes, filename: str, *,
                         title: str | None = None,
                         correspondent_id: int | None = None,
                         document_type_id: int | None = None,
                         tag_ids: list[int] | None = None,
                         created: str | None = None) -> str:
        """Wgrywa plik (konsumpcja asynchroniczna) -> `task_id`.

        Odpowiedź to goły UUID zadania (tekst, nie JSON) - zob. docstring
        modułu."""
        data: dict[str, Any] = {}
        if title:
            data["title"] = title
        if correspondent_id is not None:
            data["correspondent"] = correspondent_id
        if document_type_id is not None:
            data["document_type"] = document_type_id
        if tag_ids:
            data["tags"] = tag_ids
        if created:
            data["created"] = created
        files = {"document": (filename, content)}
        resp = self._request("post", "/api/documents/post_document/", data=data, files=files)
        task_id = resp.text.strip().strip('"')
        if not task_id:
            raise ConnectorError(f"paperless post_document({filename}): pusta odpowiedź zadania")
        return task_id

    def get_task(self, task_id: str) -> dict | None:
        """`GET /api/tasks/?task_id=...` -> pierwszy (jedyny) wpis albo
        `None`, jeśli zadanie jeszcze nie istnieje w kolejce."""
        resp = self._request("get", "/api/tasks/", params={"task_id": task_id})
        body = resp.json()
        tasks = body.get("results", body) if isinstance(body, dict) else body
        return tasks[0] if tasks else None

    @staticmethod
    def is_duplicate_task(task: dict) -> bool:
        result = str(task.get("result") or "").lower()
        return any(marker in result for marker in _DUPLICATE_MARKERS)

    @staticmethod
    def task_document_id(task: dict) -> int | None:
        """Id utworzonego dokumentu z wyniku zadania - pole `related_document`
        w nowszych wersjach Paperless-ngx; starsze API osadzają je w
        tekście `result` ("New document id 123 created") - stąd fallback.
        ZWERYFIKUJ na docelowej wersji przed użyciem produkcyjnym."""
        if task.get("related_document") is not None:
            return int(task["related_document"])
        result = str(task.get("result") or "")
        digits = "".join(ch for ch in result if ch.isdigit())
        return int(digits) if digits else None

    # ------------------------------------------------------------------
    # dokumenty
    # ------------------------------------------------------------------
    def get_document(self, document_id: int) -> dict:
        return self._request("get", f"/api/documents/{document_id}/").json()

    def update_document(self, document_id: int, fields: dict) -> dict:
        return self._request("patch", f"/api/documents/{document_id}/", json_body=fields).json()

    def download_document(self, document_id: int) -> bytes:
        resp = self._request("get", f"/api/documents/{document_id}/download/", stream=True)
        return resp.content

    def list_documents(self, *, tag_id: int | None = None, document_type_id: int | None = None,
                        correspondent_id: int | None = None, limit: int = 25,
                        ordering: str = "-created") -> dict:
        """`GET /api/documents/` - lista (paginowana przez Paperless-ngx samo,
        DRF-owy `page_size`) dokumentów filtrowana po tagu/typie/korespondencie.
        Zwraca całą odpowiedź (`count`/`results`) - `count` jest realną,
        dokładną liczbą (w przeciwieństwie do np. ERPNext get_list, gdzie
        liczymy tylko to, co faktycznie wróciło w jednej stronie)."""
        params: dict[str, Any] = {"page_size": limit, "ordering": ordering}
        if tag_id is not None:
            params["tags__id__in"] = tag_id
        if document_type_id is not None:
            params["document_type__id"] = document_type_id
        if correspondent_id is not None:
            params["correspondent__id"] = correspondent_id
        return self._request("get", "/api/documents/", params=params).json()

    # ------------------------------------------------------------------
    # taksonomia (korespondent/typ/tag po nazwie -> id, tworzy jeśli brak)
    # ------------------------------------------------------------------
    def get_or_create_correspondent(self, name: str) -> int:
        return self._get_or_create("correspondents", name)

    def get_or_create_document_type(self, name: str) -> int:
        return self._get_or_create("document_types", name)

    def get_or_create_tag(self, name: str) -> int:
        return self._get_or_create("tags", name)

    def find_tag_id(self, name: str) -> int | None:
        """Jak `get_or_create_tag`, ale bez tworzenia - do listowania
        (widget "kolejka dokumentów"), gdzie GET nie powinien mieć efektu
        ubocznego tworzenia tagu, którego ktoś użył w konfiguracji widgetu,
        zanim n8n/ktokolwiek faktycznie go nadał jakiemuś dokumentowi."""
        return self._find_id("tags", name)

    def _find_id(self, resource: str, name: str) -> int | None:
        found = self._request("get", f"/api/{resource}/", params={"name__iexact": name}).json()
        results = found.get("results", []) if isinstance(found, dict) else found
        return int(results[0]["id"]) if results else None

    def _get_or_create(self, resource: str, name: str) -> int:
        cache_key = (resource, name)
        if cache_key in self._type_cache:
            return self._type_cache[cache_key]
        existing = self._find_id(resource, name)
        if existing is not None:
            value = existing
        else:
            created = self._request("post", f"/api/{resource}/", json_body={"name": name}).json()
            value = int(created["id"])
        self._type_cache[cache_key] = value
        return value


_connector: PaperlessConnector | None = None
_connector_lock = threading.Lock()


def get_connector() -> PaperlessConnector:
    """Konektor Paperless-ngx współdzielony w obrębie procesu (singleton,
    jak `connector.signius.connector.get_connector()`)."""
    global _connector
    if _connector is None:
        with _connector_lock:
            if _connector is None:
                _connector = PaperlessConnector()
    return _connector


def reset_connector() -> None:
    """Zamknij i zapomnij konektora (testy, przełączenie konfiguracji)."""
    global _connector
    with _connector_lock:
        if _connector is not None:
            _connector.close()
        _connector = None


__all__ = [
    "PaperlessConnector",
    "get_connector",
    "reset_connector",
    "ConnectorError",
    "ConnectorConfigError",
]
