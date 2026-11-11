# coding: utf-8
# Konektor usługi pieczęci elektronicznej (repozytorium galwork-seal).
"""
Zlecanie pieczęci usłudze ``galwork-seal`` przez jej instancję Valkey.

Hub składa podpis PAdES biblioteką endesive (``edu/services/sealing.py``), a
usłudze przekazuje wyłącznie podpisane atrybuty CMS (kilkaset bajtów ze
skrótem dokumentu). Klucz pieczęci nie opuszcza usługi, a usługa nie widzi
treści dokumentu.

``RemoteSeal`` implementuje interfejs ``endesive.hsm.BaseHSM``
(``certificate()``, ``sign()``). Protokół (wersja 1) jest opisany w
``galwork-seal/galwork_seal/protocol.py``; tu jest jego część potrzebna
zlecającemu, żeby hub nie zależał od pakietu usługi:

  * certyfikaty: ``GET seal:cert:<keyid>`` (łańcuch PEM, pieczęć pierwsza),
  * zlecenie: ``XADD seal:requests`` (pola jak ``SealRequest.to_fields``),
  * odpowiedź: ``BLPOP seal:reply:<id>`` (JSON jak ``SealReply.to_json``).

Uprawnienia użytkownika ``hub`` w Valkey (ACL w galwork-seal) pozwalają
tylko na te trzy operacje.

Konfiguracja: sekcja ``[seal]`` w config/config.ini albo zmienne
``SEAL_<KLUCZ>``. Hasło najlepiej z pliku sekretu (``password_file``).
"""

from __future__ import annotations

import base64
import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from connector.base import BaseConnector, ConnectorConfigError, ConnectorError

PROTOCOL_VERSION = "1"
REQUESTS_STREAM = "seal:requests"

# Odmowy usługi, których ponawianie nic nie zmieni (galwork_seal.protocol).
PERMANENT_ERRORS = frozenset({"bad_request", "unknown_key", "forbidden"})


def cert_key(keyid: str) -> str:
    return f"seal:cert:{keyid}"


def reply_key(request_id: str) -> str:
    return f"seal:reply:{request_id}"


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None or str(value).strip() == "":
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")


class SealError(ConnectorError):
    """Usługa pieczęci odmówiła podpisu albo nie odpowiedziała."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code

    @property
    def permanent(self) -> bool:
        return self.code in PERMANENT_ERRORS


class RemoteSeal:
    """``endesive.hsm.BaseHSM`` przez kolejkę usługi pieczęci."""

    def __init__(self, client: Any, keyid: str, *, source: str, reference: str = "",
                 requested_by: str = "", timeout: int = 30, requests_maxlen: int = 10000):
        self.client = client
        self.keyid = keyid
        self.source = source
        self.reference = reference
        self.requested_by = requested_by
        self.timeout = timeout
        self.requests_maxlen = requests_maxlen
        self._certs: list | None = None

    def _load_certs(self) -> list:
        if self._certs is None:
            from cryptography import x509

            pem = self.client.get(cert_key(self.keyid))
            if not pem:
                raise SealError("unknown_key",
                                f"usługa pieczęci nie opublikowała certyfikatu {self.keyid!r}")
            self._certs = x509.load_pem_x509_certificates(
                pem if isinstance(pem, bytes) else str(pem).encode())
        return self._certs

    def certificate(self) -> tuple[str, bytes]:
        """Dla endesive: (keyid, certyfikat pieczęci w DER)."""
        from cryptography.hazmat.primitives import serialization

        return self.keyid, self._load_certs()[0].public_bytes(serialization.Encoding.DER)

    def chain(self) -> list:
        """Certyfikaty pośrednie (bez certyfikatu pieczęci) - ``othercerts`` w endesive."""
        return self._load_certs()[1:]

    def subject(self) -> str:
        return self._load_certs()[0].subject.rfc4514_string()

    def sign(self, keyid: str, data: bytes, mech: str) -> bytes:
        request_id = uuid.uuid4().hex
        fields = {
            "v": PROTOCOL_VERSION,
            "id": request_id,
            "keyid": keyid,
            "hashalgo": mech.lower(),
            "data": base64.b64encode(data).decode("ascii"),
            "source": self.source,
            "reference": self.reference,
            "requested_by": self.requested_by,
            "expires": f"{time.time() + self.timeout:.3f}",
        }
        self.client.xadd(REQUESTS_STREAM, fields, maxlen=self.requests_maxlen, approximate=True)
        popped = self.client.blpop([reply_key(request_id)], timeout=self.timeout)
        if not popped:
            raise SealError("timeout", f"brak odpowiedzi usługi pieczęci w {self.timeout} s")
        try:
            body = json.loads(popped[1])
            ok = bool(body["ok"])
            signature = base64.b64decode(body["signature"]) if body.get("signature") else None
        except (ValueError, KeyError, TypeError) as exc:
            raise SealError("bad_reply", f"niepoprawna odpowiedź usługi pieczęci: {exc}") from exc
        if not ok or not signature:
            raise SealError(body.get("error") or "error", body.get("message") or "odmowa podpisu")
        return signature


class SealConnector(BaseConnector):
    """Połączenie z Valkey usługi pieczęci (użytkownik ``hub``)."""

    name = "seal"

    def __init__(self, config=None) -> None:
        super().__init__(config)
        self._lock = threading.Lock()

    @property
    def keyid(self) -> str:
        return self.config.get("keyid", "pieczec") or "pieczec"

    @property
    def source(self) -> str:
        return self.config.get("source", "edu-pismo") or "edu-pismo"

    @property
    def timeout(self) -> int:
        raw = self.config.get("timeout", "30") or "30"
        try:
            return int(raw)
        except ValueError as exc:
            raise ConnectorConfigError(f"[seal] timeout musi być liczbą, jest {raw!r}") from exc

    def _password(self) -> str | None:
        password = self.config.get("password")
        if password:
            return password
        path = self.config.get("password_file")
        if not path:
            return None
        try:
            return Path(path).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ConnectorConfigError(f"[seal] password_file {path}: {exc}") from exc

    def connect(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            import redis  # klient zgodny z Valkey; import leniwy jak core/redis_cache.py
        except ImportError as exc:  # pragma: no cover - zależność środowiskowa
            raise ConnectorError("Brak pakietu redis - zainstaluj `redis`.") from exc
        port_raw = self.config.get("port", "6379") or "6379"
        db_raw = self.config.get("db", "0") or "0"
        try:
            port, db = int(port_raw), int(db_raw)
        except ValueError as exc:
            raise ConnectorConfigError(f"[seal] port/db muszą być liczbami: {exc}") from exc
        with self._lock:
            if self._client is None:
                self._client = redis.Redis(
                    host=self.config.get("host", "seal-valkey") or "seal-valkey",
                    port=port,
                    db=db,
                    username=self.config.get("username", "hub") or "hub",
                    password=self._password(),
                    ssl=_as_bool(self.config.get("ssl"), False),
                    # BLPOP czeka do `timeout` s - gniazdo musi czekać dłużej
                    socket_timeout=self.timeout + 10,
                    socket_connect_timeout=10,
                )
        return self._client

    def test_connection(self) -> bool:
        try:
            return bool(self.connect().ping())
        except Exception as exc:
            self.logger.warning("seal: test_connection() nieudany: %s", exc)
            return False

    def close(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            try:
                client.close()
            except Exception:  # pragma: no cover
                pass

    def remote(self, *, reference: str = "", requested_by: str = "") -> RemoteSeal:
        """``RemoteSeal`` dla jednego dokumentu (``reference`` trafia do dziennika usługi)."""
        return RemoteSeal(self.connect(), self.keyid, source=self.source, reference=reference,
                          requested_by=requested_by, timeout=self.timeout)


_connector: SealConnector | None = None
_connector_lock = threading.Lock()


def get_connector() -> SealConnector:
    global _connector
    if _connector is None:
        with _connector_lock:
            if _connector is None:
                _connector = SealConnector()
    return _connector


def reset_connector() -> None:
    global _connector
    with _connector_lock:
        if _connector is not None:
            _connector.close()
        _connector = None
