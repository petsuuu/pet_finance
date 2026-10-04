from concurrent.futures import ThreadPoolExecutor
from datetime import date

from fastapi.testclient import TestClient

from app.services.schedules import month_date


def test_anchor_does_not_drift() -> None:
    assert month_date(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert month_date(date(2026, 1, 31), 2) == date(2026, 3, 31)


def test_generation_audits_and_tags(client: TestClient) -> None:
    account = client.post("/api/v1/accounts", json={"name": "Schedule wallet"}).json()["id"]
    plan = client.post(
        "/api/v1/installments",
        json={
            "description": "Device",
            "account_id": account,
            "installment_amount": "10.01",
            "total_installments": 3,
            "first_installment_date": "2026-01-31",
        },
    ).json()["id"]
    assert (
        client.get("/api/v1/audit/installments", params={"identity": plan}).json()[0]["status"]
        == "MISSING"
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda _: client.post(f"/api/v1/installments/{plan}/generate"), range(2))
        )
    assert all(r.status_code == 200 for r in results)
    assert sum(r.json()["created_count"] for r in results) == 3
    assert client.get("/api/v1/accounts").json()[0]["current_balance"] == "0.00"
    assert len(client.get("/api/v1/transactions").json()) == 3
    rule = client.post(
        "/api/v1/recurrences",
        json={
            "description": "Subscription",
            "expected_amount": "12",
            "account_id": account,
            "due_day": 22,
            "start_date": "2026-10-01",
        },
    ).json()["id"]
    params = {"start_date": "2026-10-01", "end_date": "2026-10-31", "as_of": "2026-10-23"}
    assert client.get("/api/v1/audit/recurrences", params=params).json()[0]["status"] == "OVERDUE"
    tag = client.post("/api/v1/tags", json={"name": "Trip"}).json()["id"]
    body = {
        "description": "Subscription",
        "type": "EXPENSE",
        "status": "POSTED",
        "amount": "12",
        "transaction_date": "2026-10-22",
        "account_id": account,
        "recurrence_id": rule,
        "tags": [tag],
        "idempotency_key": "schedule-1",
    }
    assert client.post("/api/v1/transactions", json=body).status_code == 201
    assert client.get("/api/v1/audit/recurrences", params=params).json()[0]["status"] == "MATCHED"
    assert len(client.get("/api/v1/tags").json()) == 1
    assert len(client.get("/api/v1/transactions", params={"tag": tag}).json()) == 1
