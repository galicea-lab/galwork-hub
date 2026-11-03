# coding: utf-8
# wzorzec (mockup) konektora.
"""
Uniwersalny wzorzec konektora do systemu zewnętrznego.

Powstał na podstawie analizy istniejących konektorów w tym katalogu
(connector/webcon, connector/edoreczenia, connector/signius,
connector/frappe_education). Każdy z nich komunikuje się z innym rodzajem
systemu (REST+OAuth2, SOAP/REST z certyfikatem, REST+PKCS12), więc wzorzec
nie wymusza jednego protokołu - określa tylko wspólny "kontrakt" (cykl
życia, konfiguracja, test połączenia, obsługa błędów), żeby każdy nowy
konektor dało się włączyć do huba w ten sam, przewidywalny sposób.

Nowy konektor powinien:
  1. mieć własny katalog `connector/<system>/`,
  2. zdefiniować klasę dziedziczącą po `BaseConnector`,
  3. zaimplementować `connect()` i `test_connection()`,
  4. czytać konfigurację przez `BaseConnector.load_ini_section()` (sekcja
     o nazwie konektora w config/config.ini) albo przez zmienne środowiskowe,
  5. wystawiać własne metody domenowe (np. `get_invoice`, `list_workflows`) -
     `BaseConnector` nie narzuca generycznego CRUD, bo nie każdy system
     zewnętrzny się do tego nadaje.
"""

from __future__ import annotations

import configparser
import logging
import os
from abc import ABC, abstractmethod
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import requests

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "config.ini")


@dataclass
class ConnectorConfig:
    """Lekki kontener na konfigurację konektora.

    Nie jest to pydantic BaseSettings (jak core/config.py Settings) celowo -
    konektory bywają opcjonalne/nieużywane w danym wdrożeniu huba, więc ich
    konfiguracja nie powinna być wymagana do startu całej aplikacji.
    """

    section: str
    values: dict[str, str] = field(default_factory=dict)

    def get(self, key: str, default: str | None = None) -> str | None:
        """Wartość klucza: zmienna środowiskowa ma pierwszeństwo przed plikiem.

         zgodnie z `core/config.py::_section_values` i zasadą "sekrety w środowisku":
        `SIGNIUS_P12_PASSWORD` nadpisuje to, co jest w config/config.ini.
        """
        env_value = os.environ.get(f"{self.section.upper()}_{key.upper()}")
        if env_value:
            return env_value
        return self.values.get(key, default)

    def require(self, key: str) -> str:
        value = self.get(key)
        if not value:
            raise ConnectorConfigError(
                f"Brak wymaganego parametru '{key}' w sekcji [{self.section}] "
                f"pliku config.ini (albo zmiennej środowiskowej "
                f"{self.section.upper()}_{key.upper()})."
            )
        return value


class ConnectorError(Exception):
    """Błąd komunikacji z systemem zewnętrznym."""


class ConnectorConfigError(ConnectorError):
    """Brak albo niepoprawna konfiguracja konektora."""


@contextmanager
def translate_network_errors(context: str):
    """Tłumaczy błędy transportu `requests` (DNS, timeout, connection
    refused) na `ConnectorError` - konektory w tym katalogu tłumaczą na
    `ConnectorError` tylko odpowiedzi HTTP błędów (`resp.ok` fałszywe), nie
    wyjątki samego transportu. Do użycia w serwisach (wf/services/*)
    otaczających wywołania konektora, żeby routery mogły zmapować
    ConnectorError -> 502 bez niezłapanego 500 (zob.
    wf/services/erpnext_invoice_service.py)."""
    try:
        yield
    except requests.exceptions.RequestException as exc:
        raise ConnectorError(f"{context}: {exc}") from exc


class BaseConnector(ABC):
    """Wspólny kontrakt dla wszystkich konektorów w katalogu connector/.

    Subklasy implementują `connect()` i `test_connection()`; resztę
    (logger, dostęp do konfiguracji) dostają gotową.
    """

    #: nazwa sekcji w config/config.ini, np. "signius", "frappe_education"
    name: str = "base"

    def __init__(self, config: ConnectorConfig | None = None) -> None:
        self.config = config or self.load_ini_section(self.name)
        self.logger = logging.getLogger(f"connector.{self.name}")
        self._client: Any = None

    @staticmethod
    def load_ini_section(section: str) -> ConnectorConfig:
        """Wczytuje sekcję [section] z config/config.ini (jeśli istnieje)."""
        parser = configparser.ConfigParser()
        parser.read(_CONFIG_PATH)
        values = dict(parser[section]) if parser.has_section(section) else {}
        return ConnectorConfig(section=section, values=values)

    @abstractmethod
    def connect(self) -> Any:
        """Nawiązuje połączenie/tworzy klienta i zwraca go (oraz cache'uje
        w self._client). Ma być idempotentne - kolejne wywołania mogą
        oddawać już istniejące połączenie."""
        raise NotImplementedError

    @abstractmethod
    def test_connection(self) -> bool:
        """Szybki health-check (np. GET /ping, SELECT 1). Nie podnosi
        wyjątku w normalnym przebiegu - zwraca False przy niepowodzeniu."""
        raise NotImplementedError

    def close(self) -> None:
        """Domyślnie no-op - konektory bezstanowe (REST) nie muszą nic robić.
        Konektory z trwałym połączeniem (np. SQL) powinny to przesłonić."""
        self._client = None

    def __enter__(self) -> "BaseConnector":
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
