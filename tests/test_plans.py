from datetime import date
from typing import Any

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


def test_adopt_monthly_occurrences_and_generate_idempotently(client: TestClient) -> None:
    account = client.post("/api/v1/accounts", json={"name": "Migration wallet"}).json()
    payload = {
        "description": "Monthly subscription",
        "account_id": account["id"],
        "type": "EXPENSE",
        "amount": "10",
        "status": "POSTED",
        "transaction_date": "2026-10-01",
        "idempotency_key": "early-october",
    }
    october = client.post("/api/v1/transactions", json=payload).json()
    november = client.post(
        "/api/v1/transactions",
        json={
            **payload,
            "transaction_date": "2026-11-03",
            "status": "PENDING",
            "idempotency_key": "november",
        },
    ).json()
    setup = {
        "description": "Monthly subscription",
        "account_id": account["id"],
        "expected_amount": "10",
        "due_day": 3,
        "start_date": "2026-10-01",
        "end_date": "2026-12-31",
        "transaction_ids": [october["id"], november["id"]],
    }
    response = client.post("/api/v1/recurrences/setup", json=setup)
    assert response.status_code == 200
    identity = response.json()["recurrence"]["id"]
    retry = client.post("/api/v1/recurrences/setup", json=setup)
    assert retry.json()["recurrence"]["id"] == identity
    assert len(client.get("/api/v1/recurrences").json()) == 1
    dashboard = client.get(
        "/api/v1/dashboard/monthly",
        params={
            "year": 2026,
            "month": 10,
            "as_of": "2026-10-05",
        },
    ).json()
    assert dashboard["unrecorded_recurring_expenses"] == "0"
    period = {"start_date": "2026-10-01", "end_date": "2027-01-31"}
    generated = client.post(f"/api/v1/recurrences/{identity}/generate", json=period)
    assert generated.status_code == 200
    assert generated.json()["created_count"] == 1
    assert generated.json()["skipped_count"] == 2
    assert generated.json()["transactions"][0]["transaction_date"] == "2026-12-03"
    assert generated.json()["transactions"][0]["status"] == "PENDING"
    assert (
        client.post(f"/api/v1/recurrences/{identity}/generate", json=period).json()["created_count"]
        == 0
    )
    assert client.get("/api/v1/accounts").json()[0]["current_balance"] == "-10.00"
    assert (
        client.post(
            "/api/v1/recurrences/setup",
            json={
                **setup,
                "expected_amount": "20",
            },
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/api/v1/recurrences/setup",
            json={
                **setup,
                "transaction_ids": [october["id"], october["id"]],
            },
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/recurrences/setup",
            json={
                **setup,
                "type": "INCOME",
            },
        ).status_code
        == 422
    )
    client.post(f"/api/v1/transactions/{november['id']}/cancel")
    assert (
        client.post(f"/api/v1/recurrences/{identity}/generate", json=period).json()["created_count"]
        == 0
    )
    assert (
        client.post(
            f"/api/v1/recurrences/{identity}/generate",
            json={
                "start_date": "2026-01-01",
                "end_date": "2030-01-01",
            },
        ).status_code
        == 422
    )


def test_extend_rule_preserves_payments_and_annual_renewals(client: TestClient) -> None:
    account = client.post("/api/v1/accounts", json={"name": "Annual wallet"}).json()["id"]
    payment = client.post(
        "/api/v1/transactions",
        json={
            "description": "Annual renewal",
            "account_id": account,
            "type": "EXPENSE",
            "amount": "119.90",
            "status": "POSTED",
            "transaction_date": "2027-08-01",
            "idempotency_key": "annual-early-payment",
        },
    ).json()
    setup = {
        "description": "Annual renewal",
        "account_id": account,
        "expected_amount": "119.90",
        "frequency": "YEARLY",
        "month_of_year": 8,
        "due_day": 9,
        "start_date": "2027-08-01",
        "end_date": "2027-12-31",
        "transaction_ids": [payment["id"]],
    }
    configured = client.post("/api/v1/recurrences/setup", json=setup)
    assert configured.status_code == 200
    identity = configured.json()["recurrence"]["id"]
    assert configured.json()["recurrence"]["next_due_date"] == "2027-08-09"
    assert (
        client.post("/api/v1/recurrences/setup", json=setup).json()["recurrence"]["id"] == identity
    )
    patched = client.patch(f"/api/v1/recurrences/{identity}", json={"end_date": "2028-12-31"})
    assert patched.status_code == 200
    assert patched.json()["id"] == identity
    assert patched.json()["frequency"] == "YEARLY"
    assert len(client.get("/api/v1/recurrences").json()) == 1
    recorded = client.get(f"/api/v1/transactions/{payment['id']}").json()
    for key in ["amount", "status", "transaction_date"]:
        assert recorded[key] == payment[key]
    period = {"start_date": "2028-01-01", "end_date": "2028-12-31"}

    def dashboard(month: int) -> dict[str, Any]:
        result: dict[str, Any] = client.get(
            "/api/v1/dashboard/monthly",
            params={
                "year": 2028,
                "month": month,
                "as_of": f"2028-{month:02}-01",
            },
        ).json()

        return result

    assert dashboard(7)["unrecorded_recurring_expenses"] == "0"
    assert dashboard(8)["unrecorded_recurring_expenses"] == "119.90"
    generated = client.post(f"/api/v1/recurrences/{identity}/generate", json=period).json()
    assert generated["created_count"] == 1
    assert generated["transactions"][0]["transaction_date"] == "2028-08-09"
    assert dashboard(8)["unrecorded_recurring_expenses"] == "0"
    assert (
        client.post(f"/api/v1/recurrences/{identity}/generate", json=period).json()["created_count"]
        == 0
    )
    audit = client.get("/api/v1/audit/recurrences", params={**period, "as_of": "2028-01-01"}).json()
    assert len(audit) == 1
    assert audit[0]["due_date"] == "2028-08-09"
    assert (
        client.post("/api/v1/recurrences/setup", json={**setup, "month_of_year": 7}).status_code
        == 422
    )
    assert (
        client.post("/api/v1/recurrences/setup", json={**setup, "month_of_year": None}).status_code
        == 422
    )
    assert (
        client.patch(f"/api/v1/recurrences/{identity}", json={"active": False}).status_code == 200
    )
    assert client.post(f"/api/v1/recurrences/{identity}/generate", json=period).status_code == 422
