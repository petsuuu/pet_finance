"""Preventive audit regression tests: all checks are diagnostic only."""

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient

from app.services.preventive_audit import classification_findings, similar_description_duplicates


def transaction(
    description: str, *, category_id: Any = None, status: str = "POSTED"
) -> dict[str, Any]:
    return {
        "id": uuid4(),
        "type": "EXPENSE",
        "status": status,
        "account_id": uuid4(),
        "transaction_date": date(2026, 11, 22),
        "amount": Decimal("21.55"),
        "description": description,
        "category_id": category_id,
        "merchant_id": None,
    }


def test_missing_and_inactive_categories_need_review() -> None:
    active_id, inactive_id = uuid4(), uuid4()
    rows = [
        transaction("No category"),
        transaction("Inactive", category_id=inactive_id),
        transaction("Active", category_id=active_id),
        transaction("Canceled", status="CANCELLED"),
    ]
    original = [dict(item) for item in rows]
    findings = classification_findings(
        rows,
        [
            {"id": active_id, "active": True},
            {"id": inactive_id, "active": False},
        ],
        [],
    )
    assert {item["reason"] for item in findings} == {
        "missing_category",
        "inactive_or_missing_category",
    }
    assert rows == original


def test_merchant_default_is_only_a_suggestion() -> None:
    category_id, expected_id, merchant_id = uuid4(), uuid4(), uuid4()
    row = transaction("Other product", category_id=category_id)
    row["merchant_id"] = merchant_id
    findings = classification_findings(
        [row],
        [{"id": category_id, "active": True}],
        [{"id": merchant_id, "default_category_id": expected_id}],
    )
    assert findings[0]["reason"] == "merchant_default_category_differs"
    assert findings[0]["requires_review"] is True


def test_similar_descriptions_are_candidates_not_confirmed_errors() -> None:
    first = transaction("Mercado Central")
    second = dict(first, id=uuid4(), description="Mercado CentraI")
    third = dict(first, id=uuid4(), description="Oficina mecânica")
    fourth = dict(second, id=uuid4(), account_id=uuid4())
    fifth = dict(second, id=uuid4(), status="CANCELLED")
    exact = dict(first, id=uuid4())
    rows = [first, second, third, fourth, fifth, exact]
    original = [dict(item) for item in rows]
    findings = similar_description_duplicates(rows)
    assert len(findings) == 2
    assert all(
        item["reason"] == "same_account_date_amount_similar_description" for item in findings
    )
    assert rows == original


def test_dashboard_protects_ledger_and_audit_history(client: TestClient) -> None:
    account = client.post(
        "/api/v1/accounts", json={"name": "Carteira", "opening_balance": "100.00"}
    )
    assert account.status_code == 201
    account_id = account.json()["id"]
    created = client.post(
        "/api/v1/transactions",
        json={
            "type": "EXPENSE",
            "status": "POSTED",
            "description": "Sem categoria",
            "amount": "10.00",
            "transaction_date": "2026-11-22",
            "account_id": account_id,
            "idempotency_key": "pr38-missing-category",
        },
    )
    assert created.status_code == 201, created.text
    balance_before = client.get("/api/v1/accounts").json()
    transactions_before = client.get("/api/v1/transactions").json()
    audit_before = client.get("/api/v1/audit/transactions").json()
    report = client.get("/api/v1/dashboard/data-quality")
    assert report.status_code == 200, report.text
    data = report.json()
    assert data["status"] == "REVIEW"
    assert data["summary"]["classification_findings"] == 1
    assert data["summary"]["similar_duplicate_pairs"] == 0
    assert data["summary"]["balance_mismatches"] == 0
    assert client.get("/api/v1/accounts").json() == balance_before
    assert client.get("/api/v1/transactions").json() == transactions_before
    assert client.get("/api/v1/audit/transactions").json() == audit_before
