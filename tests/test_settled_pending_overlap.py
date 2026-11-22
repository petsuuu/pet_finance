"""Read-only regression tests for paid and pending recurrence overlaps."""

from datetime import date
from decimal import Decimal
from uuid import uuid4

from fastapi.testclient import TestClient

from app.services.preventive_audit import settled_pending_recurrence_overlaps


def movement(rule: object, status: str, due: date) -> dict[str, object]:
    return {
        "id": uuid4(),
        "recurrence_id": rule,
        "status": status,
        "transaction_date": due,
        "amount": Decimal("10.00"),
        "type": "EXPENSE",
    }


def test_overlap_requires_posted_and_pending_in_same_recurrence_month() -> None:
    rule, other = uuid4(), uuid4()
    posted = movement(rule, "POSTED", date(2026, 11, 20))
    pending = movement(rule, "PENDING", date(2026, 11, 22))
    rows = [
        posted,
        pending,
        movement(rule, "PENDING", date(2026, 12, 22)),
        movement(other, "POSTED", date(2026, 11, 22)),
        movement(rule, "CANCELLED", date(2026, 11, 22)),
        movement(None, "POSTED", date(2026, 11, 22)),
    ]
    original = [dict(row) for row in rows]
    result = settled_pending_recurrence_overlaps(rows)
    assert len(result) == 1
    assert result[0]["recurrence_id"] == str(rule)
    assert result[0]["month"] == "2026-11"
    assert result[0]["posted_transaction_ids"] == [str(posted["id"])]
    assert result[0]["pending_transaction_ids"] == [str(pending["id"])]
    assert result[0]["requires_review"] is True
    assert rows == original


def test_paid_only_or_pending_only_is_not_a_conflict() -> None:
    rule = uuid4()
    assert settled_pending_recurrence_overlaps(
        [movement(rule, "POSTED", date(2026, 11, 22))]
    ) == []
    assert settled_pending_recurrence_overlaps(
        [movement(rule, "PENDING", date(2026, 11, 22))]
    ) == []


def test_dashboard_paid_pending_overlap_does_not_modify_ledger(client: TestClient) -> None:
    created = client.post(
        "/api/v1/accounts", json={"name": "Essenciais", "opening_balance": "100.00"}
    )
    assert created.status_code == 201, created.text
    account_id = created.json()["id"]
    recurrence = client.post(
        "/api/v1/recurrences",
        json={
            "description": "Monthly subscription",
            "expected_amount": "10.00",
            "account_id": account_id,
            "due_day": 22,
            "start_date": "2026-11-01",
        },
    )
    assert recurrence.status_code == 201, recurrence.text
    rule_id = recurrence.json()["id"]
    for status, due, key in [
        ("POSTED", "2026-11-20", "posted"),
        ("PENDING", "2026-11-22", "pending"),
    ]:
        response = client.post(
            "/api/v1/transactions",
            json={
                "type": "EXPENSE",
                "status": status,
                "description": f"Monthly subscription {key}",
                "amount": "10.00",
                "transaction_date": due,
                "account_id": account_id,
                "recurrence_id": rule_id,
                "idempotency_key": f"pr41-{key}",
            },
        )
        assert response.status_code == 201, response.text

    accounts_before = client.get("/api/v1/accounts").json()
    transactions_before = client.get("/api/v1/transactions").json()
    history_before = client.get("/api/v1/audit/transactions").json()
    dashboard = client.get("/api/v1/dashboard/data-quality")
    assert dashboard.status_code == 200, dashboard.text
    data = dashboard.json()
    assert data["status"] == "REVIEW"
    assert data["summary"]["settled_pending_overlap_groups"] == 1
    assert data["summary"]["balance_mismatches"] == 0
    section = next(
        item for item in data["sections"] if item["id"] == "settled_pending_overlaps"
    )
    assert section["count"] == 1
    assert client.get("/api/v1/accounts").json() == accounts_before
    assert client.get("/api/v1/transactions").json() == transactions_before
    assert client.get("/api/v1/audit/transactions").json() == history_before
