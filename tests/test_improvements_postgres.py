from fastapi.testclient import TestClient


def test_cancel_rest_preserves_paid_history_and_blocks_generation(client: TestClient) -> None:
    account = client.post("/api/v1/accounts", json={"name": "Test wallet"}).json()["id"]
    rule = client.post(
        "/api/v1/recurrences",
        json={
            "description": "Subscription",
            "account_id": account,
            "expected_amount": "50",
            "due_day": 9,
            "start_date": "2026-09-01",
            "end_date": "2028-12-31",
        },
    ).json()["id"]
    generated = client.post(
        f"/api/v1/recurrences/{rule}/generate",
        json={
            "start_date": "2026-09-01",
            "end_date": "2026-12-31",
        },
    ).json()["transactions"]
    september = next(t for t in generated if t["transaction_date"] == "2026-09-09")
    october = next(t for t in generated if t["transaction_date"] == "2026-10-09")
    client.patch(f"/api/v1/transactions/{october['id']}", json={"status": "POSTED"})
    url = f"/api/v1/recurrences/{rule}/cancel"
    body = {"effective_date": "2026-10-01"}
    assert client.post(url, json=body).json()["affected_count"] == 2
    applied = client.post(url, json={**body, "preview": False})
    assert applied.status_code == 200
    assert applied.json()["cancelled_count"] == 2
    assert client.get(f"/api/v1/transactions/{october['id']}").json()["status"] == "POSTED"
    assert client.get(f"/api/v1/transactions/{september['id']}").json()["status"] == "PENDING"
    assert client.post(url, json={**body, "preview": False}).json()["cancelled_count"] == 0
    regenerated = client.post(
        f"/api/v1/recurrences/{rule}/generate",
        json={
            "start_date": "2026-10-01",
            "end_date": "2026-12-31",
        },
    )
    assert regenerated.json()["created_count"] == 0


def test_merchant_rest_rollback_and_default_category(client: TestClient) -> None:
    account = client.post("/api/v1/accounts", json={"name": "Test wallet"}).json()["id"]
    category = client.post("/api/v1/categories", json={"name": "Food"}).json()["id"]
    body = {"name": "Sample Cafe", "aliases": ["SAMPLE*CAFE LTDA"], "default_category_id": category}
    merchant = client.post("/api/v1/merchants/setup", json=body).json()["merchant"]["id"]
    assert client.post("/api/v1/merchants/setup", json=body).json()["merchant"]["id"] == merchant
    collision = client.post(
        "/api/v1/merchants/setup",
        json={
            "name": "Another shop",
            "aliases": ["SAMPLE*CAFE LTDA"],
        },
    )
    assert collision.status_code == 409
    assert len(client.get("/api/v1/merchants").json()) == 1
    resolved = client.get("/api/v1/merchants/resolve", params={"raw_name": "sample cafe ltda"})
    assert resolved.json()["suggested_category_id"] == category
    result = client.post(
        "/api/v1/transactions",
        json={
            "account_id": account,
            "merchant_id": merchant,
            "type": "EXPENSE",
            "status": "POSTED",
            "description": "Lunch",
            "amount": "33.25",
            "transaction_date": "2026-10-05",
            "idempotency_key": "merchant-lunch",
        },
    )
    assert result.status_code == 201
    assert result.json()["category_id"] == category
    client.patch(f"/api/v1/merchants/{merchant}", json={"default_category_id": None})
    assert (
        client.get(f"/api/v1/transactions/{result.json()['id']}").json()["category_id"] == category
    )
