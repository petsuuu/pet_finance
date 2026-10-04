from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.services.plans import next_monthly


@pytest.mark.parametrize(
    "start,day,expected",
    [
        (date(2026, 2, 1), 31, date(2026, 2, 28)),
        (date(2028, 2, 1), 31, date(2028, 2, 29)),
        (date(2026, 12, 23), 22, date(2027, 1, 22)),
    ],
)
def test_month_boundaries(start: date, day: int, expected: date) -> None:
    assert next_monthly(start, day) == expected


def test_plans_audit_without_balance_change(client: TestClient) -> None:
    account = client.post(
        "/api/v1/accounts", json={"name": "Plans", "opening_balance": "100"}
    ).json()
    recurrence = {
        "description": "Subscription",
        "account_id": account["id"],
        "expected_amount": "11.90",
        "due_day": 31,
        "start_date": "2026-02-01",
    }
    response = client.post("/api/v1/recurrences", json=recurrence)
    assert response.status_code == 201
    assert response.json()["next_due_date"] == "2026-02-28"
    identity = response.json()["id"]
    assert (
        client.patch(f"/api/v1/recurrences/{identity}", json={"due_day": 22}).json()[
            "next_due_date"
        ]
        == "2026-03-22"
    )
    assert (
        client.patch(f"/api/v1/recurrences/{identity}", json={"active": False}).json()[
            "next_due_date"
        ]
        is None
    )
    assert len(client.get("/api/v1/recurrences").json()) == 1
    plan = client.post(
        "/api/v1/installments",
        json={
            "description": "Device",
            "account_id": account["id"],
            "installment_amount": "216.33",
            "total_installments": 21,
            "first_installment_date": "2026-08-09",
        },
    )
    assert plan.status_code == 201
    assert plan.json()["total_amount"] == "4542.93"
    assert len(client.get("/api/v1/installments").json()) == 1
    assert client.get("/api/v1/accounts").json()[0]["current_balance"] == "100.00"
    assert client.get("/api/v1/transactions").json() == []
    entities = {r["entity_type"] for r in client.get("/api/v1/audit/transactions").json()}
    assert {"recurring_transactions", "installment_plans"} <= entities
    assert client.post("/api/v1/recurrences", json={**recurrence, "due_day": 0}).status_code == 422
    assert (
        client.post(
            "/api/v1/recurrences", json={**recurrence, "end_date": "2025-01-01"}
        ).status_code
        == 422
    )
    assert (
        client.patch(f"/api/v1/recurrences/{identity}", json={"expected_amount": None}).status_code
        == 422
    )
