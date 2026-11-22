"""Regression tests for the consolidated read-only financial quality view."""

from copy import deepcopy
from decimal import Decimal
from typing import Any

from fastapi.testclient import TestClient

from app.services.financial_quality import financial_quality_signals


def monthly() -> dict[str, Any]:
    return {
        "year": 2026,
        "month": 11,
        "as_of": "2026-11-22",
        "current_balance": Decimal("120"),
        "expected_income": Decimal("500"),
        "envelope_projections": {
            "items": [
                {
                    "account_id": "nonessential",
                    "name": "Não Essenciais",
                    "recorded_balance": Decimal("-181.57"),
                    "shortfall_without_income": Decimal("201.57"),
                },
                {
                    "account_id": "essential",
                    "name": "Essenciais",
                    "recorded_balance": Decimal("301.57"),
                    "shortfall_without_income": Decimal("0"),
                },
            ]
        },
        "category_budgets": {
            "items": [
                {
                    "status": "EXCEEDED",
                    "category_active": True,
                    "category": "Streaming",
                    "excess": Decimal("1"),
                },
                {
                    "status": "EXCEEDED",
                    "category_active": False,
                    "category": "Archived",
                    "excess": Decimal("99"),
                },
            ]
        },
        "monthly_planning": {
            "forecast_shortfall": Decimal("20"),
            "available_without_future_income": Decimal("0"),
        },
    }


def test_alerts_separate_risk_from_accounting_errors() -> None:
    data = monthly()
    before = deepcopy(data)
    report = financial_quality_signals({"status": "OK"}, data)
    assert report["integrity_status"] == "OK"
    assert report["risk_status"] == "REVIEW"
    assert report["status"] == "REVIEW"
    assert [alert["code"] for alert in report["financial_alerts"]] == [
        "NEGATIVE_ENVELOPE_BALANCE",
        "ENVELOPE_COMMITMENT_SHORTFALL",
        "CATEGORY_BUDGET_EXCEEDED",
        "FORECAST_SHORTFALL",
    ]
    assert report["financial_alerts"][0]["amount"] == Decimal("181.57")
    assert report["monthly_projection"]["available_without_future_income"] == Decimal("0")
    assert data == before


def test_clean_finances_are_ok_and_integrity_critical_takes_priority() -> None:
    data = monthly()
    data["envelope_projections"]["items"] = []
    data["category_budgets"]["items"] = []
    data["monthly_planning"]["forecast_shortfall"] = Decimal("0")
    assert financial_quality_signals({"status": "OK"}, data)["status"] == "OK"
    report = financial_quality_signals({"status": "CRITICAL"}, data)
    assert report["status"] == "CRITICAL"
    assert report["risk_status"] == "OK"


def test_new_endpoint_is_read_only_and_requires_authentication(client: TestClient) -> None:
    created = client.post(
        "/api/v1/accounts", json={"name": "Não Essenciais", "opening_balance": "10.00"}
    )
    assert created.status_code == 201
    account_id = created.json()["id"]
    movement = client.post(
        "/api/v1/transactions",
        json={
            "type": "EXPENSE",
            "status": "POSTED",
            "description": "Cinema",
            "amount": "20.00",
            "transaction_date": "2026-11-22",
            "account_id": account_id,
            "idempotency_key": "quality-pr56-cinema",
        },
    )
    assert movement.status_code == 201, movement.text
    params = {"year": 2026, "month": 11, "as_of": "2026-11-22"}
    before_accounts = client.get("/api/v1/accounts").json()
    before_transactions = client.get("/api/v1/transactions").json()
    before_audit = client.get("/api/v1/audit/transactions").json()
    response = client.get("/api/v1/dashboard/financial-quality", params=params)
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["read_only"] is True
    assert report["integrity_status"] != "CRITICAL"
    assert any(a["code"] == "NEGATIVE_ENVELOPE_BALANCE" for a in report["financial_alerts"])
    assert report["monthly_projection"]["expected_income_not_available"] == "0"
    assert client.get("/api/v1/accounts").json() == before_accounts
    assert client.get("/api/v1/transactions").json() == before_transactions
    assert client.get("/api/v1/audit/transactions").json() == before_audit
    assert (
        client.get("/api/v1/dashboard/financial-quality", params=params,
                   headers={"Authorization": ""}).status_code == 401
    )
    assert client.get(
        "/api/v1/dashboard/financial-quality",
        params={**params, "month": 13},
    ).status_code == 422
