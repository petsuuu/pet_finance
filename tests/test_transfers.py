from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

from fastapi.testclient import TestClient


def setup_transfer(client: TestClient) -> dict[str, str]:
    accounts = []
    for name, amount in [("Origin", "1000.00"), ("Destination", "100.00")]:
        response = client.post(
            "/api/v1/accounts",
            json={"name": name, "opening_balance": amount},
        )
        assert response.status_code == 201, response.text
        accounts.append(response.json()["id"])
    return {
        "account_id": accounts[0],
        "target_account_id": accounts[1],
        "amount": "250.00",
        "transaction_date": "2025-01-01",
        "description": "Internal transfer",
        "idempotency_key": "transfer-test-001",
    }


def balances(client: TestClient) -> dict[str, Decimal]:
    response = client.get("/api/v1/accounts")
    assert response.status_code == 200
    return {
        row["account_id"]: Decimal(str(row["current_balance"]))
        for row in response.json()
    }


def test_transfer_atomic_balances_and_replay(client: TestClient) -> None:
    body = setup_transfer(client)
    before = balances(client)
    first = client.post("/api/v1/transfers", json=body)
    assert first.status_code == 201, first.text
    replay = client.post("/api/v1/transfers", json=body)
    assert replay.status_code == 201
    assert replay.json()["id"] == first.json()["id"]
    after = balances(client)
    assert after[body["account_id"]] == Decimal("750")
    assert after[body["target_account_id"]] == Decimal("350")
    assert sum(before.values()) == sum(after.values())
    rows = client.get("/api/v1/transactions", params={"type": "TRANSFER"}).json()
    assert len(rows) == 1
    audit = client.get("/api/v1/audit/transactions").json()
    assert any(str(row["entity_id"]) == first.json()["id"] for row in audit)
    assert client.patch(
        f"/api/v1/transactions/{first.json()['id']}", json={"amount": "1"}
    ).status_code == 409
    assert client.post(
        f"/api/v1/transactions/{first.json()['id']}/cancel"
    ).status_code == 409
    assert balances(client) == after


def test_transfer_rejects_invalid_and_duplicate(client: TestClient) -> None:
    body = setup_transfer(client)
    before = balances(client)
    for changes, status in [
        ({"target_account_id": body["account_id"]}, 422),
        ({"amount": "1001"}, 409),
        ({"amount": "-1"}, 422),
        ({"transaction_date": "2099-01-01"}, 422),
    ]:
        response = client.post("/api/v1/transfers", json={**body, **changes})
        assert response.status_code == status, response.text
        assert balances(client) == before
    assert client.post("/api/v1/transfers", json=body).status_code == 201
    after = balances(client)
    for changes in [{"amount": "251"}, {"idempotency_key": "another-key"}]:
        response = client.post("/api/v1/transfers", json={**body, **changes})
        assert response.status_code == 409, response.text
        assert balances(client) == after


def test_concurrent_replay(client: TestClient) -> None:
    body = setup_transfer(client)

    def submit(_: int) -> str:
        response = client.post("/api/v1/transfers", json=body)
        assert response.status_code == 201, response.text
        return str(response.json()["id"])

    with ThreadPoolExecutor(max_workers=4) as pool:
        identities = list(pool.map(submit, range(4)))
    assert len(set(identities)) == 1
    assert balances(client)[body["account_id"]] == Decimal("750")
