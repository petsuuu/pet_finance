"""Diagnostic checks for expense classification and near-duplicate candidates.

Pure functions: no database writes, settlement, or recategorization.
"""

from collections import defaultdict
from difflib import SequenceMatcher
from typing import Any


def classification_findings(
    transactions: list[dict[str, Any]],
    categories: list[dict[str, Any]],
    merchants: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Report only verifiable classification anomalies, not inferred intent."""
    category_by_id = {row["id"]: row for row in categories}
    merchant_by_id = {row["id"]: row for row in merchants}
    findings: list[dict[str, Any]] = []
    for row in transactions:
        if row["type"] != "EXPENSE" or row["status"] == "CANCELLED":
            continue
        category_id = row.get("category_id")
        category = category_by_id.get(category_id)
        reason = None
        if category_id is None:
            reason = "missing_category"
        elif category is None or not category.get("active", True):
            reason = "inactive_or_missing_category"
        else:
            merchant = merchant_by_id.get(row.get("merchant_id"))
            expected = merchant.get("default_category_id") if merchant else None
            if expected is not None and expected != category_id:
                reason = "merchant_default_category_differs"
        if reason:
            findings.append(
                {
                    "transaction_id": str(row["id"]),
                    "category_id": None if category_id is None else str(category_id),
                    "reason": reason,
                    "requires_review": True,
                }
            )
    return sorted(findings, key=lambda item: item["transaction_id"])


def similar_description_duplicates(
    transactions: list[dict[str, Any]], *, threshold: float = 0.90
) -> list[dict[str, Any]]:
    """Compare only same-account/date/amount expenses; exact matches are handled elsewhere."""
    buckets: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in transactions:
        if row["type"] != "EXPENSE" or row["status"] == "CANCELLED":
            continue
        buckets[(row["account_id"], row["transaction_date"], row["amount"])].append(row)
    findings: list[dict[str, Any]] = []
    for rows in buckets.values():
        rows.sort(key=lambda row: str(row["id"]))
        for index, first in enumerate(rows):
            first_description = " ".join(first["description"].casefold().split())
            for second in rows[index + 1 :]:
                second_description = " ".join(second["description"].casefold().split())
                if first_description == second_description:
                    continue
                similarity = SequenceMatcher(None, first_description, second_description).ratio()
                if similarity < threshold:
                    continue
                findings.append(
                    {
                        "transaction_ids": [str(first["id"]), str(second["id"])],
                        "date": str(first["transaction_date"]),
                        "amount": str(first["amount"]),
                        "description": first["description"],
                        "other_description": second["description"],
                        "reason": "same_account_date_amount_similar_description",
                        "similarity": round(similarity, 3),
                        "requires_review": True,
                    }
                )
    return sorted(findings, key=lambda item: (item["date"], item["transaction_ids"]))


def pending_reference_findings(
    transactions: list[dict[str, Any]],
    accounts: list[dict[str, Any]],
    rules: dict[Any, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Flag invalid references only for active pending obligations.

    Historical POSTED/CANCELLED records are intentionally ignored.
    """
    account_by_id = {row["id"]: row for row in accounts}
    findings: list[dict[str, Any]] = []
    for movement in transactions:
        if movement["status"] != "PENDING":
            continue
        account_id = movement.get("account_id")
        account = account_by_id.get(account_id)
        if account is None or not account["active"]:
            findings.append(
                {
                    "transaction_id": str(movement["id"]),
                    "reference_id": None if account_id is None else str(account_id),
                    "reason": "missing_or_inactive_pending_account",
                    "requires_review": True,
                }
            )
        rule_id = movement.get("recurrence_id")
        if rule_id is not None and rule_id not in rules:
            findings.append(
                {
                    "transaction_id": str(movement["id"]),
                    "reference_id": str(rule_id),
                    "reason": "missing_or_archived_pending_recurrence",
                    "requires_review": True,
                }
            )
    return sorted(findings, key=lambda item: (item["transaction_id"], item["reason"]))


def pending_recurrence_duplicate_groups(
    transactions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Flag multiple pending occurrences of one recurrence in the same calendar month.

    Date/amount/description can vary following legitimate edits or rescheduling,
    so these groups remain review candidates, never automated corrections.
    """
    buckets: dict[tuple[Any, int, int], list[dict[str, Any]]] = defaultdict(list)
    for movement in transactions:
        recurrence_id = movement.get("recurrence_id")
        if movement["status"] != "PENDING" or recurrence_id is None:
            continue
        due = movement["transaction_date"]
        buckets[(recurrence_id, due.year, due.month)].append(movement)

    findings: list[dict[str, Any]] = []
    for (recurrence_id, year, month), rows in buckets.items():
        if len(rows) < 2:
            continue
        rows.sort(key=lambda row: str(row["id"]))
        findings.append(
            {
                "recurrence_id": str(recurrence_id),
                "month": f"{year:04d}-{month:02d}",
                "transaction_ids": [str(row["id"]) for row in rows],
                "dates": [str(row["transaction_date"]) for row in rows],
                "reason": "multiple_pending_occurrences_same_recurrence_month",
                "requires_review": True,
            }
        )
    return sorted(findings, key=lambda row: (row["month"], row["recurrence_id"]))
