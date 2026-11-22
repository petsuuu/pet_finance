"""Read-only salary receipt readiness and post-salary envelope shortage alerts."""

from decimal import Decimal
from typing import Any
import unicodedata

ZERO = Decimal("0")
PRIORITY = {"Essenciais": 0, "Não Essenciais": 1}


def _is_salary(description: str) -> bool:
    normalized = "".join(
        char for char in unicodedata.normalize("NFKD", description.casefold())
        if not unicodedata.combining(char)
    )
    return "salario" in normalized


def post_salary_envelope_alerts(
    income_report: dict[str, Any],
    envelope_report: dict[str, Any],
    allocation_report: dict[str, Any],
) -> dict[str, Any]:
    """Report shortages without claiming a bank deposit or scheduling transfers.

    Only income explicitly linked to a recognizable salary recurrence is
    treated as salary evidence. Other posted income cannot unlock that label.
    """
    salaries = [
        row for row in income_report["recurring_income_checks"]
        if _is_salary(str(row["description"]))
    ]
    received = sum((Decimal(row["received_amount"]) for row in salaries), ZERO)
    expected = sum((Decimal(row["expected_amount"]) for row in salaries), ZERO)
    pending = sum((Decimal(row["pending_amount"]) for row in salaries), ZERO)

    if not salaries:
        salary_state = "SALARY_RULE_NOT_IDENTIFIED"
    elif received >= expected:
        salary_state = "SALARY_RECORDED"
    elif received > ZERO:
        salary_state = "PARTIALLY_RECORDED"
    else:
        salary_state = "AWAITING_RECEIPT"

    current = allocation_report["recorded_cash_scenario"]
    conditional = allocation_report["conditional_expected_income_scenario"]
    current_gaps = {
        row["account_id"]: Decimal(row["remaining_gap"])
        for row in current["unfunded_envelopes"]
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
    return {
        "salary": {
            "state": salary_state,
            "recurrence_count": len(salaries),
            "expected": expected,
            "recorded_received": received,
            "recorded_pending": pending,
            "not_recorded_against_expected": max(ZERO, expected - received - pending),
            "bank_deposit_verified": False,
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
        "review_gate": (
            "REVIEW_RECORDED_CASH"
            if salary_state == "SALARY_RECORDED" and recorded_total > ZERO
            else "REVIEW_PARTIAL_RECEIPT"
            if salary_state == "PARTIALLY_RECORDED"
            else "REVIEW_SHORTFALLS"
            if salary_state == "SALARY_RECORDED"
            else "AWAIT_SALARY_CONFIRMATION"
        ),
        "requires_user_confirmation": True,
        "basis": (
            "Read-only planning alerts. Salary evidence requires eligible posted "
            "income linked to a salary-named recurrence; it does not verify a bank "
            "deposit. Recorded-cash distribution remains capped by the global "
            "safety and savings plan; conditional income never becomes available "
            "cash. No transfers, payments, balances or transactions are changed."
        ),
    }
