from datetime import date
from decimal import Decimal as D
from typing import Any
from uuid import uuid4

from app.services.spending import assess_purchase


def scenario(balance: str, low: str, planned: str, remaining: str = "100") -> tuple[Any, Any, Any]:
    category = uuid4()
    flow = {
        "as_of": date(2026, 10, 6),
        "end_date": date(2026, 10, 31),
        "recorded_balance_as_of": D(balance),
        "safety_margin": D("0"),
        "lowest_balance": D(low),
        "days": [
            {
                "date": date(2026, 10, 6),
                "outflow": D("0"),
                "after_safety_margin": D(low),
                "events": [],
            },
        ],
    }
    dash = {
        "category_budgets": {
            "savings_target": D("500"),
            "items": [
                {
                    "category_id": category,
                    "category": "Lazer",
                    "remaining": D(remaining),
                    "status": "OK",
                }
            ],
        },
        "budget_planning": {"forecast_after_category_budgets_and_goal": D(planned)},
    }
    return flow, dash, category


def test_negative_cash_blocks_even_with_large_forecast_and_category() -> None:
    f, d, c = scenario("-100", "1000", "500")
    r = assess_purchase(f, d, D("20"), c)
    assert r["decision"] == "NAO_RECOMENDADO"
    assert r["maximum_within_scenario"] == 0
    assert r["recorded_balance_after_purchase"] == -120


def test_reserved_allowance_is_not_deducted_twice_and_limits_protected() -> None:
    f, d, c = scenario("300", "200", "3.91")
    r = assess_purchase(f, d, D("60"), c)
    assert r["decision"] == "CABE_NO_CENARIO"
    assert r["forecast_after_plans_and_purchase"] == D("3.91")
    assert r["maximum_within_scenario"] == 100
    assert assess_purchase(f, d, D("110"), c)["decision"] == "NAO_RECOMENDADO"


def test_dated_bills_and_today_unreceived_income_do_not_authorize_purchase() -> None:
    f, d, c = scenario("100", "500", "500")
    f["days"][0]["outflow"] = D("90")
    assert assess_purchase(f, d, D("20"), c)["decision"] == "NAO_RECOMENDADO"
    f["days"][0]["outflow"] = D("0")
    f["days"][0]["after_safety_margin"] = D("15")
    assert assess_purchase(f, d, D("20"), c)["maximum_within_scenario"] == 15


def test_missing_budget_requires_review_and_underfunded_goal_blocks() -> None:
    f, d, c = scenario("300", "200", "100")
    assert assess_purchase(f, d, D("20"))["decision"] == "REVISAR"
    d["budget_planning"]["forecast_after_category_budgets_and_goal"] = D("-1")
    assert assess_purchase(f, d, D("20"), c)["decision"] == "NAO_RECOMENDADO"


def test_underfunded_plan_never_suggests_positive_capacity() -> None:
    f, d, c = scenario("300", "200", "-50")
    assert assess_purchase(f, d, D("20"), c)["maximum_within_scenario"] == 0
