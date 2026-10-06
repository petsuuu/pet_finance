from fastapi.testclient import TestClient


def test_budget_preview_apply_retry_manual_and_dashboard(client: TestClient) -> None:
    account = client.post("/api/v1/accounts", json={"name": "Budget wallet"}).json()["id"]
    category = client.post(
        "/api/v1/categories", json={"name": "Café e Padaria", "expense_class": "SUPERFLUOUS"}
    ).json()["id"]
    for day, amount in [("2026-08-01", "100"), ("2026-09-01", "300")]:
        assert (
            client.post(
                "/api/v1/transactions",
                json={
                    "type": "EXPENSE",
                    "status": "POSTED",
                    "description": "Historical cafe",
                    "amount": amount,
                    "transaction_date": day,
                    "account_id": account,
                    "category_id": category,
                    "idempotency_key": day,
                },
            ).status_code
            == 201
        )
    body = {"year": 2026, "month": 10, "savings_target": "500"}
    preview = client.post("/api/v1/budgets/generate", json=body)
    assert preview.status_code == 200
    assert preview.json()["preview"] is True
    params = {"year": 2026, "month": 10}
    assert client.get("/api/v1/budgets", params=params).json()["items"] == []
    applied = client.post("/api/v1/budgets/generate", json={**body, "preview": False})
    assert applied.status_code == 200
    assert applied.json()["items"][0]["limit_amount"] == "180.00"
    identity = applied.json()["items"][0]["id"]
    assert (
        client.post("/api/v1/budgets/generate", json={**body, "preview": False}).status_code == 200
    )
    assert client.get("/api/v1/budgets", params=params).json()["items"][0]["id"] == identity
    assert (
        client.post(
            "/api/v1/budgets/set", json={**params, "category_id": category, "limit_amount": "120"}
        ).status_code
        == 200
    )
    client.post("/api/v1/budgets/generate", json={**body, "preview": False})
    entry = {
        "type": "EXPENSE",
        "status": "POSTED",
        "description": "October cafe",
        "amount": "110",
        "transaction_date": "2026-10-06",
        "account_id": account,
        "category_id": category,
        "idempotency_key": "october-cafe",
    }
    assert client.post("/api/v1/transactions", json=entry).status_code == 201
    assert client.post("/api/v1/transactions", json=entry).status_code == 201
    data = client.get("/api/v1/budgets", params=params).json()
    assert data["items"][0]["limit_amount"] == "120.00"
    assert data["items"][0]["method"] == "MANUAL"
    assert data["items"][0]["remaining"] == "10.00"
    assert data["items"][0]["status"] == "WARNING"
    assert len(client.get("/api/v1/transactions").json()) == 3
    dash = client.get("/api/v1/dashboard/monthly", params={**params, "as_of": "2026-10-06"}).json()
    assert dash["category_budgets"]["savings_target"] == "500.00"
    assert dash["budget_planning"]["liquidity_warning"] is True


def test_no_evidence_and_invalid_budget_category(client: TestClient) -> None:
    client.post("/api/v1/categories", json={"name": "New category", "expense_class": "ESSENTIAL"})
    body = {"year": 2026, "month": 10, "preview": False}
    assert client.post("/api/v1/budgets/generate", json=body).status_code == 200
    data = client.get("/api/v1/budgets", params={"year": 2026, "month": 10}).json()
    assert data["items"][0]["status"] == "NEEDS_REVIEW"
    assert (
        client.post(
            "/api/v1/budgets/set",
            json={
                "year": 2026,
                "month": 10,
                "category_id": "11111111-1111-1111-1111-111111111111",
                "limit_amount": "10",
            },
        ).status_code
        == 404
    )
