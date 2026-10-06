from datetime import date
from decimal import Decimal as D
from typing import Any
from uuid import uuid4

from app.services.radar import recurring_radar


def rule(category: Any, **changes: Any) -> dict[str, Any]:
    return {
        "id": uuid4(),
        "category_id": category,
        "description": "Prime Video",
        "active": True,
        "type": "EXPENSE",
        "frequency": "YEARLY",
        "month_of_year": 7,
        "due_day": 9,
        "start_date": date(2026, 1, 1),
        "end_date": None,
        "expected_amount": D("120"),
        **changes,
    }


def test_annual_reference_calendar_cancellation_and_essential_protection() -> None:
    c = uuid4()
    cats = [{"id": c, "name": "Streaming e Mídia", "active": True, "expense_class": "SUPERFLUOUS"}]
    annual = rule(c)
    monthly = rule(
        c,
        description="Streaming",
        frequency="MONTHLY",
        due_day=10,
        expected_amount=D("20"),
        end_date=date(2026, 12, 31),
    )
    rows = [
        {
            "recurrence_id": monthly["id"],
            "description": "Streaming",
            "transaction_date": date(2026, 10, 10),
            "status": "CANCELLED",
            "type": "EXPENSE",
            "amount": D("20"),
            "category_id": c,
        }
    ]
    r = recurring_radar([annual, monthly], [], rows, cats, date(2026, 10, 6))
    by_name = {x["description"]: x for x in r["recurring_costs"]}
    assert by_name["Prime Video"]["annual_run_rate"] == 120
    assert by_name["Prime Video"]["monthly_equivalent"] == 10
    assert by_name["Prime Video"]["planned_unpaid_cost_next_12_months"] == 120
    assert by_name["Streaming"]["annual_run_rate"] == 240
    assert by_name["Streaming"]["planned_unpaid_cost_next_12_months"] == 40
    cats[0]["expense_class"] = "ESSENTIAL"
    assert not recurring_radar([annual], [], rows, cats, date(2026, 10, 6))["recurring_costs"][0][
        "review_candidate"
    ]


def test_migrated_installments_do_not_imply_payoff_and_duplicates_need_review() -> None:
    account, c = uuid4(), uuid4()
    rows = [
        {
            "account_id": account,
            "category_id": c,
            "recurrence_id": None,
            "installment_plan_id": None,
            "description": f"Laptop — parcela {n}/3",
            "notes": f"PAR-LAPTOP; parcela {n}/3",
            "amount": D("100"),
            "type": "EXPENSE",
            "status": "PENDING",
            "transaction_date": date(2026, 9 + n, 9),
        }
        for n in (1, 2, 3)
    ]
    r = recurring_radar([], [], rows, [], date(2026, 10, 6))
    ending = r["installments_ending_next_six_months"][0]
    assert ending["last_planned_date"] == date(2026, 12, 9)
    assert ending["release_from_month"] == date(2027, 1, 1)
    assert ending["potential_monthly_release"] == 100
    assert ending["payoff_confirmed"] is False
    r = recurring_radar([], [], rows + [rows[0]], [], date(2026, 10, 6))
    assert r["installments_ending_next_six_months"] == []
    assert r["installment_evidence_to_review"]


def test_persistent_growth_requires_three_closed_observed_months() -> None:
    c = uuid4()
    cats = [{"id": c, "name": "Food", "active": True, "expense_class": "ESSENTIAL"}]
    rows = [
        {
            "category_id": c,
            "description": "Food",
            "type": "EXPENSE",
            "status": "POSTED",
            "amount": D(a),
            "transaction_date": date(2026, m, 1),
            "notes": None,
        }
        for m, a in [(7, "10"), (8, "20"), (9, "30")]
    ]
    r = recurring_radar([], [], rows, cats, date(2026, 10, 6))
    assert r["growth_history_sufficient"] is True
    assert r["growing_categories_three_complete_months"][0]["increase_over_period"] == 20
    r = recurring_radar([], [], rows[1:], cats, date(2026, 10, 6))
    assert r["growth_history_sufficient"] is False
    assert r["growing_categories_three_complete_months"] == []
