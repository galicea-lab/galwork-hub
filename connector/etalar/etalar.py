# connector/etalar/etalar.py
"""Konektor do e-Talar - wzorzec 1:1 z galwork-hub/connector/erpnext/erpnext.py.

Dwie grupy metod, odpowiadające mapowaniu z ../barter.md §18.1:

1. Dzisiejsze M2M API e-Talar (`/v2/apps/m2m/*`) - realne, działa. Przydatne
   tylko dla Phase 0/1 (pilot ręczny z pre-fundingiem, bez automatyzacji
   środków) - `issue`/`deposit_to_user` na konto właściciela aplikacji,
   NIGDY jako emisja ET per transakcja barterowa (../barter.md §0.2/§3
   zasada 4).
2. Przyszłe `/v3/settlement/*` (batch, mandaty, holdy) - e-Talar ich jeszcze
   NIE MA (../barter.md §11.1, zmiany P0). Metody poniżej są zaślepkami:
   budują żądanie w kształcie z §12, ale `test_connection`/wywołanie zwróci
   `ConnectorError`, dopóki e-Talar nie wystawi tych endpointów - to jest
   świadome, nie błąd implementacji.
"""
from __future__ import annotations

import json
import threading
from typing import Any

import requests

from connector.base import BaseConnector, ConnectorError


class EtalarConnector(BaseConnector):
    name = "etalar"

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
            self.get_owner_balance()
            return True
        except Exception as e:
            self.logger.warning("e-Talar test_connection nieudany: %s", e)
            return False

    def _url(self, path: str) -> str:
        session = self.connect()
        return f"{session.base_url}/{path.lstrip('/')}"  # type: ignore[attr-defined]

    def _post(self, path: str, payload: dict, *, idempotency_key: str | None = None) -> dict:
        session = self.connect()
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else {}
        resp = session.post(self._url(path), json=payload, headers=headers, timeout=30)
        if not resp.ok:
            raise ConnectorError(f"e-Talar POST {path} -> {resp.status_code}: {resp.text}")
        return resp.json()

    # --- M2M dzisiejsze (barter.md §18.1) -----------------------------

    def get_owner_balance(self) -> dict:
        """GET /v2/apps/m2m/balance/owner - saldo aplikacji BCS w e-Talar."""
        session = self.connect()
        resp = session.get(self._url("/v2/apps/m2m/balance/owner"), timeout=30)
        if not resp.ok:
            raise ConnectorError(f"e-Talar get_owner_balance -> {resp.status_code}: {resp.text}")
        return resp.json()

    def deposit_to_user(self, user_id: str, amount: str, *, idempotency_key: str) -> dict:
        """POST /v2/apps/m2m/deposit-to-user - wypłata netto na konto
        uczestnika. Wymaga uprzedniej zgody DEPOSIT_FROM_APP (../barter.md
        §2.1) - BCS nie sprawdza tego sam, e-Talar odrzuci bez zgody."""
        return self._post(
            "/v2/apps/m2m/deposit-to-user",
            {"userId": user_id, "amount": amount},
            idempotency_key=idempotency_key,
        )

    def charge_user_initiate(self, user_id: str, amount: str) -> dict:
        """POST /v2/apps/m2m/charge-user/initiate - OBCIĄŻENIE JEDNORAZOWE.
        NIE używać w pętli cyklu clearingowego (wymaga zgody człowieka przy
        KAŻDYM wywołaniu) - zob. ../barter.md §10.3 'Uwaga'. Pozostaje tylko
        dla Phase 0/pilota ręcznego."""
        return self._post("/v2/apps/m2m/charge-user/initiate", {"userId": user_id, "amount": amount})

    def request_consent(self, user_id: str, consent_type: str) -> dict:
        """POST /v2/apps/m2m/consent/request - dzisiejsze typy
        (DEPOSIT_FROM_APP, BALANCE_SHARE). `CLEARING_DEBIT` (mandat
        clearingowy, §10.3) NIE istnieje jeszcze po stronie e-Talar - to
        wywołanie zwróci błąd e-Talar, nie błąd tego konektora."""
        return self._post("/v2/apps/m2m/consent/request", {"userId": user_id, "consentType": consent_type})

    # --- /v3/settlement/* przyszłe (barter.md §11.5/§12) --------------

    def create_settlement_batch(
        self,
        *,
        network_id: str,
        cycle_id: str,
        external_batch_id: str,
        checksum: str,
        transfers: list[dict],
        fees: list[dict] | None = None,
        idempotency_key: str,
    ) -> dict:
        """POST /v3/settlement/batches (§12.2) - ZAŚLANKA: e-Talar nie
        wystawia dziś tego endpointu (brak batch API, ../barter.md §11.1
        pkt 6). Kształt żądania jest już zgodny z docelowym kontraktem,
        żeby services/settlement_service.py nie trzeba było przepisywać,
        kiedy e-Talar go dostanie - tylko usunąć `raise` poniżej."""
        payload = {
            "networkId": network_id,
            "cycleId": cycle_id,
            "externalBatchId": external_batch_id,
            "currency": "ET",
            "checksum": checksum,
            "transfers": transfers,
            "fees": fees or [],
        }
        raise ConnectorError(
            "e-Talar nie wystawia jeszcze /v3/settlement/batches "
            "(barter.md §11.1 P0) - payload przygotowany, wywołanie wyłączone: "
            f"{json.dumps(payload)[:200]}..."
        )

    def reserve_batch(self, batch_id: str, expected_checksum: str) -> dict:
        """POST /v3/settlement/batches/{id}/reserve (§12.3) - zaślepka, jak
        create_settlement_batch."""
        raise ConnectorError("e-Talar nie wystawia jeszcze /v3/settlement/batches/{id}/reserve")

    def commit_batch(self, batch_id: str) -> dict:
        """POST /v3/settlement/batches/{id}/commit (§12.3) - zaślepka."""
        raise ConnectorError("e-Talar nie wystawia jeszcze /v3/settlement/batches/{id}/commit")


_connector: EtalarConnector | None = None
_connector_lock = threading.Lock()


def get_connector() -> EtalarConnector:
    global _connector
    if _connector is None:
        with _connector_lock:
            if _connector is None:
                _connector = EtalarConnector()
    return _connector


def reset_connector() -> None:
    global _connector
    with _connector_lock:
        if _connector is not None:
            _connector.close()
        _connector = None
