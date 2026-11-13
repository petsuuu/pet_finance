from fastapi.testclient import TestClient


def test_category_reparent_preserves_transactions_and_blocks_cycles(client: TestClient) -> None:
    car = client.post("/api/v1/categories", json={"name": "Carro"}).json()
    mobility = client.post("/api/v1/categories", json={"name": "Mobilidade"}).json()
    fuel = client.post(
        "/api/v1/categories", json={"name": "Combustível", "parent_id": mobility["id"]}
    ).json()
    account = client.post(
        "/api/v1/accounts", json={"name": "Essenciais", "opening_balance": "500.00"}
    ).json()
    expense = client.post(
        "/api/v1/transactions",
        json={
            "type": "EXPENSE",
            "status": "POSTED",
            "description": "Gasolina",
            "amount": "179.90",
            "transaction_date": "2026-11-13",
            "account_id": account["id"],
            "category_id": fuel["id"],
            "idempotency_key": "test-car-category",
        },
    ).json()
    balance_before = client.get("/api/v1/accounts").json()[0]["current_balance"]

    moved = client.patch(
        f"/api/v1/categories/{fuel['id']}", json={"parent_id": car["id"]}
    )
    assert moved.status_code == 200
    assert moved.json()["parent_id"] == car["id"]
    assert client.get("/api/v1/transactions").json()[0]["id"] == expense["id"]
    assert client.get("/api/v1/accounts").json()[0]["current_balance"] == balance_before

    assert client.patch(
        f"/api/v1/categories/{car['id']}", json={"parent_id": fuel["id"]}
    ).status_code == 422
    assert client.patch(
        f"/api/v1/categories/{car['id']}", json={"parent_id": car["id"]}
    ).status_code == 422
    assert client.patch(
        f"/api/v1/categories/{fuel['id']}",
        json={"parent_id": "00000000-0000-0000-0000-000000000099"},
    ).status_code == 404
