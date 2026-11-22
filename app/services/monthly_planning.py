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
                    "remaining_against_expected": max(
                        ZERO, expected - recurring_received.get(rule_id, ZERO)
                    ),
                    "remaining_after_recorded_pending": max(
                        ZERO,
                        expected
                        - recurring_received.get(rule_id, ZERO)
                        - recurring_pending.get(rule_id, ZERO),
                    ),
                    "above_expected": max(
                        ZERO,
                        recurring_received.get(rule_id, ZERO)
                        + recurring_pending.get(rule_id, ZERO)
                        - expected,
                    ),
                    "state": (
                        "RECEIVED"
                        if recurring_received.get(rule_id, ZERO) >= expected
                        else "PARTIALLY_RECEIVED"
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

def envelope_projections(
    accounts: list[dict[str, Any]],
    transactions: list[dict[str, Any]],
    rules: list[dict[str, Any]],
    start: date,
    end: date,
    as_of: date,
) -> dict[str, Any]:
    """Per-account read-only liquidity outlook, excluding unconfirmed income.

    Category budgets and savings goals are global, not split across accounts;
    these account figures must not be interpreted as purchase allowances.
    """
    from app.services.scoping import account_balance

    represented = {
        (row["recurrence_id"], row["transaction_date"].year, row["transaction_date"].month)
        for row in transactions
        if row.get("recurrence_id") is not None and row.get("archived_at") is None
    }
    result: list[dict[str, Any]] = []
    for account in sorted(accounts, key=lambda row: (row["name"], str(row["id"]))):
        account_id = account["id"]
        balance = account_balance([account], transactions, as_of)
        expenses = forecast_expenses = expected_income = pending_out = pending_in = ZERO
        for row in transactions:
            if row["status"] != "PENDING" or row.get("archived_at") is not None:
                continue
            due = row["transaction_date"]
            if due > end:
                continue
            amount = Decimal(row["amount"])
            if row["type"] == "TRANSFER":
                if row.get("account_id") == account_id:
                    pending_out += amount
                if row.get("target_account_id") == account_id:
                    pending_in += amount
            elif row.get("account_id") == account_id:
                if row["type"] in {"EXPENSE", "CARD_PAYMENT"}:
                    expenses += amount
                elif row["type"] in INCOME_TYPES:
                    expected_income += amount

        for rule in rules:
            if not rule.get("active") or rule["account_id"] != account_id:
                continue
            for due in occurrence_dates(rule, start, end):
                cutoff = account.get("opening_balance_date")
                if cutoff is not None and due < cutoff:
                    continue
                if (rule["id"], due.year, due.month) in represented:
                    continue
                amount = Decimal(rule["expected_amount"])
                if rule["type"] == "EXPENSE":
                    forecast_expenses += amount
                elif rule["type"] in INCOME_TYPES:
                    expected_income += amount
        reserved = expenses + forecast_expenses + pending_out
        without_income = balance - reserved
        result.append(
            {
                "account_id": str(account_id),
                "name": account["name"],
                "recorded_balance": balance,
                "pending_expenses": expenses,
                "unrecorded_recurring_expenses": forecast_expenses,
                "pending_transfer_out": pending_out,
                "pending_transfer_in_not_available": pending_in,
                "expected_income_not_available": expected_income,
                "after_known_commitments_without_income": without_income,
                "projected_after_unconfirmed_inflows": (
                    without_income + expected_income + pending_in
                ),
                "shortfall_without_income": max(ZERO, -without_income),
                "needs_funding": without_income < ZERO,
            }
        )
    return {
        "items": result,
        "totals": {
            "recorded_balance": sum((row["recorded_balance"] for row in result), ZERO),
            "after_known_commitments_without_income": sum(
                (row["after_known_commitments_without_income"] for row in result), ZERO
            ),
            "pending_transfer_out": sum((row["pending_transfer_out"] for row in result), ZERO),
            "pending_transfer_in_not_available": sum(
                (row["pending_transfer_in_not_available"] for row in result), ZERO
            ),
        },
        "basis": (
            "Only included active accounts. Posted balances honor opening-balance "
            "cutoffs and transfers. Pending expenses, pending transfer outflows, "
            "and unrecorded active expense recurrences are reserved; promised income "
            "and transfer inflows are not available. Global category budgets, "
            "savings goals, unsaved installment schedules and safety margin are "
            "not allocated to envelopes. Account projections are not spendable limits."
        ),
    }
