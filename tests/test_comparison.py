from datetime import date
from decimal import Decimal as D
from typing import Any
from uuid import uuid4

from app.services.comparison import category_comparison


def entry(category: Any, day: str, amount: str, **changes: Any) -> dict[str, Any]:
    return {
        "category_id": category,
        "transaction_date": date.fromisoformat(day),
        "amount": D(amount),
        "type": "EXPENSE",
        "status": "POSTED",
        "notes": None,
        **changes,
    }


def test_partial_month_uses_same_days_and_actual_holiday_spending() -> None:
    c = uuid4()
    categories = [{"id": c, "name": "Café", "active": True, "expense_class": "SUPERFLUOUS"}]
    rows = [
        entry(c, "2026-08-01", "10"),
        entry(c, "2026-09-02", "100"),
        entry(c, "2026-09-20", "900"),
        entry(c, "2026-10-02", "150", notes="Férias 2026"),
        entry(c, "2026-10-03", "10", type="REFUND"),
        entry(c, "2026-10-04", "500", status="PENDING"),
        entry(c, "2026-10-05", "500", status="CANCELLED"),
        entry(c, "2026-10-10", "500"),
    ]
    result = category_comparison(categories, rows, date(2026, 10, 6))
    r = result["items"][0]
    assert [m["paid_net"] for m in r["months"]] == [10, 1000, 140]
    assert r["matched_change_amount"] == 40
    assert r["matched_change_percent"] == 40
    assert r["trend"] == "GASTO_MAIOR"
    assert r["months"][-1]["extraordinary_net"] == 150
    assert result["periods"][-1]["is_partial"] is True


def test_direct_parent_archived_uncategorized_and_adjustments() -> None:
    parent, child, adjust, income = [uuid4() for _ in range(4)]
    cats = [
        {"id": parent, "name": "Food", "active": True, "expense_class": "ESSENTIAL"},
        {"id": child, "name": "Meals", "active": False, "expense_class": "ESSENTIAL"},
        {"id": adjust, "name": "Ajuste de Saldo", "active": True, "expense_class": "ESSENTIAL"},
        {"id": income, "name": "Salary", "active": True, "expense_class": None},
    ]
    rows = [
        entry(child, "2026-08-01", "30"),
        entry(None, "2026-10-01", "20"),
        entry(adjust, "2026-10-01", "999"),
        entry(income, "2026-10-01", "2000", type="INCOME"),
    ]
    r = category_comparison(cats, rows, date(2026, 10, 6))
    assert {i["category"] for i in r["items"]} == {"Food", "Meals", "Sem categoria"}
    assert [m["paid_net"] for m in r["totals"]] == [30, 0, 20]


def test_missing_history_zero_baseline_and_year_boundary() -> None:
    c = uuid4()
    cats = [{"id": c, "name": "Food", "active": True, "expense_class": "ESSENTIAL"}]
    r = category_comparison(cats, [entry(c, "2027-01-04", "10")], date(2027, 1, 6))
    assert [p["month"] for p in r["periods"]] == ["2026-11", "2026-12", "2027-01"]
    assert r["items"][0]["trend"] == "SEM_BASE_COMPARAVEL"
    assert r["items"][0]["matched_change_percent"] is None
    assert r["items"][0]["matched_change_amount"] is None


def test_short_month_and_lower_net_is_not_confirmed_savings() -> None:
    c = uuid4()
    cats = [{"id": c, "name": "Food", "active": True, "expense_class": "ESSENTIAL"}]
    rows = [
        entry(c, "2026-01-01", "1"),
        entry(c, "2026-02-28", "100"),
        entry(c, "2026-03-31", "75"),
    ]
    r = category_comparison(cats, rows, date(2026, 3, 31))
    assert r["periods"][1]["matched_until"] == date(2026, 2, 28)
    assert r["items"][0]["matched_change_percent"] == -25
    assert r["items"][0]["trend"] == "GASTO_MENOR"
