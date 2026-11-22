"""Income reconciliation and conservative monthly planning regressions."""

from datetime import date
from decimal import Decimal
from uuid import uuid4

from fastapi.testclient import TestClient

from app.services.monthly_planning import income_reconciliation, planning_summary


def income(
    account: object,
    amount: str,
    status: str,
    day: int,
    recurrence: object = None,
    **extra: object,
) -> dict[str, object]:
    return {
        "id": uuid4(),
        "type": "INCOME",
        "account_id": account,
        "recurrence_id": recurrence,
        "amount": Decimal(amount),
        "status": status,
        "transaction_date": date(2026, 11, day),
        **extra,
    }


def test_income_receipts_are_distinct_from_promises_and_forecasts() -> None:
    account, rule_id, missing_id = uuid4(), uuid4(), uuid4()
    accounts = [{"id": account, "opening_balance_date": date(2026, 11, 12)}]
    rule = {
        "id": rule_id,
        "account_id": account,
        "description": "Salary",
        "type": "INCOME",
        "active": True,
        "frequency": "MONTHLY",
        "due_day": 20,
        "start_date": date(2026, 1, 1),
        "end_date": None,
        "expected_amount": Decimal("1000"),
    }
    missing = {**rule, "id": missing_id, "description": "Other income", "due_day": 25}
    entries = [
        income(account, "500", "POSTED", 13, rule_id),
        income(account, "500", "PENDING", 20, rule_id),
        income(account, "20", "POSTED", 22),
        income(account, "30", "POSTED", 8, baseline_included=True),
        income(account, "999", "CANCELLED", 23),
        income(account, "40", "POSTED", 28),
    ]
    original = [dict(row) for row in entries]
    result = income_reconciliation(
        entries,
        [rule, missing],
        accounts,
        date(2026, 11, 1),
        date(2026, 11, 30),
        date(2026, 11, 22),
    )
    assert result["received"] == Decimal("520")
    assert result["pending_recorded"] == Decimal("500")
    assert result["unrecorded_forecast"] == Decimal("1000")
    assert result["future_dated_posted_not_yet_received"] == Decimal("40")
    assert result["total_expected_not_received"] == Decimal("1500")
    assert result["recurring_income_checks"][0]["state"] == "RECEIVED"
    assert result["recurring_income_checks"][1]["state"] == "FORECAST_ONLY"
    assert entries == original


def test_cancelled_rule_occurrence_is_not_recreated_as_salary() -> None:
    account, rule_id = uuid4(), uuid4()
    rule = {
        "id": rule_id,
        "account_id": account,
        "description": "Salary",
        "type": "INCOME",
        "active": True,
        "frequency": "MONTHLY",
        "due_day": 20,
        "start_date": date(2026, 11, 1),
        "end_date": None,
        "expected_amount": Decimal("1000"),
    }
    result = income_reconciliation(
        [income(account, "1000", "CANCELLED", 20, rule_id)],
        [rule],
        [{"id": account, "opening_balance_date": None}],
        date(2026, 11, 1),
        date(2026, 11, 30),
        date(2026, 11, 22),
    )
    assert result["received"] == Decimal("0")
    assert result["unrecorded_forecast"] == Decimal("0")
    assert result["recurring_income_checks"][0]["state"] == "REPRESENTED_NO_RECEIPT"


def test_planning_never_credits_expected_income_as_spendable() -> None:
    result = planning_summary(
        Decimal("100"), Decimal("80"), Decimal("30"), Decimal("20"), Decimal("10"), Decimal("1000")
    )
    assert result["available_without_future_income"] == Decimal("0")
    assert result["unfunded_without_future_income"] == Decimal("40")
    assert result["forecast_after_expected_income"] == Decimal("960")
    assert result["forecast_shortfall"] == Decimal("0")
    assert result == planning_summary(
        Decimal("100"), Decimal("80"), Decimal("30"), Decimal("20"), Decimal("10"), Decimal("1000")
    )


def test_monthly_dashboard_is_read_only_and_reconciles_income(client: TestClient) -> None:
    account = client.post("/api/v1/accounts", json={"name": "Carteira", "opening_balance": "100"})
    assert account.status_code == 201, account.text
    account_id = account.json()["id"]
    recurrence = client.post(
        "/api/v1/recurrences",
        json={
            "description": "Salário",
            "type": "INCOME",
            "account_id": account_id,
            "expected_amount": "1000",
            "due_day": 25,
            "start_date": "2026-11-01",
        },
    )
    assert recurrence.status_code == 201, recurrence.text
    transaction = client.post(
        "/api/v1/transactions",
        json={
            "description": "Salário parcial",
            "amount": "200",
            "type": "INCOME",
            "status": "POSTED",
            "transaction_date": "2026-11-15",
            "account_id": account_id,
            "recurrence_id": recurrence.json()["id"],
            "idempotency_key": "pr42-salary-posted",
        },
    )
    assert transaction.status_code == 201, transaction.text
    accounts_before = client.get("/api/v1/accounts").json()
    transactions_before = client.get("/api/v1/transactions").json()
    history_before = client.get("/api/v1/audit/transactions").json()
    report = client.get(
        "/api/v1/dashboard/monthly",
        params={"year": 2026, "month": 11, "as_of": "2026-11-22"},
    )
    assert report.status_code == 200, report.text
    result = report.json()
    assert result["income_reconciliation"]["received"] == "200.00"
    assert result["income_reconciliation"]["unrecorded_forecast"] == "0"
    assert result["income_reconciliation"]["recurring_income_checks"][0]["state"] == "RECEIVED"
    assert result["monthly_planning"]["recorded_balance"] == "300.00"
    assert result["monthly_planning"]["available_without_future_income"] == "300.00"
    assert client.get("/api/v1/accounts").json() == accounts_before
    assert client.get("/api/v1/transactions").json() == transactions_before
    assert client.get("/api/v1/audit/transactions").json() == history_before
