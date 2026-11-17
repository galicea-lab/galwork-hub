# connector/etalar/etalar.py
# 2026-11-16: token client_credentials, pola zgodne z API M2M e-Talar,
# nowe metody (issue, saldo użytkownika, wymiana kodu zgody), błędy z kodem.
"""Konektor do e-Talar.

2026-11-16: konektor dostosowany do API M2M e-Talar
  * uwierzytelnianie tokenem OAuth2 `client_credentials` z Keycloak e-Talar
    (klient aplikacji zarejestrowanej w panelu e-Talar), a nie nagłówkiem
    `token klucz:sekret` - endpointy `/v2/apps/m2m/*` przyjmują wyłącznie
    token M2M; token jest trzymany do wygaśnięcia i odnawiany raz po 401,
  * użytkownik e-Talar jest wskazywany adresem e-mail (`userEmail`),
  * `charge-user/initiate` i `consent/request` wymagają `redirectUri`
    zarejestrowanego dla aplikacji (domyślnie `[etalar] redirect_uri`),
  * metody: `issue`, `get_owner_balance`, `get_user_balance`,
    `deposit_to_user`, `charge_user_initiate`, `request_consent`,
    `exchange_consent_code`,
  * błąd 403 `CONSENT_REQUIRED` jako `EtalarConsentRequired` (stan
    oczekiwany - użytkownik nie udzielił albo odwołał zgodę),
  * kwoty jako `Decimal` zaokrąglone do 0,01, w JSON jako liczby.

e-Talar nie obsługuje jeszcze nagłówka `Idempotency-Key` (konektor go
wysyła na przyszłość). Ponowienie `issue` albo `deposit_to_user` po błędzie
sieci może więc zdublować operację - wołający musi to rozstrzygnąć sam
(np. sprawdzając saldo albo historię transakcji przed ponowieniem).

Dwie grupy metod:

1. Dzisiejsze M2M API e-Talar (`/v2/apps/m2m/*`). W BCS przydatne tylko dla
   Phase 0/1 (pilot ręczny z pre-fundingiem) - `issue`/`deposit_to_user` na
   konto właściciela aplikacji, NIGDY jako emisja ET per transakcja
   barterowa (../barter.md §0.2/§3 zasada 4).
2. Przyszłe `/v3/settlement/*` (batch, mandaty, holdy) - e-Talar ich jeszcze
   NIE MA (../barter.md §11.1, zmiany P0). Metody są zaślepkami: budują
   żądanie w kształcie z §12, ale wywołanie zwraca `ConnectorError`, dopóki
   e-Talar nie wystawi tych endpointów - to jest świadome.
"""
from __future__ import annotations

import json
import threading
import time
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

import requests

from connector.base import BaseConnector, ConnectorError

#: typy zgody stałej obsługiwane dziś przez e-Talar (`ConsentType`)
CONSENT_TYPES = ("DEPOSIT_FROM_APP", "BALANCE_SHARE")

_TOKEN_MARGIN = 30  # s - odnowienie tokenu przed wygaśnięciem
_CENT = Decimal("0.01")


class EtalarError(ConnectorError):
    """Odpowiedź błędu e-Talar (`status_code`, `detail` z `{"detail": ...}`)."""

    def __init__(self, what: str, status_code: int | None = None, detail: Any = None):
        self.what = what
        self.status_code = status_code
        self.detail = detail
        text = detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False) if detail else ""
        super().__init__(f"e-Talar {what} -> {status_code}{': ' + text[:300] if text else ''}")


class EtalarConsentRequired(EtalarError):
    """403 `CONSENT_REQUIRED` - brak aktywnej zgody użytkownika
    (`consent_type`: `DEPOSIT_FROM_APP` albo `BALANCE_SHARE`)."""

    @property
    def consent_type(self) -> str | None:
        return self.detail.get("consent_type") if isinstance(self.detail, dict) else None


def _amount(value: Any) -> float:
    """Kwota ET: dodatnia, zaokrąglona do 0,01; w JSON jako liczba (e-Talar
    przyjmuje `float`)."""
    try:
        amount = Decimal(str(value)).quantize(_CENT, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Niepoprawna kwota ET: {value!r}") from exc
    if amount <= 0:
        raise ValueError(f"Kwota ET musi być dodatnia: {value!r}")
    return float(amount)


def _detail(resp: requests.Response) -> Any:
    try:
        body = resp.json()
    except ValueError:
        return (resp.text or "")[:300]
    return body.get("detail", body) if isinstance(body, dict) else body


class EtalarConnector(BaseConnector):
    """Konfiguracja: sekcja `[etalar]` w config.ini albo zmienne
    `ETALAR_<KLUCZ>`:

      * `base_url` - adres API, np. `https://api.e-talar.com`,
      * `token_url` - endpoint tokenu Keycloak e-Talar, np.
        `https://login.e-talar.com/realms/et/protocol/openid-connect/token`,
      * `client_id`, `client_secret` - klient aplikacji z rejestracji
        w e-Talar (`keycloakClientId`, `clientSecret`),
      * `redirect_uri` - domyślny adres powrotu z ekranu zgody (musi być
        zarejestrowany dla aplikacji),
      * `timeout` (s, domyślnie 30), `verify_tls` (domyślnie true).
    """

    name = "etalar"

    def __init__(self, config=None) -> None:
        super().__init__(config)
        self._token: str | None = None
        self._token_expires = 0.0
        self._token_lock = threading.Lock()

    # --- połączenie i token ---------------------------------------------

    @property
    def base_url(self) -> str:
        return self.config.require("base_url").rstrip("/")

    @property
    def timeout(self) -> int:
        return int(self.config.get("timeout") or 30)

    def connect(self) -> requests.Session:
        if self._client is None:
            session = requests.Session()
            session.headers.update({"Accept": "application/json"})
            session.verify = str(self.config.get("verify_tls") or "true").lower() not in ("0", "false", "no")
            self._client = session
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
        self._client = None
        self._token = None
        self._token_expires = 0.0

    def test_connection(self) -> bool:
        try:
            self.get_owner_balance()
            return True
        except Exception as e:
            self.logger.warning("e-Talar test_connection nieudany: %s", e)
            return False

    def _access_token(self, *, refresh: bool = False) -> str:
        with self._token_lock:
            if not refresh and self._token and time.monotonic() < self._token_expires:
                return self._token
            try:
                resp = self.connect().post(self.config.require("token_url"), data={
                    "grant_type": "client_credentials",
                    "client_id": self.config.require("client_id"),
                    "client_secret": self.config.require("client_secret"),
                }, timeout=self.timeout)
            except requests.RequestException as exc:
                raise EtalarError("token", None, type(exc).__name__) from exc
            if not resp.ok:
                body = _detail(resp)
                # bez treści żądania (sekret) - tylko kod błędu OAuth2
                error = body.get("error") if isinstance(body, dict) else None
                raise EtalarError("token", resp.status_code, error)
            data = resp.json()
            token = data.get("access_token")
            if not token:
                raise EtalarError("token", resp.status_code, "brak access_token w odpowiedzi")
            self._token = token
            self._token_expires = time.monotonic() + max(int(data.get("expires_in") or 60) - _TOKEN_MARGIN, 5)
            return token

    def _request(self, method: str, path: str, *, json_body: dict | None = None,
                 params: dict | None = None, idempotency_key: str | None = None) -> dict:
        url = f"{self.base_url}/{path.lstrip('/')}"
        for attempt in (0, 1):
            headers = {"Authorization": f"Bearer {self._access_token(refresh=attempt == 1)}"}
            if idempotency_key:
                headers["Idempotency-Key"] = idempotency_key
            try:
                resp = self.connect().request(method, url, json=json_body, params=params,
                                              headers=headers, timeout=self.timeout)
            except requests.RequestException as exc:
                raise EtalarError(f"{method} {path}", None, type(exc).__name__) from exc
            if resp.status_code == 401 and attempt == 0:
                continue  # token wygasł albo został unieważniony - jeden ponowny token
            break
        if resp.ok:
            return resp.json() if resp.content else {}
        detail = _detail(resp)
        if resp.status_code == 403 and isinstance(detail, dict) and detail.get("error") == "CONSENT_REQUIRED":
            raise EtalarConsentRequired(f"{method} {path}", resp.status_code, detail)
        raise EtalarError(f"{method} {path}", resp.status_code, detail)

    def _redirect_uri(self, redirect_uri: str | None) -> str:
        return redirect_uri or self.config.require("redirect_uri")

    # --- M2M dzisiejsze (/v2/apps/m2m/*, integrator_sdk.md §4-§9) --------

    def issue(self, amount: Any, *, idempotency_key: str | None = None) -> dict:
        """POST /v2/apps/m2m/issue - emisja na konto WŁAŚCICIELA aplikacji
        (limit `issueLimit`, przekroczenie: 422). Zwraca `{"balanceEtalar": ...}`."""
        return self._request("POST", "/v2/apps/m2m/issue", json_body={"amount": _amount(amount)},
                             idempotency_key=idempotency_key)

    def get_owner_balance(self) -> dict:
        """GET /v2/apps/m2m/balance/owner - saldo właściciela aplikacji."""
        return self._request("GET", "/v2/apps/m2m/balance/owner")

    def get_user_balance(self, user_email: str) -> dict:
        """GET /v2/apps/m2m/balance/user - saldo użytkownika; wymaga zgody
        `BALANCE_SHARE` (inaczej `EtalarConsentRequired`)."""
        return self._request("GET", "/v2/apps/m2m/balance/user", params={"user_email": user_email})

    def deposit_to_user(self, user_email: str, amount: Any, *, description: str | None = None,
                        idempotency_key: str | None = None) -> dict:
        """POST /v2/apps/m2m/deposit-to-user - przelew z konta właściciela
        aplikacji na konto użytkownika. Wymaga zgody `DEPOSIT_FROM_APP`
        (inaczej `EtalarConsentRequired`); brak środków właściciela: 400.
        Zwraca nowe saldo użytkownika."""
        body: dict[str, Any] = {"userEmail": user_email, "amount": _amount(amount)}
        if description:
            body["description"] = description
        return self._request("POST", "/v2/apps/m2m/deposit-to-user", json_body=body,
                             idempotency_key=idempotency_key)

    def charge_user_initiate(self, user_email: str, amount: Any, *, description: str | None = None,
                             redirect_uri: str | None = None, state: str | None = None) -> dict:
        """POST /v2/apps/m2m/charge-user/initiate - OBCIĄŻENIE JEDNORAZOWE,
        zawsze z potwierdzeniem użytkownika na ekranie zgody. Zwraca
        `{"requestId", "consentUrl"}` - przeglądarkę użytkownika trzeba
        przekierować na `consentUrl`, a kod z powrotu wymienić
        `exchange_consent_code`. NIE używać w pętli cyklu clearingowego
        (../barter.md §10.3)."""
        body: dict[str, Any] = {"userEmail": user_email, "amount": _amount(amount),
                                "redirectUri": self._redirect_uri(redirect_uri)}
        if description:
            body["description"] = description
        if state:
            body["state"] = state
        return self._request("POST", "/v2/apps/m2m/charge-user/initiate", json_body=body)

    def request_consent(self, consent_type: str, *, redirect_uri: str | None = None,
                        state: str | None = None) -> dict:
        """POST /v2/apps/m2m/consent/request - zgoda stała
        (`DEPOSIT_FROM_APP` albo `BALANCE_SHARE`). Żądanie nie wskazuje
        użytkownika: zgody udziela osoba zalogowana na ekranie zgody, a jej
        e-mail zwraca `exchange_consent_code`. Zwraca `{"requestId", "consentUrl"}`."""
        if consent_type not in CONSENT_TYPES:
            raise ValueError(f"Nieobsługiwany typ zgody e-Talar: {consent_type!r} (dozwolone: {', '.join(CONSENT_TYPES)})")
        body: dict[str, Any] = {"consentType": consent_type, "redirectUri": self._redirect_uri(redirect_uri)}
        if state:
            body["state"] = state
        return self._request("POST", "/v2/apps/m2m/consent/request", json_body=body)

    def exchange_consent_code(self, exchange_code: str) -> dict:
        """POST /v2/apps/consent/exchange - wynik zgody po powrocie na
        `redirectUri?code=...`: `{"status": "APPROVED"|"REJECTED",
        "operationType", "amount", "userEmail"}`. Kod jest jednorazowy
        (ponownie: 409) i ważny 5 minut (potem: 410). Wołać z backendu."""
        return self._request("POST", "/v2/apps/consent/exchange", json_body={"exchangeCode": exchange_code})

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
