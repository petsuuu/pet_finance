"""Regressions for review-only repeated pending recurrence findings."""

from datetime import date
from decimal import Decimal
from uuid import uuid4

from fastapi.testclient import TestClient

from app.services.preventive_audit import pending_recurrence_duplicate_groups


def movement(
    recurrence_id: object, due: date, status: str = "PENDING"
) -> dict[str, object]:
    return {
        "id": uuid4(),
        "recurrence_id": recurrence_id,
        "status": status,
        "transaction_date": due,
        "amount": Decimal("10.00"),
    }


def test_repeated_pending_month_detected_without_changing_rows() -> None:
    rule = uuid4()
    first = movement(rule, date(2026, 11, 20))
    second = movement(rule, date(2026, 11, 22))
    rows = [
        first,
        second,
        movement(rule, date(2026, 12, 20)),
        movement(rule, date(2026, 11, 22), "POSTED"),
        movement(rule, date(2026, 11, 22), "CANCELLED"),
        movement(uuid4(), date(2026, 11, 22)),
        movement(None, date(2026, 11, 22)),
    ]
    original = [dict(row) for row in rows]
    findings = pending_recurrence_duplicate_groups(rows)
    assert len(findings) == 1
    assert findings[0]["recurrence_id"] == str(rule)
    assert findings[0]["month"] == "2026-11"
    assert set(findings[0]["transaction_ids"]) == {str(first["id"]), str(second["id"])}
    assert findings[0]["requires_review"] is True
    assert rows == original


def test_two_paid_occurrences_are_not_pending_duplicates() -> None:
    rule = uuid4()
    rows = [
        movement(rule, date(2026, 11, 20), "POSTED"),
        movement(rule, date(2026, 11, 22), "POSTED"),
    ]
    assert pending_recurrence_duplicate_groups(rows) == []


def test_repeated_pending_dashboard_is_read_only(client: TestClient) -> None:
    account = client.post(
        "/api/v1/accounts", json={"name": "Essenciais", "opening_balance": "100.00"}
    )
    assert account.status_code == 201, account.text
    account_id = account.json()["id"]
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
    for day, key in [(20, "first"), (22, "second")]:
        created = client.post(
            "/api/v1/transactions",
            json={
                "type": "EXPENSE",
                "status": "PENDING",
                "description": f"Monthly subscription {day}",
                "amount": "10.00",
                "transaction_date": f"2026-11-{day:02d}",
                "account_id": account_id,
                "recurrence_id": rule_id,
                "idempotency_key": f"pr40-{key}",
            },
        )
        assert created.status_code == 201, created.text
    before_accounts = client.get("/api/v1/accounts").json()
    before_transactions = client.get("/api/v1/transactions").json()
    before_history = client.get("/api/v1/audit/transactions").json()
    report = client.get("/api/v1/dashboard/data-quality")
    assert report.status_code == 200, report.text
    result = report.json()
    assert result["summary"]["pending_recurrence_duplicate_groups"] == 1
    assert result["status"] == "REVIEW"
    section = next(
        part for part in result["sections"] if part["id"] == "repeated_pending_recurrences"
    )
    assert section["count"] == 1
    assert len(section["items"][0]["transaction_ids"]) == 2
    assert client.get("/api/v1/accounts").json() == before_accounts
    assert client.get("/api/v1/transactions").json() == before_transactions
    assert client.get("/api/v1/audit/transactions").json() == before_history
