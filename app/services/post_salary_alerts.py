"""Read-only salary receipt readiness and post-salary envelope shortage alerts."""

import unicodedata
from datetime import date
from decimal import Decimal
from typing import Any

ZERO = Decimal("0")
PRIORITY = {"Essenciais": 0, "Não Essenciais": 1}


def _is_salary(description: str) -> bool:
    normalized = "".join(
        char
        for char in unicodedata.normalize("NFKD", description.casefold())
        if not unicodedata.combining(char)
    )
    return "salario" in normalized


def _salary_follow_up(
    salary_state: str, days_until_due: int | None, review_gate: str
) -> dict[str, Any]:
    """Select one read-only next step; never infer bank settlement from a due date."""
    if salary_state == "SALARY_RULE_NOT_IDENTIFIED":
        level = "REVIEW"
        action = "VERIFY_SALARY_RECURRENCE"
        reason = "No identifiable salary recurrence is available for confirmation."
    elif salary_state == "SALARY_RECORDED":
        level = "REVIEW"
        action = (
            "REVIEW_ENVELOPE_SUGGESTIONS"
            if review_gate == "REVIEW_RECORDED_CASH"
            else "REVIEW_RECORDED_SALARY"
        )
        reason = "Salary is recorded in the ledger, but bank settlement is not verified."
    elif days_until_due is not None and days_until_due < 0:
        level = "ATTENTION"
        action = "VERIFY_OVERDUE_SALARY"
        reason = "The scheduled date passed with salary not fully recorded."
    elif salary_state == "PARTIALLY_RECORDED":
        level = "ATTENTION"
        action = "RECONCILE_PARTIAL_SALARY"
        reason = "Only part of the expected salary is recorded."
    elif days_until_due is not None and days_until_due <= 3:
        level = "UPCOMING"
        action = "CHECK_UPCOMING_SALARY"
        reason = "The scheduled salary date is within the next three calendar days."
    else:
        level = "INFO"
        action = "WAIT_FOR_SALARY"
        reason = "Do not treat pending or forecast salary as available cash."
    return {
        "level": level,
        "action": action,
        "reason": reason,
        "requires_user_confirmation": True,
        "read_only": True,
    }


def _salary_receipt_reconciliation(salaries: list[dict[str, Any]]) -> dict[str, Any]:
    """Keep deficits separate: extra payment on one rule cannot settle another."""
    items: list[dict[str, Any]] = []
    for row in salaries:
        expected = Decimal(row["expected_amount"])
        posted = Decimal(row["received_amount"])
        pending = Decimal(row["pending_amount"])
        raw_due = row.get("due_date")
        due = (
            raw_due
            if isinstance(raw_due, date)
            else date.fromisoformat(str(raw_due))
            if raw_due is not None
            else None
        )
        unreceived = max(ZERO, expected - posted)
        items.append(
            {
                "recurrence_id": (
                    str(row["recurrence_id"]) if row.get("recurrence_id") is not None else None
                ),
                "description": str(row["description"]),
                "due_date": due,
                "expected": expected,
                "posted": posted,
                "pending_not_available": pending,
                "unreceived": unreceived,
                "not_recorded_after_pending": max(ZERO, unreceived - pending),
                "posted_above_expected": max(ZERO, posted - expected),
                "recording_state": (
                    "FULLY_POSTED"
                    if unreceived == ZERO
                    else "PARTIALLY_POSTED"
                    if posted > ZERO
                    else "PENDING_ONLY"
                    if pending > ZERO
                    else "NOT_RECORDED"
                ),
            }
        )
    items.sort(
        key=lambda item: (
            item["due_date"] or date.max,
            item["description"],
            item["recurrence_id"] or "",
        )
    )
    return {
        "items": items,
        "unreceived_total": sum((row["unreceived"] for row in items), ZERO),
        "not_recorded_after_pending_total": sum(
            (row["not_recorded_after_pending"] for row in items), ZERO
        ),
        "posted_above_expected_total": sum(
            (row["posted_above_expected"] for row in items), ZERO
        ),
        "all_fully_posted": bool(items)
        and all(row["unreceived"] == ZERO for row in items),
        "basis": (
            "Amounts are reconciled per salary recurrence without offsetting "
            "missing records against excess posted on another rule. POSTED is "
            "ledger evidence, not verified bank settlement; PENDING is not cash."
        ),
    }


def post_salary_envelope_alerts(
    income_report: dict[str, Any],
    envelope_report: dict[str, Any],
    allocation_report: dict[str, Any],
    as_of: date | None = None,
) -> dict[str, Any]:
    """Report shortages without claiming a bank deposit or scheduling transfers.

    Only income explicitly linked to a recognizable salary recurrence is
    treated as salary evidence. Other posted income cannot unlock that label.
    """
    salaries = [
        row
        for row in income_report["recurring_income_checks"]
        if _is_salary(str(row["description"]))
    ]
    received = sum((Decimal(row["received_amount"]) for row in salaries), ZERO)
    expected = sum((Decimal(row["expected_amount"]) for row in salaries), ZERO)
    pending = sum((Decimal(row["pending_amount"]) for row in salaries), ZERO)
    reconciliation = _salary_receipt_reconciliation(salaries)

    outstanding_due_dates = [
        row["due_date"]
        for row in reconciliation["items"]
        if row["unreceived"] > ZERO and row["due_date"] is not None
    ]
    earliest_due = min(outstanding_due_dates, default=None)
    days_until_due = (
        (earliest_due - as_of).days if earliest_due is not None and as_of is not None else None
    )

    if not salaries:
        salary_state = "SALARY_RULE_NOT_IDENTIFIED"
    elif reconciliation["all_fully_posted"]:
        salary_state = "SALARY_RECORDED"
    elif received > ZERO:
        salary_state = "PARTIALLY_RECORDED"
    else:
        salary_state = "AWAITING_RECEIPT"

    current = allocation_report["recorded_cash_scenario"]
    conditional = allocation_report["conditional_expected_income_scenario"]
    current_gaps = {
        row["account_id"]: Decimal(row["remaining_gap"]) for row in current["unfunded_envelopes"]
    }
    conditional_gaps = {
        row["account_id"]: Decimal(row["remaining_gap"])
        for row in conditional["unfunded_envelopes"]
    }
    alerts: list[dict[str, Any]] = []
    for row in sorted(
        envelope_report["items"],
        key=lambda item: (PRIORITY.get(item["name"], 99), item["name"]),
    ):
        if row["name"] not in PRIORITY:
            continue
        account_id = row["account_id"]
        gap = max(ZERO, -Decimal(row["after_known_commitments_without_income"]))
        after_current = current_gaps.get(account_id, gap)
        after_conditional = conditional_gaps.get(account_id, gap)
        if gap == ZERO and after_current == ZERO and after_conditional == ZERO:
            continue
        alerts.append(
            {
                "account_id": account_id,
                "envelope": row["name"],
                "priority": PRIORITY[row["name"]] + 1,
                "shortfall_before_suggestions": gap,
                "shortfall_after_recorded_cash_suggestions": after_current,
                "shortfall_after_conditional_suggestions": after_conditional,
                "severity": "ACTION_REVIEW" if salary_state == "SALARY_RECORDED" else "PLAN_AHEAD",
                "action": "REVIEW_ONLY",
            }
        )

    recorded_total = Decimal(current["suggested_total"])
    review_gate = (
        "REVIEW_RECORDED_CASH"
        if salary_state == "SALARY_RECORDED" and recorded_total > ZERO
        else "REVIEW_PARTIAL_RECEIPT"
        if salary_state == "PARTIALLY_RECORDED"
        else "REVIEW_SHORTFALLS"
        if salary_state == "SALARY_RECORDED"
        else "AWAIT_SALARY_CONFIRMATION"
    )
    return {
        "salary": {
            "state": salary_state,
            "recurrence_count": len(salaries),
            "expected": expected,
            "recorded_received": received,
            "recorded_pending": pending,
            "not_recorded_against_expected": reconciliation[
                "not_recorded_after_pending_total"
            ],
            "unreceived_against_expected": reconciliation["unreceived_total"],
            "recurrence_breakdown": reconciliation,
            "bank_deposit_verified": False,
            "earliest_outstanding_due_date": earliest_due,
            "days_until_due": days_until_due,
            "receipt_overdue": days_until_due is not None and days_until_due < 0,
            "overdue_days": max(0, -days_until_due) if days_until_due is not None else None,
        },
        "alerts": alerts,
        "shortfall_alert_count": len(alerts),
        "total_unfunded_after_recorded_suggestions": sum(
            (row["shortfall_after_recorded_cash_suggestions"] for row in alerts), ZERO
        ),
        "total_unfunded_after_conditional_suggestions": sum(
            (row["shortfall_after_conditional_suggestions"] for row in alerts), ZERO
        ),
        "recorded_cash_suggestions_available": recorded_total > ZERO,
        "review_gate": review_gate,
        "follow_up": _salary_follow_up(salary_state, days_until_due, review_gate),
        "requires_user_confirmation": True,
        "basis": (
            "Read-only planning alerts. Salary evidence requires eligible posted "
            "income linked to a salary-named recurrence; it does not verify a bank "
            "deposit. Each salary recurrence is reconciled separately so "
            "overpayments on one rule do not hide another rule's shortfall. "
            "The follow-up is informational and does not initiate "
            "notifications. A scheduled salary due date is not proof of receipt. "
            "Recorded-cash distribution remains capped by the global "
            "safety and savings plan; conditional income never becomes available "
            "cash. No transfers, payments, balances or transactions are changed."
        ),
    }
