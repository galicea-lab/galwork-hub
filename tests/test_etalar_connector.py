# tests/test_etalar_connector.py
# 2026-11-16: testy konektora e-Talar (connector/etalar) - bez żywego
# serwera: atrapa `requests.Session` i konfiguracja `ConnectorConfig` w pamięci.
from __future__ import annotations

import json
import unittest

from connector.base import ConnectorConfig, ConnectorConfigError, ConnectorError
from connector.etalar.etalar import EtalarConnector, EtalarConsentRequired, EtalarError

TOKEN_URL = "https://login.example.org/realms/et/protocol/openid-connect/token"


class FakeResponse:
    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self._json = json_data
        self.text = json.dumps(json_data) if json_data is not None else ""
        self.content = self.text.encode()

    def json(self):
        if self._json is None:
            raise ValueError("brak JSON")
        return self._json


class FakeSession:
    """Zapisuje wywołania; odpowiedzi z kolejek przypisanych do końcówki URL."""

    def __init__(self):
        self.headers = {}
        self.verify = True
        self.calls: list[dict] = []
        self.queues: dict[str, list[FakeResponse]] = {}
        self.token_no = 0

    def program(self, suffix: str, *responses: FakeResponse):
        self.queues.setdefault(suffix, []).extend(responses)

    def post(self, url, data=None, timeout=None):  # endpoint tokenu
        self.calls.append({"method": "TOKEN", "url": url, "data": data})
        queue = self.queues.get("TOKEN")
        if queue:
            return queue.pop(0)
        self.token_no += 1
        return FakeResponse(200, {"access_token": f"tok{self.token_no}", "expires_in": 300})

    def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        for suffix, queue in self.queues.items():
            if suffix != "TOKEN" and url.endswith(suffix) and queue:
                return queue.pop(0)
        raise AssertionError(f"nieoczekiwane wywołanie {method} {url}")

    def close(self):
        pass


def make(**overrides):
    values = {"base_url": "https://api.example.org/", "token_url": TOKEN_URL,
              "client_id": "app-123", "client_secret": "sekret",
              "redirect_uri": "https://galwork.example.org/etalar/callback"}
    values.update(overrides)
    connector = EtalarConnector(ConnectorConfig(section="etalar_test", values=values))
    session = FakeSession()
    connector._client = session
    return connector, session


def api_calls(session):
    return [c for c in session.calls if c["method"] != "TOKEN"]


class EtalarConnectorTest(unittest.TestCase):
    def test_token_client_credentials_and_bearer(self):
        connector, session = make()
        session.program("/v2/apps/m2m/balance/owner", FakeResponse(200, {"balanceEtalar": 87.5}))
        self.assertEqual(connector.get_owner_balance(), {"balanceEtalar": 87.5})
        token_call = session.calls[0]
        self.assertEqual(token_call["url"], TOKEN_URL)
        self.assertEqual(token_call["data"], {"grant_type": "client_credentials",
                                              "client_id": "app-123", "client_secret": "sekret"})
        call = api_calls(session)[0]
        self.assertEqual(call["url"], "https://api.example.org/v2/apps/m2m/balance/owner")
        self.assertEqual(call["headers"]["Authorization"], "Bearer tok1")

    def test_token_cached_between_calls(self):
        connector, session = make()
        session.program("/balance/owner", FakeResponse(200, {"balanceEtalar": 1}), FakeResponse(200, {"balanceEtalar": 2}))
        connector.get_owner_balance()
        connector.get_owner_balance()
        self.assertEqual(sum(1 for c in session.calls if c["method"] == "TOKEN"), 1)

    def test_401_refreshes_token_once(self):
        connector, session = make()
        session.program("/balance/owner", FakeResponse(401, {"detail": "expired"}),
                        FakeResponse(200, {"balanceEtalar": 5}))
        self.assertEqual(connector.get_owner_balance(), {"balanceEtalar": 5})
        calls = api_calls(session)
        self.assertEqual([c["headers"]["Authorization"] for c in calls], ["Bearer tok1", "Bearer tok2"])

    def test_token_error_does_not_leak_secret(self):
        connector, session = make()
        session.program("TOKEN", FakeResponse(401, {"error": "unauthorized_client",
                                                     "error_description": "Invalid client secret"}))
        with self.assertRaises(EtalarError) as ctx:
            connector.get_owner_balance()
        self.assertEqual(ctx.exception.status_code, 401)
        self.assertIn("unauthorized_client", str(ctx.exception))
        self.assertNotIn("sekret", str(ctx.exception))

    def test_deposit_uses_user_email_and_rounds_amount(self):
        connector, session = make()
        session.program("/deposit-to-user", FakeResponse(200, {"balanceEtalar": 47}))
        connector.deposit_to_user("jan@example.org", "5.005", description="Wycena W/1/2026",
                                  idempotency_key="sprawa-1-v1")
        call = api_calls(session)[0]
        self.assertEqual(call["method"], "POST")
        self.assertEqual(call["json"], {"userEmail": "jan@example.org", "amount": 5.01,
                                        "description": "Wycena W/1/2026"})
        self.assertEqual(call["headers"]["Idempotency-Key"], "sprawa-1-v1")

    def test_consent_required_is_typed(self):
        connector, session = make()
        session.program("/deposit-to-user", FakeResponse(403, {"detail": {
            "error": "CONSENT_REQUIRED", "consent_type": "DEPOSIT_FROM_APP"}}))
        with self.assertRaises(EtalarConsentRequired) as ctx:
            connector.deposit_to_user("jan@example.org", 5)
        self.assertEqual(ctx.exception.consent_type, "DEPOSIT_FROM_APP")
        self.assertIsInstance(ctx.exception, ConnectorError)

    def test_other_errors_keep_status_and_detail(self):
        connector, session = make()
        session.program("/m2m/issue", FakeResponse(422, {"detail": "Przekroczony limit emisji"}))
        with self.assertRaises(EtalarError) as ctx:
            connector.issue(100)
        self.assertEqual(ctx.exception.status_code, 422)
        self.assertEqual(ctx.exception.detail, "Przekroczony limit emisji")
        self.assertNotIsInstance(ctx.exception, EtalarConsentRequired)

    def test_invalid_amount_rejected_before_request(self):
        connector, session = make()
        for bad in (0, -1, "abc", "0.001"):
            with self.assertRaises(ValueError):
                connector.issue(bad)
        self.assertEqual(session.calls, [])

    def test_charge_user_uses_default_redirect_uri_and_state(self):
        connector, session = make()
        session.program("/charge-user/initiate", FakeResponse(201, {"requestId": "r1", "consentUrl": "https://x/consent"}))
        result = connector.charge_user_initiate("jan@example.org", 10, description="Szkolenie", state="sprawa-7")
        self.assertEqual(result["requestId"], "r1")
        self.assertEqual(api_calls(session)[0]["json"], {
            "userEmail": "jan@example.org", "amount": 10.0,
            "redirectUri": "https://galwork.example.org/etalar/callback",
            "description": "Szkolenie", "state": "sprawa-7"})

    def test_redirect_uri_required(self):
        connector, _ = make(redirect_uri="")
        with self.assertRaises(ConnectorConfigError):
            connector.request_consent("DEPOSIT_FROM_APP")

    def test_request_consent_validates_type_and_sends_no_user(self):
        connector, session = make()
        with self.assertRaises(ValueError):
            connector.request_consent("CLEARING_DEBIT")
        session.program("/m2m/consent/request", FakeResponse(201, {"requestId": "r2", "consentUrl": "u"}))
        connector.request_consent("BALANCE_SHARE", state="polacz-42")
        self.assertEqual(api_calls(session)[0]["json"], {
            "consentType": "BALANCE_SHARE",
            "redirectUri": "https://galwork.example.org/etalar/callback", "state": "polacz-42"})

    def test_user_balance_and_exchange_paths(self):
        connector, session = make()
        session.program("/m2m/balance/user", FakeResponse(200, {"balanceEtalar": 42}))
        session.program("/apps/consent/exchange", FakeResponse(200, {
            "status": "APPROVED", "operationType": "CHARGE_USER", "amount": 10, "userEmail": "jan@example.org"}))
        connector.get_user_balance("jan@example.org")
        result = connector.exchange_consent_code("kod-1")
        calls = api_calls(session)
        self.assertEqual(calls[0]["params"], {"user_email": "jan@example.org"})
        self.assertEqual(calls[1]["url"], "https://api.example.org/v2/apps/consent/exchange")
        self.assertEqual(calls[1]["json"], {"exchangeCode": "kod-1"})
        self.assertEqual(result["status"], "APPROVED")

    def test_missing_config_is_config_error(self):
        connector, _ = make(client_id="")
        with self.assertRaises(ConnectorConfigError):
            connector.get_owner_balance()

    def test_settlement_batch_still_stub(self):
        connector, session = make()
        with self.assertRaises(ConnectorError):
            connector.create_settlement_batch(network_id="n", cycle_id="c", external_batch_id="b",
                                              checksum="x", transfers=[], idempotency_key="k")
        self.assertEqual(session.calls, [])


if __name__ == "__main__":
    unittest.main()
