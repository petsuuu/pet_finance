"""Pending-reference auditing must preserve historical data and avoid writes."""

from datetime import date
from decimal import Decimal
from uuid import uuid4

from fastapi.testclient import TestClient

from app.services.preventive_audit import pending_reference_findings


def movement(status: str, account_id: object, rule_id: object = None) -> dict[str, object]:
    return {
        "id": uuid4(),
        "type": "EXPENSE",
        "status": status,
        "account_id": account_id,
        "recurrence_id": rule_id,
        "transaction_date": date(2026, 12, 1),
        "amount": Decimal("10.00"),
    }


def test_pending_only_flags_inactive_account_and_missing_rule() -> None:
    inactive, active, missing_rule = uuid4(), uuid4(), uuid4()
    rows = [
        movement("PENDING", inactive, missing_rule),
        movement("POSTED", inactive, missing_rule),
        movement("CANCELLED", inactive, missing_rule),
        movement("PENDING", active),
    ]
    before = [dict(row) for row in rows]
    alerts = pending_reference_findings(
        rows, [{"id": inactive, "active": False}, {"id": active, "active": True}], {}
    )
    assert {item["reason"] for item in alerts} == {
        "missing_or_inactive_pending_account",
        "missing_or_archived_pending_recurrence",
    }
    assert all(item["transaction_id"] == str(rows[0]["id"]) for item in alerts)
    assert rows == before


def test_pending_references_accept_valid_rule_and_account() -> None:
    account_id, rule_id = uuid4(), uuid4()
    rows = [movement("PENDING", account_id, rule_id)]
    assert (
        pending_reference_findings(
            rows, [{"id": account_id, "active": True}], {rule_id: {"id": rule_id}}
        )
        == []
    )


def test_dashboard_inactive_pending_account_is_read_only(client: TestClient) -> None:
    created = client.post("/api/v1/accounts", json={"name": "Account", "opening_balance": "100.00"})
    assert created.status_code == 201, created.text
    account_id = created.json()["id"]
    expense = client.post(
        "/api/v1/transactions",
        json={
            "type": "EXPENSE",
            "status": "PENDING",
            "description": "Future expense",
            "amount": "10.00",
            "transaction_date": "2026-12-01",
            "account_id": account_id,
            "idempotency_key": "pr39-pending-account",
        },
    )
    assert expense.status_code == 201, expense.text
    inactive = client.patch(f"/api/v1/accounts/{account_id}", json={"active": False})
    assert inactive.status_code == 200, inactive.text
    before_accounts = client.get("/api/v1/accounts").json()
    before_transactions = client.get("/api/v1/transactions").json()
    before_history = client.get("/api/v1/audit/transactions").json()
    report = client.get("/api/v1/dashboard/data-quality")
    assert report.status_code == 200, report.text
    assert report.json()["summary"]["pending_reference_findings"] == 1
    assert report.json()["status"] == "REVIEW"
    assert client.get("/api/v1/accounts").json() == before_accounts
    assert client.get("/api/v1/transactions").json() == before_transactions
    assert client.get("/api/v1/audit/transactions").json() == before_history
