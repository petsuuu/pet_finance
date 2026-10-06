from datetime import date
from decimal import Decimal
from typing import Any

from app.services.agenda import expense_agenda


def report(
    transactions: list[dict[str, Any]],
    rules: list[dict[str, Any]] | None = None,
    plans: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return expense_agenda(
        transactions,
        rules or [],
        plans or [],
        [{"id": "adjustment", "name": "Ajuste de Saldo"}],
        date(2026, 10, 1),
        date(2026, 10, 31),
        date(2026, 10, 5),
    )


def entry(identity: str, day: date, **changes: Any) -> dict[str, Any]:
    return {
        "id": identity,
        "transaction_date": day,
        "description": identity,
        "type": "EXPENSE",
        "status": "PENDING",
        "amount": Decimal("10"),
        **changes,
    }


def test_agenda_status_boundaries_and_cash_movements() -> None:
    result = report(
        [
            entry("old arrears", date(2026, 9, 9)),
            entry("old paid", date(2026, 9, 9), status="POSTED"),
            entry("paid", date(2026, 10, 3), status="POSTED"),
            entry("today", date(2026, 10, 5)),
            entry("next week", date(2026, 10, 12)),
            entry("later", date(2026, 10, 13)),
            entry("next month", date(2026, 11, 1)),
            entry("cancelled", date(2026, 10, 2), status="CANCELLED"),
            entry("card payment", date(2026, 10, 2), type="CARD_PAYMENT"),
            entry("transfer", date(2026, 10, 2), type="TRANSFER"),
            entry("technical correction", date(2026, 10, 2), category_id="adjustment"),
        ]
    )
    assert result["summary"] == {
        "paid": {"count": 1, "amount": Decimal("10")},
        "pending": {"count": 3, "amount": Decimal("30")},
        "overdue": {"count": 1, "amount": Decimal("10")},
    }
    assert [r["description"] for r in result["due_today"]] == ["today"]
    assert [r["description"] for r in result["next_seven_days"]] == ["next week"]


def test_missing_forecasts_never_duplicate_linked_paid_or_cancelled_occurrences() -> None:
    rule = {
        "id": "rule",
        "description": "Subscription",
        "type": "EXPENSE",
        "active": True,
        "frequency": "MONTHLY",
        "due_day": 9,
        "start_date": date(2026, 1, 1),
        "end_date": date(2028, 12, 31),
        "expected_amount": Decimal("30"),
    }
    plan = {
        "id": "plan",
        "description": "Purchase",
        "active": True,
        "total_installments": 3,
        "first_installment_date": date(2026, 9, 30),
        "installment_amount": Decimal("50"),
    }
    empty = report([], [rule], [plan])
    assert len(empty["missing_forecasts"]) == 2
    assert empty["summary"]["pending"]["amount"] == Decimal("80")
    assert all(r["transaction_id"] is None for r in empty["items"])
    assert empty["items"][1]["installment_number"] == 2
    linked = [
        entry("paid early", date(2026, 10, 1), recurrence_id="rule", status="POSTED"),
        entry(
            "cancelled installment",
            date(2026, 10, 30),
            installment_plan_id="plan",
            installment_number=2,
            status="CANCELLED",
        ),
    ]
    result = report(linked, [rule], [plan])
    assert result["missing_forecasts"] == []
    assert len(result["items"]) == 1
    assert linked[0]["status"] == "POSTED"


def test_annual_rules_inactive_plans_and_month_end_clamping() -> None:
    rule = {
        "id": "annual",
        "description": "Annual",
        "type": "EXPENSE",
        "active": True,
        "frequency": "YEARLY",
        "month_of_year": 8,
        "due_day": 9,
        "start_date": date(2026, 1, 1),
        "expected_amount": Decimal("30"),
    }
    plan = {
        "id": "plan",
        "description": "Purchase",
        "active": True,
        "total_installments": 2,
        "first_installment_date": date(2026, 9, 30),
        "installment_amount": Decimal("50"),
    }
    assert report([], [rule], [{**plan, "active": False}])["items"] == []
    result = expense_agenda(
        [], [], [plan], [], date(2026, 10, 1), date(2026, 10, 31), date(2026, 10, 31)
    )
    assert result["items"][0]["date"] == date(2026, 10, 30)
    assert result["summary"]["overdue"]["count"] == 1
