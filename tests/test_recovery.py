from datetime import date
from decimal import Decimal as D
from typing import Any
from uuid import uuid4

from app.services.recovery import recovery_plan, stable_date


def fixture(balances: list[str], remaining: str = "0", target: str = "500") -> tuple[Any, Any]:
    flow = {
        "as_of": date(2026, 10, 6),
        "end_date": date(2026, 10, 6 + len(balances) - 1),
        "recorded_balance_as_of": D("-100"),
        "safety_margin": D("0"),
        "forecast_closing_balance": D(balances[-1]),
        "days": [
            {"date": date(2026, 10, 6 + i), "closing_balance": D(b), "events": []}
            for i, b in enumerate(balances)
        ],
    }
    budgets = {
        "savings_target": D(target),
        "unrecorded_planned_spending": D(remaining),
        "items": [],
    }
    return flow, budgets


def test_recovery_requires_staying_positive_not_first_crossing() -> None:
    f, b = fixture(["-100", "200", "-50", "600"])
    r = recovery_plan(f, b, [])
    assert r["recovery_date_known_obligations"] == date(2026, 10, 9)
    assert r["conditional_savings_date"] == date(2026, 10, 9)
    assert r["recorded_deficit"] == 100
    assert r["warnings"]


def test_unknown_variable_dates_are_explicit_and_charges_change_savings_date() -> None:
    f, b = fixture(["-100", "550", "550"], "100")
    r = recovery_plan(f, b, [])
    assert r["conditional_savings_date"] is None
    assert r["forecast_after_planned_spending"] == 450
    r = recovery_plan(f, b, [], D("60"))
    assert r["forecast_after_planned_spending"] == 390
    assert not any("Juros e encargos desconhecidos" in w for w in r["warnings"])


def test_reductions_preserve_essential_meals_and_registered_obligations() -> None:
    f, b = fixture(["-100", "600"], "300")
    cats = []
    for name, kind, amount in [
        ("Lazer", "SUPERFLUOUS", "100"),
        ("Escola", "ESSENTIAL", "100"),
        ("Refeições fora", "SUPERFLUOUS", "100"),
    ]:
        identity = uuid4()
        cats.append({"id": identity, "expense_class": kind})
        b["items"].append(
            {
                "category_id": identity,
                "category": name,
                "remaining": D(amount),
                "category_active": True,
                "status": "OK",
            }
        )
    r = recovery_plan(f, b, cats)
    assert r["required_reduction_for_goal"] == 200
    assert r["suggested_reduction_total"] == 100
    assert r["unresolved_shortfall_for_goal"] == 100
    assert [s["category"] for s in r["suggested_reductions"]] == ["Lazer"]
    assert r["conditional_savings_date"] is None


def test_today_expected_income_does_not_authorize_saving_today() -> None:
    f, b = fixture(["1000", "1000"])
    r = recovery_plan(f, b, [])
    assert r["conditional_savings_date"] == date(2026, 10, 7)
    assert stable_date([{"date": date(2026, 10, 6), "balance": D("-1")}], D("0")) is None


def test_last_day_counts_exact_variable_allowance_and_no_goal() -> None:
    f, b = fixture(["100", "100", "100"], "1")
    b["savings_target"] = None
    r = recovery_plan(f, b, [])
    assert r["forecast_after_planned_spending"] == 99
    assert r["conditional_savings_date"] is None
    assert "Meta de poupança não configurada." in r["warnings"]
