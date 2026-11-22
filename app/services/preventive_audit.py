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
