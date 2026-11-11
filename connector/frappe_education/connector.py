# coding: utf-8

"""
Konektor do Frappe Education.

Konfiguracja - sekcja `[frappe_education]` w config/config.ini (albo
zmienne środowiskowe `FRAPPE_EDUCATION_BASE_URL`, `FRAPPE_EDUCATION_API_KEY`,
`FRAPPE_EDUCATION_API_SECRET`, `FRAPPE_EDUCATION_TIMEOUT`). Hasła/klucze
celowo nie są wpisane w repo.

Korzystanie:

    from connector.frappe_education.connector import get_connector, ConnectorError

    connector = get_connector()
    students = connector.get_list("Student", filters=[["enabled", "=", 1]])
"""

from __future__ import annotations

import json
import logging
import re
import threading
from typing import Any, Iterator

from connector.base import (
    BaseConnector,
    ConnectorConfigError,
    ConnectorError,
)
# wywołania w imieniu użytkownika portalu.
from connector.frappe_education.user_context import forward_user_headers

log = logging.getLogger("connector.frappe_education")


def _encode_list_param(value: Any) -> str | None:
    """JSON-enkoduje `filters`/`fields`/`order_by` (Frappe REST oczekuje JSON,
    nie Pythonowego `repr`/`str` - zob. uwaga w docstringu modułu)."""
    if value is None:
        return None
    return json.dumps(value)


# 2026-11-08 //--
class FrappeRequestError(ConnectorError):
    """Frappe odpowiedziało błędem.

    `str(błąd)` zawiera tylko operację, status HTTP i typ wyjątku Frappe - bez
    treści odpowiedzi, nazw dokumentów i ścieżek plików, bo trafia do logów, a
    może zawierać dane osobowe. Komunikat Frappe dla użytkownika (bez śladu
    stosu) jest w `user_message`; zob. `error_message()`.
    """

    def __init__(self, what: str, status_code: int, exc_type: str | None = None,
                 user_message: str | None = None) -> None:
        self.status_code = status_code
        self.exc_type = exc_type
        self.user_message = user_message
        suffix = f" {exc_type}" if exc_type else ""
        super().__init__(f"frappe_education {what} -> {status_code}{suffix}")


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text).strip()


def _frappe_error_details(resp: Any) -> tuple[str | None, str | None]:
    """`(exc_type, komunikat dla użytkownika)` z odpowiedzi błędu Frappe."""
    try:
        data = resp.json()
    except Exception:
        return None, None
    if not isinstance(data, dict):
        return None, None
    exc_type = data.get("exc_type")
    message = None
    try:
        messages = json.loads(data.get("_server_messages") or "[]")
        if messages:
            first = json.loads(messages[0]) if isinstance(messages[0], str) else messages[0]
            message = first.get("message") if isinstance(first, dict) else str(first)
    except (TypeError, ValueError):
        message = None
    if not message and data.get("exception"):
        # "frappe.exceptions.ValidationError: treść" -> "treść" (bez śladu stosu)
        last = str(data["exception"]).strip().splitlines()[-1]
        message = last.split(": ", 1)[1] if ": " in last else last
    return (str(exc_type) if exc_type else None), (_strip_html(message) if message else None)


def error_message(exc: BaseException) -> str:
    """Komunikat do odpowiedzi API: `user_message` z Frappe, jeśli jest
    (także w wyjątku-przyczynie), inaczej `str(exc)`. Nie do logów."""
    current: BaseException | None = exc
    while current is not None:
        message = getattr(current, "user_message", None)
        if message:
            return message
        current = current.__cause__
    return str(exc)
# --//


class FrappeEducationConnector(BaseConnector):
    """Sesja REST do instancji Frappe z zainstalowaną aplikacją `education`.

    Parametry z sekcji `[frappe_education]` pliku `config/config.ini`:
    `base_url`, `api_key`, `api_secret` (wymagane), `timeout` (opcjonalnie,
    sekundy, domyślnie 30).
    """

    name = "frappe_education"

    def __init__(self, config=None) -> None:
        super().__init__(config)
        self._lock = threading.Lock()
        self.base_url: str = ""

    # ------------------------------------------------------------------
    # kontrakt BaseConnector
    # ------------------------------------------------------------------
    def connect(self) -> Any:
        """Zwraca (i tworzy przy pierwszym wywołaniu) `requests.Session`.

        Idempotentne - kolejne wywołania oddają już istniejącą sesję, chyba
        że wcześniej wywołano `close()`/`reset_connector()`.
        """
        if self._client is not None:
            return self._client
        try:
            import requests  # import leniwy
        except ImportError as exc:  # pragma: no cover - zależność środowiskowa
            raise ConnectorError(
                "Brak pakietu requests - zainstaluj `requests`."
            ) from exc
        base_url = self.config.require("base_url")
        api_key = self.config.require("api_key")
        api_secret = self.config.require("api_secret")
        timeout_raw = self.config.get("timeout", "30")
        try:
            self._timeout = int(timeout_raw)
        except (TypeError, ValueError) as exc:
            raise ConnectorConfigError(
                f"[frappe_education] timeout musi być liczbą, jest {timeout_raw!r}"
            ) from exc
        with self._lock:
            if self._client is None:
                session = requests.Session()
                # nagłówki użytkownika portalu w czasie obsługi
                # jego żądania (zob. connector/frappe_education/user_context.py).
                session.auth = forward_user_headers
                session.headers.update({
                    "Authorization": f"token {api_key}:{api_secret}",
                    "Accept": "application/json",
                })
                self.base_url = base_url.rstrip("/")
                self._client = session
        return self._client

    def test_connection(self) -> bool:
        """Health-check: liczba rekordów `Student` (nie podnosi wyjątku)."""
        try:
            self.get_count("Student")
            return True
        except Exception as exc:
            log.warning("frappe_education: test_connection() nieudany: %s", exc)
            return False

    def close(self) -> None:
        """Zamyka sesję `requests` (jeśli istnieje)."""
        client, self._client = self._client, None
        if client is None:
            return
        try:
            client.close()
        except Exception as exc:  # pragma: no cover
            log.warning("frappe_education: błąd przy zamykaniu sesji: %s", exc)

    # ------------------------------------------------------------------
    # pomocnicze
    # ------------------------------------------------------------------
    def _url(self, path: str) -> str:
        self.connect()
        return f"{self.base_url}/{path.lstrip('/')}"

    def _timeout_s(self) -> int:
        return getattr(self, "_timeout", 30)

    def _raise_for_status(self, resp: Any, what: str) -> None:
        if not resp.ok:
            # bez treści odpowiedzi w komunikacie (trafia do logów).
            exc_type, user_message = _frappe_error_details(resp)
            raise FrappeRequestError(what, resp.status_code, exc_type, user_message)

    # ------------------------------------------------------------------
    # CRUD generyczny (/api/resource/<DocType>[/<name>])
    # ------------------------------------------------------------------
    def get_list(
        self,
        doctype: str,
        *,
        filters: list | dict | None = None,
        fields: list[str] | None = None,
        order_by: str | None = None,
        limit_page_length: int = 20,
        limit_start: int = 0,
    ) -> list[dict]:
        """`GET /api/resource/<doctype>` - strona listy rekordów."""
        session = self.connect()
        params: dict[str, Any] = {
            "limit_page_length": limit_page_length,
            "limit_start": limit_start,
        }
        encoded_filters = _encode_list_param(filters)
        if encoded_filters is not None:
            params["filters"] = encoded_filters
        encoded_fields = _encode_list_param(fields)
        if encoded_fields is not None:
            params["fields"] = encoded_fields
        if order_by:
            params["order_by"] = order_by
        resp = session.get(
            self._url(f"/api/resource/{doctype}"), params=params, timeout=self._timeout_s()
        )
        self._raise_for_status(resp, f"get_list({doctype})")
        return resp.json().get("data", [])

    def get_all(
        self,
        doctype: str,
        *,
        filters: list | dict | None = None,
        fields: list[str] | None = None,
        order_by: str | None = None,
        page_size: int = 200,
    ) -> Iterator[dict]:
        """Jak `get_list`, ale stronicuje automatycznie i zwraca wszystkie
        rekordy (generator) - potrzebne dla zapytań słownikowych/zbiorczych."""
        start = 0
        while True:
            page = self.get_list(
                doctype,
                filters=filters,
                fields=fields,
                order_by=order_by,
                limit_page_length=page_size,
                limit_start=start,
            )
            if not page:
                return
            yield from page
            if len(page) < page_size:
                return
            start += page_size

    def get_doc(self, doctype: str, name: str) -> dict:
        """`GET /api/resource/<doctype>/<name>` - pojedynczy dokument."""
        session = self.connect()
        resp = session.get(self._url(f"/api/resource/{doctype}/{name}"), timeout=self._timeout_s())
        # bez nazwy dokumentu (np. e-mail jako nazwa User).
        self._raise_for_status(resp, f"get_doc({doctype})")
        return resp.json().get("data", {})

    def create_doc(self, doctype: str, data: dict) -> dict:
        session = self.connect()
        resp = session.post(self._url(f"/api/resource/{doctype}"), json=data, timeout=self._timeout_s())
        self._raise_for_status(resp, f"create_doc({doctype})")
        return resp.json().get("data", {})

    def update_doc(self, doctype: str, name: str, data: dict) -> dict:
        session = self.connect()
        resp = session.put(self._url(f"/api/resource/{doctype}/{name}"), json=data, timeout=self._timeout_s())
        # bez nazwy dokumentu.
        self._raise_for_status(resp, f"update_doc({doctype})")
        return resp.json().get("data", {})

    def delete_doc(self, doctype: str, name: str) -> None:
        session = self.connect()
        resp = session.delete(self._url(f"/api/resource/{doctype}/{name}"), timeout=self._timeout_s())
        # bez nazwy dokumentu.
        self._raise_for_status(resp, f"delete_doc({doctype})")

    # ------------------------------------------------------------------
    # /api/method/... (RPC) i pliki
    # ------------------------------------------------------------------
    def call_method(self, method: str, params: dict | None = None, http_method: str = "GET") -> Any:
        """`GET`/`POST` `/api/method/<dotted.path>` - whitelisted RPC."""
        session = self.connect()
        url = self._url(f"/api/method/{method}")
        if http_method.upper() == "GET":
            resp = session.get(url, params=params or {}, timeout=self._timeout_s())
        else:
            resp = session.post(url, json=params or {}, timeout=self._timeout_s())
        self._raise_for_status(resp, f"call_method({method})")
        body = resp.json()
        return body.get("message", body)

    def get_count(self, doctype: str, filters: list | dict | None = None) -> int:
        """`frappe.client.get_count` - liczba rekordów (bez ściągania ich)."""
        params: dict[str, Any] = {"doctype": doctype}
        encoded_filters = _encode_list_param(filters)
        if encoded_filters is not None:
            params["filters"] = encoded_filters
        return int(self.call_method("frappe.client.get_count", params))

    def get_value(self, doctype: str, filters: list | dict, fieldname: str | list[str]):
        """`frappe.client.get_value` - jedna wartość/jeden rekord bez pobierania
        całego dokumentu."""
        params = {
            "doctype": doctype,
            "filters": _encode_list_param(filters),
            "fieldname": _encode_list_param(fieldname) if isinstance(fieldname, list) else fieldname,
        }
        result = self.call_method("frappe.client.get_value", params)
        return result

    def get_file_list(self, doctype: str, name: str) -> list[dict]:
        """Pliki dołączone do dokumentu (`File`, `attached_to_doctype`/
        `attached_to_name`) - zob. connector/frappe_education/wydruki.py."""
        return self.get_list(
            "File",
            filters=[
                ["attached_to_doctype", "=", doctype],
                ["attached_to_name", "=", name],
            ],
            fields=["name", "file_name", "file_url", "is_private", "creation", "owner"],
            order_by="creation desc",
            limit_page_length=100,
        )

    def get_file_content(self, file_url: str) -> bytes:
        """Ściąga treść pliku (publicznego `/files/...` albo prywatnego
        `/private/files/...`) tą samą sesją/tokenem - Frappe sprawdza
        uprawnienia do dokumentu, do którego plik jest podczepiony."""
        session = self.connect()
        resp = session.get(f"{self.base_url}{file_url}", timeout=self._timeout_s())
        # bez ścieżki pliku (nazwy plików bywają nazwiskami).
        self._raise_for_status(resp, "get_file_content()")
        return resp.content

    # 2026-11-06: PDF wydruku dokumentu (Print Format) - źródło dokumentu
    # do opieczętowania (edu/services/sealing.py).
    def get_print_pdf(
        self,
        doctype: str,
        name: str,
        print_format: str | None = None,
        *,
        letterhead: str | None = None,
        no_letterhead: bool = False,
    ) -> bytes:
        """`GET /api/method/frappe.utils.print_format.download_pdf` - PDF
        dokumentu w danym szablonie wydruku (bez `print_format`: szablon
        domyślny doctype'u, a bez niego "Standard")."""
        session = self.connect()
        params = {"doctype": doctype, "name": name, "no_letterhead": "1" if no_letterhead else "0"}
        if print_format:
            params["format"] = print_format
        if letterhead:
            params["letterhead"] = letterhead
        resp = session.get(
            self._url("/api/method/frappe.utils.print_format.download_pdf"),
            params=params,
            timeout=self._timeout_s(),
        )
        # bez nazwy dokumentu.
        self._raise_for_status(resp, f"get_print_pdf({doctype})")
        if not resp.content.startswith(b"%PDF"):
            raise ConnectorError(
                # bez nazwy dokumentu.
                f"frappe_education get_print_pdf({doctype}): odpowiedź nie jest PDF "
                f"({resp.headers.get('Content-Type', '?')})"
            )
        return resp.content

    def upload_file(
        self,
        doctype: str,
        name: str,
        filename: str,
        content: bytes,
        *,
        is_private: bool = True,
        fieldname: str | None = None,
        content_type: str = "application/octet-stream",
    ) -> dict:
        """`POST /api/method/upload_file` - wgrywa plik i podpina go do
        dokumentu (`File.attached_to_doctype`/`attached_to_name`).

        `fieldname`, jeśli podany, dodatkowo ustawia pole typu `Attach` na
        dokumencie (np. `signed_document` w `Student Correspondence`) w tym
        samym wywołaniu - nie trzeba potem robić osobnego `update_doc`.
        Potrzebne dla `wf/routers/signius_api.py` (odkładanie podpisanego PDF
        po stronie Frappe jako systemu rekordu)."""
        session = self.connect()
        data = {
            "doctype": doctype,
            "docname": name,
            "is_private": "1" if is_private else "0",
        }
        if fieldname:
            data["fieldname"] = fieldname
        files = {"file": (filename, content, content_type)}
        resp = session.post(
            self._url("/api/method/upload_file"),
            data=data,
            files=files,
            timeout=self._timeout_s(),
        )
        # bez nazwy dokumentu.
        self._raise_for_status(resp, f"upload_file({doctype})")
        return resp.json().get("message", {})


_connector: FrappeEducationConnector | None = None
_connector_lock = threading.Lock()


def get_connector() -> FrappeEducationConnector:
    """Konektor Frappe Education współdzielony w obrębie procesu (singleton,
    tak jak inne konektory dziedziczące po `connector.base.BaseConnector`)."""
    global _connector
    if _connector is None:
        with _connector_lock:
            if _connector is None:
                _connector = FrappeEducationConnector()
    return _connector


def reset_connector() -> None:
    """Zamknij i zapomnij konektora (testy, przełączenie konfiguracji)."""
    global _connector
    with _connector_lock:
        if _connector is not None:
            _connector.close()
        _connector = None


__all__ = [
    "FrappeEducationConnector",
    "get_connector",
    "reset_connector",
    "ConnectorError",
    "ConnectorConfigError",
]
