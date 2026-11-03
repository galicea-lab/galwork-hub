# coding: utf-8

"""
Konektor do Signius (https://www.signius.eu/) - polskiego dostawcy
kwalifikowanych podpisów elektronicznych, REST API
(`https://professional.signius.eu:8900/services/...`).

Zastępuje `connector/signius/signius.py` (skrypt CLI z argparse, własną
klasą konfiguracji `GlobalConfig(ConfigParser)` i trzema niedziałającymi/
zakomentowanymi wariantami wysyłki pliku - jeden z nich, `upload_pycurl_file`,
w ogóle nie działał, drugi, `upload_request_file`, base64-kodował treść
plików przed wysłaniem w `files=` co kończyło się błędem 500 po stronie
Signius - multipart ma przenosić surowe bajty, nie tekst base64). Ten
moduł implementuje wzorzec `connector/base.py::BaseConnector` - zob.
`connector/frappe_education/connector.py` dla analogicznego konektora REST
w tym repo.

Uwierzytelnianie: mTLS certyfikatem klienckim w formacie PKCS#12 (plik
.p12 + hasło) - Signius nie akceptuje tokenu API, tylko certyfikat wydany
dla konta. Transport: pakiet `requests_pkcs12` (rozszerzenie `requests` o
obsługę PKCS#12 - sam `requests` tego nie potrafi), import leniwy (jak
`requests` w `connector/frappe_education/connector.py`) - żeby brak tej
zależności nie blokował importu modułu w środowiskach, które z Signiusa
nie korzystają.

Stary skrypt łączył się z `verify=False` (bez weryfikacji łańcucha TLS).
Zostawiamy to jako domyślne zachowanie (`verify_tls` domyślnie `false`),
żeby nie złamać działającej integracji bez zrozumienia, czy certyfikat
serwera faktycznie miał problem z łańcuchem - ale jest to teraz jawny,
nazwany przełącznik w konfiguracji, nie zaszyty w kodzie `verify=False`.
Włącz `verify_tls=true`, gdy certyfikat `professional.signius.eu` okaże
się poprawny.

Konfiguracja - sekcja `[signius]` w `config/config.ini`: `p12_file`,
`p12_password` (wymagane), `base_url` (opcjonalnie, domyślnie
`https://professional.signius.eu:8900`), `timeout` (opcjonalnie, sekundy,
domyślnie 30), `verify_tls` (opcjonalnie, domyślnie `false`) - albo
zmienne środowiskowe `SIGNIUS_P12_FILE`, `SIGNIUS_P12_PASSWORD`,
`SIGNIUS_BASE_URL`, `SIGNIUS_TIMEOUT`, `SIGNIUS_VERIFY_TLS`.

Przepływ podpisu dokumentu:
  1. `create_folder(name)` - utworzenie folderu dokumentów (raz per sprawa/
     kategoria, albo jeden stały folder dla całej integracji).
  2. `upload_document(folder_id, filename, pdf_bytes)` -> `document_id`.
  3. `request_signature(document_id, signee_email, signee_phone, title)` -
     wysyła podpisującemu link/kod; Signius zwraca status dokumentu.
  4. odpytywanie `get_document_status`/`get_folder` do czasu
     `status == "SIGNED"` - to API nie ma webhooka (nie ma go w oryginalnym
     kliencie ani w jego komentarzach), więc po stronie SpiffWorkflow to
     będzie BPMN timer + service task pollujący, nie callback.
  5. `get_signed_document(document_id)` -> bajty podpisanego PDF.

Korzystanie:

    from connector.signius.connector import get_connector

    connector = get_connector()
    folder = connector.create_folder("korespondencja-dziekanat")
    doc = connector.upload_document(folder["documentFolderId"], "wniosek.pdf", pdf_bytes)
    connector.request_signature(doc["documentId"], "jan@example.com", "+48600000000", "Wniosek o urlop")
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from connector.base import BaseConnector, ConnectorConfigError, ConnectorError

log = logging.getLogger("connector.signius")

_DEFAULT_BASE_URL = "https://professional.signius.eu:8900"


def _as_bool(value: str | None, default: bool) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


class SigniusConnector(BaseConnector):
    """Sesja REST (mTLS/PKCS#12) do Signius Professional API.

    `requests_pkcs12` nie ma obiektu sesji analogicznego do
    `requests.Session` - każde wywołanie niesie certyfikat osobno. "Klient"
    (`self._client`) to więc po prostu zweryfikowany moduł `requests_pkcs12`
    plus zapamiętane dane certyfikatu; realne żądania idą przez `_request()`.
    """

    name = "signius"

    def __init__(self, config=None) -> None:
        super().__init__(config)
        self._lock = threading.Lock()
        self.base_url: str = ""

    # ------------------------------------------------------------------
    # kontrakt BaseConnector
    # ------------------------------------------------------------------
    def connect(self) -> Any:
        """Waliduje konfigurację i zwraca moduł `requests_pkcs12` (cache'owany
        w `self._client`). Idempotentne - jak w innych konektorach REST."""
        if self._client is not None:
            return self._client
        # Walidacja konfiguracji PRZED importem opcjonalnej zależności - inaczej
        # błąd konfiguracji (ConnectorConfigError) i brak pakietu w środowisku
        # (ConnectorError) mylą się w zależności od tego, czy akurat
        # requests_pkcs12 jest zainstalowany (zob. test_signius_connector.py).
        p12_file = self.config.require("p12_file")
        p12_password = self.config.require("p12_password")
        timeout_raw = self.config.get("timeout", "30")
        try:
            self._timeout = int(timeout_raw)
        except (TypeError, ValueError) as exc:
            raise ConnectorConfigError(
                f"[signius] timeout musi być liczbą, jest {timeout_raw!r}"
            ) from exc
        try:
            import requests_pkcs12  # import leniwy, zob. docstring modułu
        except ImportError as exc:  # pragma: no cover - zależność środowiskowa
            raise ConnectorError(
                "Brak pakietu requests_pkcs12 - zainstaluj `requests-pkcs12`."
            ) from exc
        with self._lock:
            if self._client is None:
                self.base_url = self.config.get("base_url", _DEFAULT_BASE_URL).rstrip("/")
                self._verify_tls = _as_bool(self.config.get("verify_tls"), False)
                self._p12_file = p12_file
                self._p12_password = p12_password
                self._client = requests_pkcs12
        return self._client

    def test_connection(self) -> bool:
        """Health-check: Signius nie ma dedykowanego endpointu `/ping` w tym
        API, więc odpytujemy nieistniejący folder - sam fakt otrzymania
        jakiejkolwiek odpowiedzi HTTP (nawet 404) dowodzi, że certyfikat
        PKCS#12 został zaakceptowany i serwer jest dostępny. Błąd
        transportowy (SSL/TLS, DNS, timeout) albo brak/błędna konfiguracja
        zwracają `False`."""
        try:
            self._request(
                "get",
                "/services/documents/document-folders/00000000-0000-0000-0000-000000000000",
                raise_on_error=False,
            )
            return True
        except Exception as exc:
            log.warning("signius: test_connection() nieudany: %s", exc)
            return False

    def _timeout_s(self) -> int:
        return getattr(self, "_timeout", 30)

    # ------------------------------------------------------------------
    # transport
    # ------------------------------------------------------------------
    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict | None = None,
        files: dict | None = None,
        stream: bool = False,
        raise_on_error: bool = True,
    ) -> Any:
        client = self.connect()
        headers = {"accept": "*/*"}
        kwargs: dict[str, Any] = {
            "pkcs12_filename": self._p12_file,
            "pkcs12_password": self._p12_password,
            "verify": self._verify_tls,
            "timeout": self._timeout_s(),
            "headers": headers,
        }
        if json_body is not None:
            kwargs["json"] = json_body
            headers["Content-Type"] = "application/json"
        if files is not None:
            kwargs["files"] = files
        if stream:
            kwargs["stream"] = True
        url = f"{self.base_url}{path}"
        resp = getattr(client, method)(url, **kwargs)
        if raise_on_error and not resp.ok:
            raise ConnectorError(
                f"signius {method.upper()} {path} -> {resp.status_code}: {resp.text[:500]}"
            )
        return resp

    # ------------------------------------------------------------------
    # operacje domenowe
    # ------------------------------------------------------------------
    def create_folder(self, name: str) -> dict:
        """`POST /services/document-folders` -> `{"documentFolderId": "..."}`."""
        resp = self._request("post", "/services/document-folders", json_body={"name": name})
        return resp.json()

    def get_folder(self, folder_id: str) -> list[dict]:
        """`GET /services/documents/document-folders/<folder_id>` - lista
        dokumentów w folderze (każdy z `id`, `name`, `status`)."""
        resp = self._request("get", f"/services/documents/document-folders/{folder_id}")
        return resp.json()

    def upload_document(
        self,
        folder_id: str,
        filename: str,
        pdf_bytes: bytes,
        thumbnail_filename: str | None = None,
        thumbnail_bytes: bytes | None = None,
    ) -> dict:
        """`POST /services/document-folders/<folder_id>/documents` - wgrywa
        PDF (opcjonalnie z miniaturą PNG) -> `{"documentId": "..."}`.

        Przekazujemy bajty bezpośrednio w `files=` (surowy multipart) -
        zob. uwaga w docstringu modułu o błędzie base64 w starej
        implementacji. Nie zweryfikowane jeszcze na żywym środowisku
        Signius - przy pierwszym użyciu sprawdź odpowiedź `create_doc`.
        """
        files = {"file": (filename, pdf_bytes, "application/pdf")}
        if thumbnail_bytes is not None:
            files["fileThumbnail"] = (
                thumbnail_filename or "thumbnail.png",
                thumbnail_bytes,
                "image/png",
            )
        resp = self._request(
            "post", f"/services/document-folders/{folder_id}/documents", files=files
        )
        return resp.json()

    def request_signature(
        self,
        document_id: str,
        signee_email: str,
        signee_phone: str,
        title: str,
        *,
        qualified: bool = True,
    ) -> dict:
        """`PUT /services/documents/<document_id>` - żądanie podpisu; zwraca
        zaktualizowany dokument (`id`, `status`, ...).

        `qualified=True` -> `signeeSignatureType: QUALIFIED` (podpis
        kwalifikowany - dotychczasowy przepływ). `signingOrder` jest zawsze
        `1` (jeden podpisujący) - wielu sygnatariuszy wymagałby listy
        `signeesUsers` z realną kolejnością, czego stary przepływ nie
        potrzebował."""
        body = {
            "addSignaturePage": True,
            "signeeSignatureType": "QUALIFIED" if qualified else "SIMPLE",
            "signeesUsers": [
                {"signeeEmail": signee_email, "signeePhone": signee_phone, "signingOrder": 1},
            ],
            "title": title,
        }
        resp = self._request("put", f"/services/documents/{document_id}", json_body=body)
        return resp.json()

    def get_document_status(self, document_id: str, folder_id: str) -> str | None:
        """Signius nie udostępnia w tym API pojedynczego GET-u po statusie
        jednego dokumentu - status trzeba odczytać z listy folderu
        (dokładnie jak robił to oryginalny skrypt). Zwraca `None`, jeśli
        dokument nie jest (już) w folderze."""
        for doc in self.get_folder(folder_id):
            if doc.get("id") == document_id:
                return doc.get("status")
        return None

    def get_signed_document(self, document_id: str) -> bytes:
        """`GET /services/documents/<document_id>/signed` -> bajty
        podpisanego PDF."""
        resp = self._request(
            "get", f"/services/documents/{document_id}/signed", stream=True
        )
        return resp.content


_connector: SigniusConnector | None = None
_connector_lock = threading.Lock()


def get_connector() -> SigniusConnector:
    """Konektor Signius współdzielony w obrębie procesu (singleton, jak
    `connector.frappe_education.connector.get_connector()`)."""
    global _connector
    if _connector is None:
        with _connector_lock:
            if _connector is None:
                _connector = SigniusConnector()
    return _connector


def reset_connector() -> None:
    """Zamknij i zapomnij konektora (testy, przełączenie konfiguracji)."""
    global _connector
    with _connector_lock:
        if _connector is not None:
            _connector.close()
        _connector = None


__all__ = [
    "SigniusConnector",
    "get_connector",
    "reset_connector",
    "ConnectorError",
    "ConnectorConfigError",
]
