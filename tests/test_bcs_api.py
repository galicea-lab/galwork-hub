# tests/test_api.py
# Przepływ end-to-end przez REST: sieć -> uczestnicy (KYB, aktywacja) ->
# integracja ERP -> import -> spór -> cykl -> netting -> batch -> statementy.
import pytest

from tests.test_bsc_conf import TEST_SUB, client  # noqa: F401 - fixture `client`


def ok(response, status=200):
    assert response.status_code == status, response.text
    return response.json()


@pytest.fixture()
def network(client):
    return ok(client.post("/api/networks", json={"code": "TM", "name": "Trans-Med"}), 201)


def add_member(client, network_id, code):
    org = ok(client.post("/api/organizations", json={"legal_name": f"Firma {code}", "tax_id": f"NIP-{code}"}), 201)
    return ok(
        client.post(
            f"/api/networks/{network_id}/members",
            json={"organization_id": org["id"], "member_code": code, "credit_limit_et": "100"},
        ),
        201,
    )


@pytest.fixture()
def members(client, network):
    return {code: add_member(client, network["id"], code) for code in "ABC"}


def import_docs(client, network_id, source_id, docs, batch_id="b1"):
    return ok(
        client.post(
            f"/api/networks/{network_id}/obligations/batch",
            json={
                "source_system_id": source_id,
                "batch_id": batch_id,
                "obligations": [
                    {
                        "external_id": ext,
                        "debtor_member_code": debtor,
                        "creditor_member_code": creditor,
                        "document_type": "INVOICE",
                        "document_number": ext,
                        "issue_date": "2026-10-21",
                        "amount": amount,
                        "document_hash": "sha256:x",
                    }
                    for ext, debtor, creditor, amount in docs
                ],
            },
        )
    )


def test_network_get_patch_and_duplicate_code(client, network):
    assert ok(client.get(f"/api/networks/{network['id']}"))["cycle_policy"] == {}
    patched = ok(client.patch(f"/api/networks/{network['id']}", json={"status": "PILOT"}))
    assert patched["status"] == "PILOT"
    assert client.post("/api/networks", json={"code": "TM", "name": "x"}).status_code == 409
    assert client.get("/api/networks/nope").json() == {"detail": "Sieć nie znaleziona"}


def test_organizations_list_get_patch(client, members):
    orgs = ok(client.get("/api/organizations"))
    assert [o["legal_name"] for o in orgs] == ["Firma A", "Firma B", "Firma C"]
    assert ok(client.get("/api/organizations", params={"tax_id": "NIP-B"}))[0]["legal_name"] == "Firma B"
    org_id = members["A"]["organization_id"]
    assert ok(client.patch(f"/api/organizations/{org_id}", json={"kyb_status": "APPROVED"}))["kyb_status"] == "APPROVED"
    assert ok(client.get(f"/api/organizations/{org_id}"))["kyb_status"] == "APPROVED"


def test_member_activation_requires_kyb(client, network, members):
    a = members["A"]
    url = f"/api/networks/{network['id']}/members/{a['id']}"
    assert client.post(f"{url}/activate").status_code == 409
    client.patch(f"/api/organizations/{a['organization_id']}", json={"kyb_status": "APPROVED"})
    activated = ok(client.post(f"{url}/activate"))
    assert activated["status"] == "ACTIVE" and activated["onboarded_at"]
    assert ok(client.post(f"{url}/suspend"))["status"] == "SUSPENDED"
    assert ok(client.patch(url, json={"credit_limit_et": "250.5"}))["credit_limit_et"] == "250.5000"
    assert ok(client.get(url))["member_code"] == "A"


def test_member_code_unique_and_member_scoped_to_network(client, network, members):
    org = ok(client.post("/api/organizations", json={"legal_name": "X"}), 201)
    dup = client.post(f"/api/networks/{network['id']}/members", json={"organization_id": org["id"], "member_code": "A"})
    assert dup.status_code == 409
    other = ok(client.post("/api/networks", json={"code": "OTHER", "name": "Inna"}), 201)
    assert client.get(f"/api/networks/{other['id']}/members/{members['A']['id']}").status_code == 404


def test_member_users_and_my_memberships(client, network, members):
    a = members["A"]["id"]
    url = f"/api/networks/{network['id']}/members/{a}/users"
    assert ok(client.get("/api/me/memberships")) == []
    ok(client.post(url, json={"user_sub": TEST_SUB, "username": "tester"}), 201)
    assert client.post(url, json={"user_sub": TEST_SUB}).status_code == 409
    assert [m["member_code"] for m in ok(client.get("/api/me/memberships"))] == ["A"]
    assert client.delete(f"{url}/{TEST_SUB}").status_code == 204
    assert ok(client.get("/api/me/memberships")) == []


def test_source_systems_by_member_and_network(client, network, members):
    a = members["A"]["id"]
    s1 = ok(client.post(f"/api/members/{a}/source-systems", json={"type": "CSV"}), 201)
    s2 = ok(
        client.post(
            f"/api/networks/{network['id']}/source-systems",
            json={"member_id": members["B"]["id"], "type": "REST_PUSH", "endpoint": "https://erp.b"},
        ),
        201,
    )
    assert s1["status"] == "ACTIVE" and s2["endpoint"] == "https://erp.b"
    assert len(ok(client.get(f"/api/networks/{network['id']}/source-systems"))) == 2
    assert [s["id"] for s in ok(client.get(f"/api/members/{a}/source-systems"))] == [s1["id"]]
    assert client.post(f"/api/members/{a}/source-systems", json={"type": "FAX"}).status_code == 422


def test_full_cycle_with_dispute_and_statements(client, network, members):
    nid = network["id"]
    source = ok(client.post(f"/api/members/{members['A']['id']}/source-systems", json={"type": "CSV"}), 201)
    result = import_docs(
        client,
        nid,
        source["id"],
        [("FV1", "A", "B", "100"), ("FV2", "B", "C", "80"), ("FV3", "C", "A", "40"), ("FV4", "C", "B", "5")],
    )
    assert result["accepted"] == 4

    docs = {o["external_id"]: o for o in ok(client.get(f"/api/networks/{nid}/obligations"))}
    a_id = members["A"]["id"]
    assert len(ok(client.get(f"/api/networks/{nid}/obligations", params={"member_id": a_id}))) == 2
    assert len(ok(client.get(f"/api/networks/{nid}/obligations", params={"member_id": a_id, "role": "debtor"}))) == 1
    assert ok(client.get(f"/api/networks/{nid}/obligations/{docs['FV1']['id']}"))["issue_date"] == "2026-10-21"

    # spór: tylko strona dokumentu, jeden otwarty naraz; dokument wypada z nettingu
    fv4 = docs["FV4"]["id"]
    bad = client.post(
        f"/api/networks/{nid}/obligations/{fv4}/dispute",
        json={"reason": "Usługa niewykonana", "raised_by_member_id": a_id},
    )
    assert bad.status_code == 409
    dispute = ok(
        client.post(
            f"/api/networks/{nid}/obligations/{fv4}/dispute",
            json={"reason": "Usługa niewykonana", "raised_by_member_id": members["C"]["id"]},
        ),
        201,
    )
    assert client.post(f"/api/networks/{nid}/obligations/{fv4}/dispute", json={"reason": "jeszcze raz!!"}).status_code == 409
    assert ok(client.get(f"/api/networks/{nid}/obligations/{fv4}"))["status"] == "DISPUTED"
    assert len(ok(client.get(f"/api/networks/{nid}/disputes", params={"status": "OPEN"}))) == 1

    cycle = ok(
        client.post(
            f"/api/networks/{nid}/cycles",
            json={"period_start": "2026-10-20T00:00:00", "period_end": "2026-10-26T23:59:00"},
        ),
        201,
    )
    cid = cycle["id"]
    assert ok(client.get(f"/api/cycles/{cid}"))["status"] == "OPEN"
    assert client.get(f"/api/cycles/{cid}/statement").status_code == 409
    ok(client.post(f"/api/cycles/{cid}/close"))
    positions = ok(client.post(f"/api/cycles/{cid}/net"))
    assert {p["member_id"]: p["net_et"] for p in positions}[a_id] == "-60.0000"

    batch = ok(client.post(f"/api/cycles/{cid}/settle"))
    batches = ok(client.get(f"/api/cycles/{cid}/batches"))
    assert [b["id"] for b in batches] == [batch["id"]]

    network_stmt = ok(client.get(f"/api/cycles/{cid}/statement"))
    assert network_stmt["gross_total_et"] == "220.0000"
    assert network_stmt["obligations_count"] == 3
    assert network_stmt["open_disputes"] == 1
    assert network_stmt["transfers_count"] == len(batch["transfers"])
    assert network_stmt["hash"].startswith("sha256:")

    stmt = ok(client.get(f"/api/members/{a_id}/statement", params={"cycle_id": cid}))
    assert stmt["member_code"] == "A"
    assert stmt["receivables_et"] == "40.0000" and stmt["payables_et"] == "100.0000"
    assert sum(float(t["amount"]) for t in stmt["transfers"]) == 60.0
    # bez cycle_id - ostatni cykl po nettingu, ten sam (deterministyczny) hash
    assert ok(client.get(f"/api/members/{a_id}/statement"))["hash"] == stmt["hash"]

    # rozstrzygnięcie sporu
    did = dispute["id"]
    assert ok(client.post(f"/api/disputes/{did}/review"))["status"] == "UNDER_REVIEW"
    resolved = ok(client.post(f"/api/disputes/{did}/resolve", json={"outcome": "REJECTED", "decision": "Usługa wykonana"}))
    assert resolved["status"] == "REJECTED" and resolved["resolved_at"]
    assert ok(client.get(f"/api/networks/{nid}/obligations/{fv4}"))["status"] == "VALIDATED"
    assert client.post(f"/api/disputes/{did}/resolve", json={"outcome": "UPHELD", "decision": "x" * 5}).status_code == 409


def test_dispute_upheld_cancels_obligation(client, network, members):
    nid = network["id"]
    source = ok(client.post(f"/api/members/{members['A']['id']}/source-systems", json={"type": "CSV"}), 201)
    import_docs(client, nid, source["id"], [("FV9", "A", "B", "10")])
    oid = ok(client.get(f"/api/networks/{nid}/obligations"))[0]["id"]
    did = ok(client.post(f"/api/networks/{nid}/obligations/{oid}/dispute", json={"reason": "Zgłoszenie operatora"}), 201)[
        "id"
    ]
    assert ok(client.get(f"/api/disputes/{did}"))["raised_by_member_id"] is None
    ok(client.post(f"/api/disputes/{did}/resolve", json={"outcome": "UPHELD", "decision": "Faktura błędna"}))
    assert ok(client.get(f"/api/networks/{nid}/obligations/{oid}"))["status"] == "CANCELLED"


def test_unknown_routes_vs_missing_records(client):
    # frontend (isMissingEndpoint) odróżnia brak trasy od braku rekordu
    assert client.get("/api/cycles/nope").json() == {"detail": "Cykl nie znaleziony"}
    assert client.get("/api/nonexistent").json() == {"detail": "Not Found"}
    assert client.post(
        "/api/networks/x/cycles", json={"period_start": "2026-10-20T00:00:00", "period_end": "2026-10-26T00:00:00"}
    ).status_code == 404
