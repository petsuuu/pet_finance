from calendar import monthrange
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select

from app.schemas.inputs import Money
from app.services.budgets import budget_usage
from app.services.cashflow import daily_cashflow
from app.services.comparison import category_comparison
from app.services.ledger import Ledger
from app.services.occurrences import occurrence_dates
from app.services.radar import recurring_radar
from app.services.recovery import recovery_plan
from app.services.spending import assess_purchase

ZERO = Decimal("0")


def monthly_dashboard(
    service: Ledger, year: int, month: int, as_of: date, safety_margin: Decimal
) -> dict[str, Any]:
    start = date(year, month, 1)
    end = date(year, month, monthrange(year, month)[1])
    if not start <= as_of <= end:
        raise HTTPException(422, "as_of must belong to requested month")

    def rows(name: str) -> list[dict[str, Any]]:
        table = service.table(name)
        return [
            dict(r)
            for r in service.connection.execute(
                select(table).where(table.c.user_id == service.user_id)
            ).mappings()
        ]

    accounts = rows("accounts")
    transactions = rows("transactions")
    categories = {r["id"]: r for r in rows("categories")}
    rules = rows("recurring_transactions")
    balance = sum((r["opening_balance"] for r in accounts), ZERO)
    income = expense = pending_expense = pending_income = ZERO
    classes = {"ESSENTIAL": ZERO, "FUNDAMENTAL": ZERO, "SUPERFLUOUS": ZERO, "UNCLASSIFIED": ZERO}
    totals: dict[Any, Decimal] = {}
    for row in transactions:
        day = row["transaction_date"]
        amount = row["amount"]
        kind = row["type"]
        technical_adjustment = (
            categories.get(row["category_id"], {}).get("name") == "Ajuste de Saldo"
        )
        if row["status"] == "POSTED" and day <= as_of:
            if kind in {"INCOME", "YIELD", "REFUND", "ADJUSTMENT"}:
                balance += amount
            elif kind in {"EXPENSE", "CARD_PAYMENT"}:
                balance -= amount
            if day >= start and not technical_adjustment:
                if kind in {"INCOME", "YIELD"}:
                    income += amount
                elif kind in {"EXPENSE", "REFUND"}:
                    net = -amount if kind == "REFUND" else amount
                    expense += net
                    category = categories.get(row["category_id"], {})
                    classes[category.get("expense_class") or "UNCLASSIFIED"] += net
                    totals[row["category_id"]] = totals.get(row["category_id"], ZERO) + net
        elif row["status"] == "PENDING" and day <= end and not technical_adjustment:
            if kind == "EXPENSE":
                pending_expense += amount
            elif kind in {"INCOME", "YIELD", "REFUND"}:
                pending_income += amount
    # Linked transactions already represent the occurrence, including cancellation.
    represented = {
        (r["recurrence_id"], r["transaction_date"].year, r["transaction_date"].month)
        for r in transactions
        if r["recurrence_id"]
    }
    virtual_expense = virtual_income = ZERO
    for rule in rules:
        if not rule["active"]:
            continue
        due = next(occurrence_dates(rule, start, end), None)
        if due is None:
            continue
        if (rule["id"], due.year, due.month) in represented:
            continue
        if rule["type"] == "EXPENSE":
            virtual_expense += rule["expected_amount"]
        elif rule["type"] in {"INCOME", "YIELD", "REFUND"}:
            virtual_income += rule["expected_amount"]
    commitments = pending_expense + virtual_expense
    expected_income = pending_income + virtual_income
    budgets = budget_usage(service, year, month)
    planned = budgets["unrecorded_planned_spending"]
    target = budgets["savings_target"] or ZERO
    after_plan = balance + expected_income - commitments - planned - target - safety_margin
    result = {
        "year": year,
        "month": month,
        "as_of": as_of,
        "current_balance": balance,
        "income_month": income,
        "expense_month": expense,
        "month_result": income - expense,
        "effective_savings": None,
        "pending_commitments": commitments,
        "pending_recorded_expenses": pending_expense,
        "unrecorded_recurring_expenses": virtual_expense,
        "expected_income": expected_income,
        "safety_margin": safety_margin,
        "free_after_commitments": balance - commitments - safety_margin,
        "forecast_closing_balance": balance + expected_income - commitments,
        "forecast_after_safety_margin": balance + expected_income - commitments - safety_margin,
        "essential_expense": classes["ESSENTIAL"],
        "fundamental_expense": classes["FUNDAMENTAL"],
        "superfluous_expense": classes["SUPERFLUOUS"],
        "unclassified_expense": classes["UNCLASSIFIED"],
        "top_categories": [
            {
                "category_id": key,
                "name": categories.get(key, {}).get("name", "Sem categoria"),
                "amount": value,
            }
            for key, value in sorted(totals.items(), key=lambda pair: pair[1], reverse=True)[:5]
        ],
        "projection_basis": (
            "Pending transactions and active recurrence rules; no variable spending estimate"
        ),
        "category_budgets": budgets,
        "budget_planning": {
            "forecast_after_category_budgets_and_goal": after_plan,
            "planning_shortfall": max(ZERO, -after_plan),
            "liquidity_warning": balance < ZERO,
            "basis": "Expected income is not received cash. Remaining category plans, target "
            "and safety margin deducted from forecast; not a spending authorization.",
        },
    }
    flow = daily_cashflow(
        accounts,
        transactions,
        rules,
        rows("installment_plans"),
        list(categories.values()),
        as_of,
        end,
        safety_margin,
    )
    scenario = assess_purchase(flow, result, ZERO)
    result["spending_today"] = {
        "maximum_without_category": scenario["maximum_within_scenario"],
        "recorded_balance": scenario["recorded_balance"],
        "lowest_forecast_balance": flow["lowest_balance"],
        "first_negative_date": flow["first_negative_date"],
        "next_obligations": scenario["next_obligations"],
        "basis": scenario["basis"],
    }
    result["recovery_plan"] = recovery_plan(flow, budgets, list(categories.values()))
    result["category_comparison"] = category_comparison(
        list(categories.values()), transactions, as_of
    )
    result["recurring_radar"] = recurring_radar(
        rules, rows("installment_plans"), transactions, list(categories.values()), as_of
    )
    return result


def dashboard_router(dependency: Any) -> APIRouter:
    router = APIRouter(prefix="/api/v1")

    @router.get("/dashboard/monthly")
    def dashboard(
        year: int = Query(ge=2000, le=2100),
        month: int = Query(ge=1, le=12),
        as_of: date | None = None,
        safety_margin: Annotated[Money, Query(ge=0)] = Decimal("0"),
        service: Ledger = Depends(dependency),
    ) -> dict[str, Any]:
        today = as_of or datetime.now(ZoneInfo("America/Sao_Paulo")).date()
        if as_of is None and (year, month) < (today.year, today.month):
            today = date(year, month, monthrange(year, month)[1])
        return monthly_dashboard(service, year, month, today, safety_margin)

    return router
