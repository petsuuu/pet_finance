"""Daily cash projection, without turning estimates into payments."""

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from app.services.agenda import expense_agenda
from app.services.occurrences import occurrence_dates

ZERO = Decimal("0")


def movement(row: dict[str, Any]) -> Decimal:
    if row["type"] in {"INCOME", "REFUND", "YIELD", "ADJUSTMENT"}:
        return Decimal(row["amount"])
    if row["type"] in {"EXPENSE", "CARD_PAYMENT"}:
        return -Decimal(row["amount"])
    # Both transfer legs belong to the same owner and cancel in the consolidated total.
    return ZERO


def daily_cashflow(
    accounts: list[dict[str, Any]],
    transactions: list[dict[str, Any]],
    rules: list[dict[str, Any]],
    plans: list[dict[str, Any]],
    categories: list[dict[str, Any]],
    as_of: date,
    end: date,
    safety_margin: Decimal = ZERO,
) -> dict[str, Any]:
    balance = sum((Decimal(a["opening_balance"]) for a in accounts), ZERO)
    balance += sum(
        (
            movement(r)
            for r in transactions
            if r["status"] == "POSTED" and r["transaction_date"] <= as_of
        ),
        ZERO,
    )
    current = balance
    events: dict[date, list[dict[str, Any]]] = {}

    def add(day: date, amount: Decimal, description: str, estimated: bool, overdue: bool) -> None:
        events.setdefault(day, []).append(
            {
                "description": description,
                "change": amount,
                "estimated": estimated,
                "overdue": overdue,
            }
        )

    for row in transactions:
        day = row["transaction_date"]
        if day > end or row["status"] == "CANCELLED":
            continue
        if row["status"] == "PENDING" or (row["status"] == "POSTED" and day > as_of):
            add(
                max(day, as_of),
                movement(row),
                row["description"],
                row["status"] == "PENDING",
                day < as_of,
            )
    start = as_of.replace(day=1)
    agenda = expense_agenda(transactions, rules, plans, categories, start, end, as_of)
    for row in agenda["missing_forecasts"]:
        add(max(row["date"], as_of), -row["amount"], row["description"], True, row["date"] < as_of)
    represented = {
        (r["recurrence_id"], r["transaction_date"].year, r["transaction_date"].month)
        for r in transactions
        if r.get("recurrence_id")
    }
    for rule in rules:
        if rule["active"] and rule["type"] in {"INCOME", "YIELD", "REFUND"}:
            for due in occurrence_dates(rule, start, end):
                if (rule["id"], due.year, due.month) not in represented:
                    add(
                        max(due, as_of),
                        rule["expected_amount"],
                        rule["description"],
                        True,
                        due < as_of,
                    )
    days: list[dict[str, Any]] = []
    day = as_of
    while day <= end:
        entries = events.get(day, [])
        inflow = sum((r["change"] for r in entries if r["change"] > ZERO), ZERO)
        outflow = -sum((r["change"] for r in entries if r["change"] < ZERO), ZERO)
        balance += inflow - outflow
        days.append(
            {
                "date": day,
                "inflow": inflow,
                "outflow": outflow,
                "closing_balance": balance,
                "after_safety_margin": balance - safety_margin,
                "events": entries,
            }
        )
        day += timedelta(days=1)
    lowest = min(days, key=lambda r: r["closing_balance"])
    return {
        "as_of": as_of,
        "end_date": end,
        "recorded_balance_as_of": current,
        "forecast_closing_balance": balance,
        "safety_margin": safety_margin,
        "lowest_balance": lowest["closing_balance"],
        "lowest_balance_date": lowest["date"],
        "first_negative_date": as_of
        if current < ZERO
        else next((r["date"] for r in days if r["closing_balance"] < ZERO), None),
        "days": days,
        "basis": "Consolidated recorded balance as of date; pending overdue movements assumed "
        "on as_of, then dated entries and missing active forecasts. No new variable "
        "spending or bank reconciliation; expected income is not guaranteed.",
    }
