"""Regression tests for the read-only data-quality dashboard."""

from typing import Any, cast
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.services.data_quality import data_quality_dashboard
from app.services.ledger import Ledger


def account(client: TestClient, name: str) -> str:
    response = client.post("/api/v1/accounts", json={"name": name, "opening_balance": "100.00"})
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def purchase(client: TestClient, account_id: str, key: str, *, force: bool = False) -> str:
    response = client.post(
        "/api/v1/transactions",
        json={
            "type": "EXPENSE",
            "status": "POSTED",
            "description": "Café",
            "amount": "10.00",
            "transaction_date": "2026-11-22",
            "account_id": account_id,
            "idempotency_key": key,
            "force": force,
        },
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def test_clean_dashboard_has_no_warnings(client: TestClient) -> None:
    account(client, "Carteira")
    response = client.get("/api/v1/dashboard/data-quality")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["read_only"] is True
    assert data["status"] == "OK"
    assert data["summary"]["accounts_checked"] == 1
    assert data["summary"]["total_findings"] == 0
    assert [section["id"] for section in data["sections"]] == [
        "balances",
        "potential_duplicates",
        "recurrence_accounts",
        "classification",
        "similar_duplicates",
    ]
    assert all(section["severity"] == "OK" for section in data["sections"])


def test_duplicate_dashboard_is_review_only_and_does_not_mutate(client: TestClient) -> None:
    source = account(client, "Carteira")
    one = purchase(client, source, "first")
    two = purchase(client, source, "second", force=True)
    before = client.get("/api/v1/transactions").json()
    history = client.get("/api/v1/audit/transactions").json()
    response = client.get("/api/v1/dashboard/data-quality")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == "REVIEW"
    assert data["summary"]["potential_duplicate_groups"] == 1
    assert data["summary"]["balance_mismatches"] == 0
    section = next(item for item in data["sections"] if item["id"] == "potential_duplicates")
    assert section["severity"] == "REVIEW"
    assert set(section["items"][0]["transaction_ids"]) == {one, two}
    assert client.get("/api/v1/transactions").json() == before
    assert client.get("/api/v1/audit/transactions").json() == history


def test_dashboard_has_authentication_and_limit_validation(client: TestClient) -> None:
    assert client.get("/api/v1/dashboard/data-quality", params={"limit": 0}).status_code == 422
    assert client.get("/api/v1/dashboard/data-quality", params={"limit": 501}).status_code == 422
    unauthenticated = client.get("/api/v1/dashboard/data-quality", headers={"Authorization": ""})
    assert unauthenticated.status_code == 401


def test_critical_balance_warning_takes_priority() -> None:
    report: dict[str, Any] = {
        "summary": {
            "accounts_checked": 1,
            "balance_mismatches": 1,
            "potential_duplicate_groups": 1,
            "recurrence_account_mismatches": 0,
        },
        "balances": [{"account_name": "Carteira", "matches": False, "difference": "1.00"}],
        "potential_duplicates": [{"requires_review": True}],
        "recurrence_account_mismatches": [],
        "truncated": {"potential_duplicates": False, "recurrence_account_mismatches": False},
    }
    with patch("app.services.data_quality.inspect_integrity", return_value=report):
        result = data_quality_dashboard(cast(Ledger, object()))
    assert result["status"] == "CRITICAL"
    assert result["summary"]["total_findings"] == 2
    assert result["sections"][0]["severity"] == "CRITICAL"
    assert result["sections"][0]["items"][0]["difference"] == "1.00"


def test_truncation_preserves_full_finding_count() -> None:
    report: dict[str, Any] = {
        "summary": {
            "accounts_checked": 0,
            "balance_mismatches": 0,
            "potential_duplicate_groups": 9,
            "recurrence_account_mismatches": 3,
        },
        "balances": [],
        "potential_duplicates": [{"requires_review": True}],
        "recurrence_account_mismatches": [{"requires_review": True}],
        "truncated": {"potential_duplicates": True, "recurrence_account_mismatches": True},
    }
    with patch("app.services.data_quality.inspect_integrity", return_value=report):
        result = data_quality_dashboard(cast(Ledger, object()), limit=1)
    assert result["summary"]["total_findings"] == 12
    assert result["sections"][1]["count"] == 9
    assert result["sections"][1]["truncated"] is True
    assert result["sections"][2]["count"] == 3
    assert result["sections"][2]["truncated"] is True
