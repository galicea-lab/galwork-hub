# core/redis_cache.py
# coding: utf-8

"""
Klient Redis używany do buforowania skompilowanych formularzy zadań
(`wf/services/process_service.py::task_form`) - zob. rjsf.md/x-webcon.md:
backend ma być "projektorem/filtrem" formularza wg `x-permissions`, a wynik
tego filtrowania (ten sam dla wszystkich instancji tego samego procesu w tym
samym kroku) trzymać w Redis, żeby nie przeliczać go przy każdym wejściu w
zadanie.

Konfiguracja - sekcja `[redis]` w config/config.ini (albo zmienne
środowiskowe REDIS_HOST/PORT/DB/PASSWORD/TTL). W przeciwieństwie do innych
konektorów (connector/signius, connector/frappe_education) Redis ma sensowne
wartości domyślne (localhost:6379/db 0) - cache jest optymalizacją, nie
integracją wymagającą sekretów, więc działa "out of the box" w dev.

WAŻNE: błąd Redis (brak połączenia, timeout) NIGDY nie ma wywalić żądania -
cache jest przezroczystą optymalizacją; przy błędzie `cache_get_json`
zwraca `None` (tak jakby nie było trafienia), a `cache_set_json` po prostu
nic nie zapisuje. Oba logują ostrzeżenie, ale nie podnoszą wyjątku.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from connector.base import BaseConnector, ConnectorConfigError

logger = logging.getLogger("core.redis_cache")

#: domyślny czas życia wpisu (sekundy) - 24h; formularz wygasa też jawnie
#: przy republikacji procesu, bo klucz cache'a niesie `content_hash` wersji
#: (zob. process_service.py::task_form).
DEFAULT_TTL_SECONDS = 24 * 3600


class RedisCacheConnector(BaseConnector):
    """Konektor Redis wg wzorca `connector/base.py::BaseConnector`."""

    name = "redis"

    def connect(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            import redis
        except ImportError as exc:  # pragma: no cover - zależność środowiskowa
            raise ConnectorConfigError(
                "Brak pakietu redis - zainstaluj `redis` (patrz requirements.txt)."
            ) from exc
        host = self.config.get("host", "localhost")
        port_raw = self.config.get("port", "6379")
        db_raw = self.config.get("db", "0")
        password = self.config.get("password") or None
        try:
            port = int(port_raw)
            db = int(db_raw)
        except (TypeError, ValueError) as exc:
            raise ConnectorConfigError(
                f"[redis] port/db muszą być liczbami (port={port_raw!r}, db={db_raw!r})"
            ) from exc
        self._client = redis.Redis(host=host, port=port, db=db, password=password,
                                   decode_responses=True, socket_connect_timeout=2,
                                   socket_timeout=2)
        return self._client

    def test_connection(self) -> bool:
        try:
            return bool(self.connect().ping())
        except Exception as exc:
            logger.warning("redis: test_connection() nieudany: %s", exc)
            return False

    def ttl_seconds(self) -> int:
        try:
            return int(self.config.get("ttl", str(DEFAULT_TTL_SECONDS)))
        except (TypeError, ValueError):
            return DEFAULT_TTL_SECONDS


_connector: RedisCacheConnector | None = None


def get_redis_connector() -> RedisCacheConnector:
    """Konektor Redis współdzielony w obrębie procesu (singleton, jak inne
    konektory dziedziczące po `BaseConnector`)."""
    global _connector
    if _connector is None:
        _connector = RedisCacheConnector()
    return _connector


def reset_redis_connector() -> None:
    """Zamknij i zapomnij konektora (testy, przełączenie konfiguracji)."""
    global _connector
    if _connector is not None:
        _connector.close()
    _connector = None


def cache_get_json(key: str) -> Any | None:
    """Odczyt JSON z Redis pod `key` - `None`, jeśli brak wpisu, Redis jest
    nieskonfigurowany/offline, albo wartość nie jest poprawnym JSON."""
    connector = get_redis_connector()
    try:
        raw = connector.connect().get(key)
    except ConnectorConfigError:
        return None
    except Exception as exc:  # połączenie odrzucone, timeout itd.
        logger.warning("redis GET %s: %s", key, exc)
        return None
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        logger.warning("redis GET %s: wartość nie jest poprawnym JSON", key)
        return None


def cache_set_json(key: str, value: Any, ttl: int | None = None) -> None:
    """Zapis JSON do Redis pod `key` (TTL z `[redis] ttl`, domyślnie 24h).
    Błąd zapisu jest tylko logowany - cache jest optymalizacją, nie źródłem
    prawdy."""
    connector = get_redis_connector()
    try:
        client = connector.connect()
    except ConnectorConfigError:
        return
    try:
        client.set(key, json.dumps(value, ensure_ascii=False),
                   ex=ttl if ttl is not None else connector.ttl_seconds())
    except Exception as exc:
        logger.warning("redis SET %s: %s", key, exc)
