"""Conservative monthly planning and income reconciliation; no writes."""

from datetime import date
from decimal import Decimal
from typing import Any

from app.services.occurrences import occurrence_dates

ZERO = Decimal("0")
INCOME_TYPES = {"INCOME", "YIELD", "REFUND"}


def income_reconciliation(
    transactions: list[dict[str, Any]],
    rules: list[dict[str, Any]],
    accounts: list[dict[str, Any]],
    start: date,
    end: date,
    as_of: date,
) -> dict[str, Any]:
    """Distinguish received money, recorded promises and unsaved forecasts.

    Only posted income dated on/before as_of is considered received.
    Cancelled occurrences suppress virtual regeneration but are not income.
    """
    account_by_id = {row["id"]: row for row in accounts}
    represented: set[tuple[Any, int, int]] = set()
    received = pending = future_posted = virtual = ZERO
    received_count = pending_count = 0
    recurring_received: dict[Any, Decimal] = {}
    recurring_pending: dict[Any, Decimal] = {}
    for row in transactions:
        due = row["transaction_date"]
        rule_id = row.get("recurrence_id")
        if rule_id is not None:
            represented.add((rule_id, due.year, due.month))
        if row["type"] not in INCOME_TYPES or not start <= due <= end:
            continue
        account = account_by_id.get(row.get("account_id"))
        if account is None or row.get("archived_at") is not None:
            continue
        cutoff = account.get("opening_balance_date")
        status = row["status"]
        amount = Decimal(row["amount"])
        if status == "POSTED":
            if row.get("baseline_included") or (cutoff is not None and due < cutoff):
                continue
            if due <= as_of:
                received += amount
                received_count += 1
                if rule_id is not None:
                    recurring_received[rule_id] = recurring_received.get(rule_id, ZERO) + amount
            else:
                future_posted += amount
        elif status == "PENDING":
            pending += amount
            pending_count += 1
            if rule_id is not None:
                recurring_pending[rule_id] = recurring_pending.get(rule_id, ZERO) + amount

    checks: list[dict[str, Any]] = []
    for rule in rules:
        if not rule["active"] or rule["type"] not in INCOME_TYPES:
            continue
        account = account_by_id.get(rule["account_id"])
        if account is None:
            continue
        for due in occurrence_dates(rule, start, end):
            cutoff = account.get("opening_balance_date")
            if cutoff is not None and due < cutoff:
                continue
            rule_id = rule["id"]
            saved = (rule_id, due.year, due.month) in represented
            expected = Decimal(rule["expected_amount"])
            if not saved:
                virtual += expected
            checks.append(
                {
                    "recurrence_id": str(rule_id),
                    "description": rule["description"],
                    "due_date": due,
                    "expected_amount": expected,
                    "received_amount": recurring_received.get(rule_id, ZERO),
                    "pending_amount": recurring_pending.get(rule_id, ZERO),
                    "unrecorded_forecast": expected if not saved else ZERO,
                    "state": (
                        "RECEIVED"
                        if recurring_received.get(rule_id, ZERO) > ZERO
                        else "PENDING_RECORDED"
                        if recurring_pending.get(rule_id, ZERO) > ZERO
                        else "FORECAST_ONLY"
                        if not saved
                        else "REPRESENTED_NO_RECEIPT"
                    ),
                }
            )
    checks.sort(key=lambda row: (row["due_date"], row["description"], row["recurrence_id"]))
    return {
        "received": received,
        "received_count": received_count,
        "pending_recorded": pending,
        "pending_count": pending_count,
        "unrecorded_forecast": virtual,
        "future_dated_posted_not_yet_received": future_posted,
        "total_expected_not_received": pending + virtual,
        "recurring_income_checks": checks,
        "basis": (
            "Only eligible posted income through as_of is received. Pending and "
            "missing active recurring income are estimates, not available cash. "
            "Future-dated posted entries are disclosed separately. Historical "
            "opening-balance entries and archived accounts are excluded."
        ),
    }


def planning_summary(
    balance: Decimal,
    commitments: Decimal,
    unrecorded_budget: Decimal,
    savings_target: Decimal,
    safety_margin: Decimal,
    expected_income: Decimal,
) -> dict[str, Any]:
    """Reserve known commitments and goals without crediting future income."""
    protected = balance - commitments - unrecorded_budget - savings_target - safety_margin
    forecast = protected + expected_income
    return {
        "recorded_balance": balance,
        "pending_and_forecast_commitments": commitments,
        "remaining_budget_reserve": unrecorded_budget,
        "savings_target": savings_target,
        "safety_margin": safety_margin,
        "expected_income_not_available": expected_income,
        "available_without_future_income": max(ZERO, protected),
        "unfunded_without_future_income": max(ZERO, -protected),
        "forecast_after_expected_income": forecast,
        "forecast_shortfall": max(ZERO, -forecast),
        "basis": (
            "Conservative planning ceiling, not a spend authorization. Expected "
            "income does not increase available cash. Budget remainder and "
            "recorded commitments are separately reserved; undocumented expenses "
            "and unavailable bank reconciliation may reduce actual availability."
        ),
    }
