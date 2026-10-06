from datetime import date
from decimal import Decimal
from typing import Any

from app.services.cashflow import daily_cashflow


def entry(kind: str, amount: str, day: int, **changes: Any) -> dict[str, Any]:
    return {
        "type": kind,
        "amount": Decimal(amount),
        "transaction_date": date(2026, 10, day),
        "status": "PENDING",
        "description": kind,
        **changes,
    }


def test_cashflow_negative_before_income_and_no_double_count_of_posted_today() -> None:
    rows = [
        entry("EXPENSE", "20", 6, status="POSTED"),
        entry("EXPENSE", "10", 2),
        entry("EXPENSE", "80", 7),
        entry("INCOME", "100", 8),
        entry("EXPENSE", "999", 7, status="CANCELLED"),
        entry("TRANSFER", "70", 7),
    ]
    result = daily_cashflow(
        [{"opening_balance": Decimal("100")}],
        rows,
        [],
        [],
        [],
        date(2026, 10, 6),
        date(2026, 10, 9),
        Decimal("5"),
    )
    assert result["recorded_balance_as_of"] == Decimal("80")
    assert result["days"][0]["closing_balance"] == Decimal("70")
    assert result["days"][0]["events"][0]["overdue"] is True
    assert result["lowest_balance"] == Decimal("-10")
    assert result["first_negative_date"] == date(2026, 10, 7)
    assert result["forecast_closing_balance"] == Decimal("90")
    assert result["days"][-1]["after_safety_margin"] == Decimal("85")


def test_cashflow_missing_recurring_estimates_and_cancelled_occurrences() -> None:
    expense = {
        "id": "bill",
        "description": "Bill",
        "type": "EXPENSE",
        "active": True,
        "frequency": "MONTHLY",
        "due_day": 3,
        "start_date": date(2026, 10, 1),
        "expected_amount": Decimal("20"),
    }
    income = {**expense, "id": "income", "type": "INCOME", "due_day": 8}
    annual = {**expense, "id": "annual", "frequency": "YEARLY", "month_of_year": 8}
    result = daily_cashflow(
        [{"opening_balance": Decimal("0")}],
        [],
        [expense, income, annual],
        [],
        [],
        date(2026, 10, 6),
        date(2026, 10, 9),
    )
    assert result["days"][0]["outflow"] == Decimal("20")
    assert result["days"][2]["inflow"] == Decimal("20")
    assert result["forecast_closing_balance"] == Decimal("0")
    cancelled = entry("EXPENSE", "20", 3, status="CANCELLED", recurrence_id="bill")
    result = daily_cashflow(
        [{"opening_balance": Decimal("0")}],
        [cancelled],
        [expense],
        [],
        [],
        date(2026, 10, 6),
        date(2026, 10, 9),
    )
    assert result["forecast_closing_balance"] == Decimal("0")
