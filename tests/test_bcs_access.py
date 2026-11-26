# tests/test_bcs_access.py
# 2026-11-24: kontrola dostępu BCS (bcs/api/access.py, router.py):
# anonim - 401/403 wszędzie; operator - wszystko; audytor - odczyt;
# uczestnik - tylko dane uczestników sieci, do których jest przypięty.
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import bcs.models.audit  # noqa: F401 - rejestracja tabel
import bcs.models.cycle  # noqa: F401
import bcs.models.dispute  # noqa: F401
import bcs.models.member  # noqa: F401
import bcs.models.network  # noqa: F401
import bcs.models.obligation  # noqa: F401
import bcs.models.settlement  # noqa: F401
import bcs.models.source_system  # noqa: F401
import bcs.models.statement  # noqa: F401
from api.dependencies import get_db
from api.security import BCS_OPERATOR_ROLE, CurrentPrincipal, get_current_principal, require_operator
from bcs.api.access import BCS_AUDITOR_ROLE
from db.base import Base
from main import app
from models.user import User
from router import app_bcs
from schemas.token_schemas import Token


def _principal(sub, roles=()):
    return CurrentPrincipal(
        user=User(id=1, keycloak_sub=sub, name=sub, email=f"{sub}@example.com"),
        token=Token(username=sub, email=f"{sub}@example.com", message="", scope="", roles=[], sub=sub),
        claims={"sub": sub, "realm_access": {"roles": list(roles)}},
    )


OPERATOR = _principal("operator", [BCS_OPERATOR_ROLE])
AUDITOR = _principal("auditor", [BCS_AUDITOR_ROLE])
PARTNER_A = _principal("partner-a")
OUTSIDER = _principal("outsider")


class Actor:
    current = OPERATOR


@pytest.fixture()
def client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    def _db():
        db = session()
        try:
            yield db
        finally:
            db.close()

    def _operator():
        # require_operator sprawdza rolę na bieżącym aktorze (jak w produkcji)
        if BCS_OPERATOR_ROLE not in Actor.current.claims["realm_access"]["roles"]:
            from fastapi import HTTPException
            raise HTTPException(status_code=403, detail="operator")
        return Actor.current

    Actor.current = OPERATOR
    app_bcs.dependency_overrides[get_db] = _db
    app_bcs.dependency_overrides[get_current_principal] = lambda: Actor.current
    app_bcs.dependency_overrides[require_operator] = _operator
    yield TestClient(app, base_url="http://testserver/v1/bcs")
    app_bcs.dependency_overrides.clear()
    engine.dispose()


def ok(response, status=200):
    assert response.status_code == status, response.text
    return response.json()


@pytest.fixture()
def world(client):
    """Sieć z uczestnikami A, B, C; partner-a przypięty do A; dokumenty A-B i B-C."""
    net = ok(client.post("/api/networks", json={"code": "TM", "name": "Trans-Med"}), 201)
    members = {}
    for code in "ABC":
        org = ok(client.post("/api/organizations", json={"legal_name": f"Firma {code}", "tax_id": f"NIP-{code}"}), 201)
        members[code] = ok(client.post(f"/api/networks/{net['id']}/members",
                                       json={"organization_id": org["id"], "member_code": code,
                                             "credit_limit_et": "100"}), 201)
    ok(client.post(f"/api/networks/{net['id']}/members/{members['A']['id']}/users",
                   json={"user_sub": "partner-a", "username": "partner-a"}), 201)
    src_a = ok(client.post(f"/api/members/{members['A']['id']}/source-systems", json={"type": "CSV"}), 201)
    src_b = ok(client.post(f"/api/members/{members['B']['id']}/source-systems", json={"type": "CSV"}), 201)
    result = ok(client.post(f"/api/networks/{net['id']}/obligations/batch", json={
        "source_system_id": src_b["id"], "batch_id": "b1",
        "obligations": [_doc("ab", "A", "B"), _doc("bc", "B", "C")],
    }))
    assert result["accepted"] == 2
    return {"net": net, "members": members, "src_a": src_a, "src_b": src_b}


def _doc(ext, debtor, creditor, amount="10"):
    return {"external_id": ext, "debtor_member_code": debtor, "creditor_member_code": creditor,
            "document_type": "INVOICE", "document_number": ext, "issue_date": "2026-10-21",
            "amount": amount, "document_hash": "sha256:x"}


def test_anonymous_gets_401_or_403(client):
    app_bcs.dependency_overrides.pop(get_current_principal)
    for method, url in [("get", "/api/networks"), ("get", "/api/organizations"),
                        ("post", "/api/organizations"), ("get", "/api/networks/x/obligations"),
                        ("post", "/api/networks/x/obligations/batch"), ("get", "/api/batches/x"),
                        ("get", "/api/members/x/statement"), ("get", "/api/me/memberships")]:
        response = getattr(client, method)(url, **({"json": {}} if method == "post" else {}))
        assert response.status_code in (401, 403), (method, url, response.status_code)


def test_partner_sees_only_own_data(client, world):
    net_id, m = world["net"]["id"], world["members"]
    Actor.current = PARTNER_A
    assert [n["id"] for n in ok(client.get("/api/networks"))] == [net_id]
    assert [x["member_code"] for x in ok(client.get(f"/api/networks/{net_id}/members"))] == ["A"]
    docs = ok(client.get(f"/api/networks/{net_id}/obligations"))
    assert [d["external_id"] for d in docs] == ["ab"]
    assert client.get(f"/api/networks/{net_id}/obligations", params={"member_id": m["B"]["id"]}).status_code == 403
    assert client.get(f"/api/networks/{net_id}/members/{m['B']['id']}").status_code == 403
    assert client.get(f"/api/members/{m['B']['id']}/source-systems").status_code == 403
    assert client.get(f"/api/members/{m['B']['id']}/statement").status_code == 403
    assert client.get("/api/organizations").status_code == 403
    assert client.get(f"/api/networks/{net_id}/source-systems").status_code == 403
    assert ok(client.get(f"/api/members/{m['A']['id']}/source-systems"))[0]["id"] == world["src_a"]["id"]


def test_outsider_has_no_network_access(client, world):
    net_id = world["net"]["id"]
    Actor.current = OUTSIDER
    assert ok(client.get("/api/networks")) == []
    assert client.get(f"/api/networks/{net_id}").status_code == 403
    assert client.get(f"/api/networks/{net_id}/cycles").status_code == 403
    assert ok(client.get(f"/api/networks/{net_id}/obligations")) == []
    assert ok(client.get(f"/api/networks/{net_id}/disputes")) == []


def test_partner_import_only_via_own_source_and_as_party(client, world):
    net_id = world["net"]["id"]
    Actor.current = PARTNER_A
    url = f"/api/networks/{net_id}/obligations/batch"
    other = client.post(url, json={"source_system_id": world["src_b"]["id"], "batch_id": "x",
                                   "obligations": [_doc("x1", "A", "B")]})
    assert other.status_code == 403
    result = ok(client.post(url, json={"source_system_id": world["src_a"]["id"], "batch_id": "y",
                                       "obligations": [_doc("y1", "A", "C"), _doc("y2", "B", "C")]}))
    assert result["accepted"] == 1
    assert [r["code"] for r in result["rejections"]] == ["NOT_A_PARTY"]
    unknown = client.post(url, json={"source_system_id": "nope", "batch_id": "z", "obligations": []})
    assert unknown.status_code == 404


def test_partner_disputes_only_as_own_member(client, world):
    net_id, m = world["net"]["id"], world["members"]
    Actor.current = OPERATOR
    docs = {d["external_id"]: d for d in ok(client.get(f"/api/networks/{net_id}/obligations"))}
    Actor.current = PARTNER_A
    url = f"/api/networks/{net_id}/obligations/{docs['ab']['id']}/dispute"
    reason = {"reason": "Towar nie dotarł do magazynu"}
    assert client.post(url, json=reason).status_code == 403
    assert client.post(url, json={**reason, "raised_by_member_id": m["B"]["id"]}).status_code == 403
    dispute = ok(client.post(url, json={**reason, "raised_by_member_id": m["A"]["id"]}), 201)
    assert ok(client.get(f"/api/disputes/{dispute['id']}"))["id"] == dispute["id"]
    Actor.current = OUTSIDER
    assert client.get(f"/api/disputes/{dispute['id']}").status_code == 403


def test_auditor_reads_everything_but_cannot_write(client, world):
    net_id, m = world["net"]["id"], world["members"]
    Actor.current = AUDITOR
    assert len(ok(client.get(f"/api/networks/{net_id}/obligations"))) == 2
    assert len(ok(client.get("/api/organizations"))) == 3
    assert len(ok(client.get(f"/api/networks/{net_id}/members"))) == 3
    assert client.post("/api/organizations", json={"legal_name": "X", "tax_id": "NIP-X"}).status_code == 403
    assert client.post(f"/api/members/{m['A']['id']}/source-systems", json={"type": "CSV"}).status_code == 403
    assert client.post(f"/api/networks/{net_id}/obligations/batch", json={
        "source_system_id": world["src_b"]["id"], "batch_id": "a", "obligations": []}).status_code == 403
