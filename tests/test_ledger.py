from concurrent.futures import ThreadPoolExecutor
from typing import Any

from fastapi.testclient import TestClient


def setup(client: TestClient) -> dict[str, Any]:
    account = client.post(
        "/api/v1/accounts", json={"name": "Test wallet", "opening_balance": "100.00"}
    )
    assert account.status_code == 201
    category = client.post("/api/v1/categories", json={"name": "Fuel"})
    assert category.status_code == 201
    return {
        "type": "EXPENSE",
        "status": "POSTED",
        "description": "Test fuel",
        "amount": "50.01",
        "transaction_date": "2026-10-04",
        "account_id": account.json()["id"],
        "category_id": category.json()["id"],
        "idempotency_key": "test-1",
    }


def test_expense_update_cancel(client: TestClient) -> None:
    payload = setup(client)
    response = client.post("/api/v1/transactions", json=payload)
    assert response.status_code == 201
    assert response.json()["amount"] == "50.01"
    identity = response.json()["id"]
    assert client.get("/api/v1/accounts").json()[0]["current_balance"] == "49.99"
    assert (
        client.patch(f"/api/v1/transactions/{identity}", json={"amount": "60.02"}).status_code
        == 200
    )
    assert client.get("/api/v1/accounts").json()[0]["current_balance"] == "39.98"
    assert client.post(f"/api/v1/transactions/{identity}/cancel").status_code == 200
    assert client.get("/api/v1/accounts").json()[0]["current_balance"] == "100.00"
    assert len(client.get("/api/v1/transactions").json()) == 1
    actions = [
        r["action"]
        for r in client.get("/api/v1/audit/transactions").json()
        if r["entity_type"] == "transactions"
    ]
    assert actions == ["CANCEL", "UPDATE", "CREATE"]


def test_idempotency_and_duplicates(client: TestClient) -> None:
    payload = setup(client)
    first = client.post("/api/v1/transactions", json=payload).json()
    assert client.post("/api/v1/transactions", json=payload).json()["id"] == first["id"]
    assert client.post("/api/v1/transactions", json={**payload, "amount": "20"}).status_code == 409
    payload["idempotency_key"] = "test-2"
    assert client.post("/api/v1/transactions", json=payload).status_code == 409
    assert client.post("/api/v1/transactions", json={**payload, "force": True}).status_code == 201
    assert len(client.get("/api/v1/transactions").json()) == 2


def test_distinct_allowances_and_near_identical_duplicates(client: TestClient) -> None:
    payload = {**setup(client), "description": "Mesada Martin — recorrência"}
    first = client.post("/api/v1/transactions", json=payload)
    assert first.status_code == 201
    second_payload = {
        **payload,
        "description": "Mesada Luigi — recorrência",
        "idempotency_key": "allowance-luigi",
    }
    second = client.post("/api/v1/transactions", json=second_payload)
    assert second.status_code == 201
    assert second.json()["id"] != first.json()["id"]
    retry = client.post("/api/v1/transactions", json=second_payload)
    assert retry.json()["id"] == second.json()["id"]
    for description in ["Mesada Luigi — recorrência", "Mesada Luig — recorrência"]:
        duplicate = client.post(
            "/api/v1/transactions",
            json={**second_payload, "description": description, "idempotency_key": description},
        )
        assert duplicate.status_code == 409
        assert duplicate.json()["detail"]["candidate_id"] == second.json()["id"]
    assert len(client.get("/api/v1/transactions").json()) == 2


def test_concurrent_idempotency(client: TestClient) -> None:
    payload = setup(client)
    with ThreadPoolExecutor(max_workers=4) as executor:
        responses = list(
            executor.map(lambda _: client.post("/api/v1/transactions", json=payload), range(4))
        )
    assert all(r.status_code == 201 for r in responses)
    assert len({r.json()["id"] for r in responses}) == 1
    assert len(client.get("/api/v1/transactions").json()) == 1


def test_income_pending_validation_and_auth(client: TestClient) -> None:
    payload = setup(client)
    assert client.get("/health").status_code == 200
    assert client.get("/api/v1/accounts", headers={"Authorization": "wrong"}).status_code == 401
    for amount in ["0", "-1", "0.001", "NaN"]:
        assert (
            client.post("/api/v1/transactions", json={**payload, "amount": amount}).status_code
            == 422
        )
    assert (
        client.post("/api/v1/transactions", json={**payload, "type": "TRANSFER"}).status_code == 422
    )
    assert (
        client.post("/api/v1/transactions", json={**payload, "status": "PENDING"}).status_code
        == 201
    )
    assert client.get("/api/v1/accounts").json()[0]["current_balance"] == "100.00"
    assert (
        client.post(
            "/api/v1/transactions",
            json={**payload, "type": "INCOME", "idempotency_key": "income", "amount": "10.10"},
        ).status_code
        == 201
    )
    assert client.get("/api/v1/accounts").json()[0]["current_balance"] == "110.10"


def test_category_uniqueness_and_missing_reference(client: TestClient) -> None:
    payload = setup(client)
    assert client.post("/api/v1/categories", json={"name": "Fuel"}).status_code == 409
    assert (
        client.post(
            "/api/v1/categories", json={"name": "Child", "parent_id": payload["category_id"]}
        ).status_code
        == 201
    )
    assert (
        client.post(
            "/api/v1/transactions",
            json={**payload, "account_id": "00000000-0000-0000-0000-000000000099"},
        ).status_code
        == 404
    )
    assert client.get("/api/v1/transactions").json() == []
