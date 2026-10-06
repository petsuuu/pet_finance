from datetime import date
from decimal import Decimal
from typing import Any

from app.services.budgets import category_limits


def row(month: int, amount: str, **changes: Any) -> dict[str, Any]:
    return {
        "category_id": "cafe",
        "type": "EXPENSE",
        "status": "POSTED",
        "transaction_date": date(2026, month, 1),
        "description": "Cafe",
        "amount": Decimal(amount),
        **changes,
    }


def category(name: str = "Café e Padaria", expense_class: str = "SUPERFLUOUS") -> dict[str, Any]:
    return {"id": "cafe", "name": name, "expense_class": expense_class, "active": True}


def test_budget_median_excludes_vacation_and_refunds_reduce_spending() -> None:
    rows = [
        row(8, "100"),
        row(9, "300"),
        row(9, "1000", notes="Férias 2026"),
        row(9, "100", type="REFUND"),
        row(9, "500", status="CANCELLED"),
        row(10, "900"),
    ]
    result = category_limits([category()], rows, 2026, 10)[0]
    assert result["basis"]["median"] == "150"
    assert result["limit_amount"] == Decimal("135")
    assert result["basis"]["historical_months"] == ["2026-08", "2026-09"]
    assert result["basis"]["confidence"] == "LIMITED"


def test_budget_protects_imported_installments_and_work_meals() -> None:
    rows = [
        row(8, "100"),
        row(9, "100"),
        row(10, "220", status="PENDING", notes="PAR-SUBSCRIPTION; parcela 2/12"),
    ]
    result = category_limits([category()], rows, 2026, 10)[0]
    assert result["limit_amount"] == Decimal("220")
    assert category_limits([category("Refeições fora")], rows[:2], 2026, 10)[0][
        "limit_amount"
    ] == Decimal("100")


def test_budget_no_evidence_income_and_inactive_categories() -> None:
    cats = [
        category(),
        {**category(), "id": "income", "expense_class": None},
        {**category(), "id": "inactive", "active": False},
    ]
    result = category_limits(cats, [], 2026, 10)
    assert len(result) == 1
    assert result[0]["basis"]["confidence"] == "NO_EVIDENCE"
    assert result[0]["limit_amount"] == Decimal("0")


def test_budget_does_not_assume_partial_month_is_complete_or_duplicate_children() -> None:
    rows = [row(8, "100", transaction_date=date(2026, 8, 20)), row(9, "200")]
    cats = [category(), {**category(), "id": "parent", "name": "Alimentação"}]
    result = category_limits(cats, rows, 2026, 10)
    assert result[0]["basis"]["historical_months"] == ["2026-09"]
    assert result[0]["limit_amount"] == Decimal("180")
    assert result[1]["limit_amount"] == Decimal("0")
