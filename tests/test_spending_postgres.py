from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient


def test_purchase_readonly_validated_and_exposed_in_dashboard(client: TestClient) -> None:
    account = client.post(
        "/api/v1/accounts", json={"name": "Cash", "opening_balance": "1000"}
    ).json()["id"]
    category = client.post("/api/v1/categories", json={"name": "Leisure"}).json()["id"]
    today = datetime.now(ZoneInfo("America/Sao_Paulo")).date()
    client.post(
        "/api/v1/budgets/set",
        json={
            "year": today.year,
            "month": today.month,
            "category_id": category,
            "limit_amount": "100",
        },
    )
    assert (
        client.post(
            "/api/v1/budgets/generate",
            json={
                "year": today.year,
                "month": today.month,
                "savings_target": "500",
                "preview": False,
            },
        ).status_code
        == 200
    )
    client.post(
        "/api/v1/transactions",
        json={
            "type": "EXPENSE",
            "status": "PENDING",
            "description": "Rent due today",
            "amount": "950",
            "transaction_date": today.isoformat(),
            "account_id": account,
            "idempotency_key": "rent-due",
        },
    )
    before = client.get("/api/v1/audit/transactions").json()
    params = {"amount": "60", "category_id": category}
    for _ in range(2):
        r = client.get("/api/v1/dashboard/purchase-check", params=params)
        assert r.status_code == 200
        assert r.json()["decision"] == "NAO_RECOMENDADO"
        assert r.json()["maximum_within_scenario"] == "0"
        assert r.json()["next_obligations"][0]["description"] == "Rent due today"
    assert client.get("/api/v1/audit/transactions").json() == before
    assert len(client.get("/api/v1/transactions").json()) == 1
    dash = client.get(
        "/api/v1/dashboard/monthly", params={"year": today.year, "month": today.month}
    ).json()
    assert dash["spending_today"]["maximum_without_category"] == "0"
    assert (
        client.get("/api/v1/dashboard/purchase-check", params={"amount": "-1"}).status_code == 422
    )
    assert (
        client.get(
            "/api/v1/dashboard/purchase-check",
            params={"amount": "1", "category_id": "11111111-1111-1111-1111-111111111111"},
        ).status_code
        == 404
    )
    client.patch("/api/v1/categories/" + category, json={"active": False})
    assert client.get("/api/v1/dashboard/purchase-check", params=params).status_code == 422
