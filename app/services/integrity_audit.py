"""Read-only checks for ledger integrity and potential duplicate expenses.

Findings are diagnostic: they never settle, recategorize or remove transactions.
"""

from collections import defaultdict
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from app.services.ledger import Ledger

ZERO = Decimal("0")


def inspect_integrity(service: Ledger, *, limit: int = 100) -> dict[str, Any]:
    accounts_table = service.table("accounts")
    transactions_table = service.table("transactions")
    rules_table = service.table("recurring_transactions")
    account_rows = [
        dict(row)
        for row in service.connection.execute(
            select(accounts_table).where(accounts_table.c.user_id == service.user_id)
        ).mappings()
    ]
    transactions = [
        dict(row)
        for row in service.connection.execute(
            select(transactions_table).where(
                transactions_table.c.user_id == service.user_id,
                transactions_table.c.archived_at.is_(None),
            )
        ).mappings()
    ]
    rules = {
        row["id"]: dict(row)
        for row in service.connection.execute(
            select(rules_table).where(
                rules_table.c.user_id == service.user_id,
                rules_table.c.archived_at.is_(None),
            )
        ).mappings()
    }
    recorded = {
        row["account_id"]: Decimal(row["current_balance"])
        for row in service.connection.execute(
            select(service.table("account_balances")).where(
                service.table("account_balances").c.user_id == service.user_id
            )
        ).mappings()
    }
    expected = {row["id"]: Decimal(row["opening_balance"]) for row in account_rows}
    account_map = {row["id"]: row for row in account_rows}

    def eligible(account_id: UUID | None, movement: dict[str, Any]) -> bool:
        account = account_map.get(account_id)
        if account is None or movement.get("baseline_included"):
            return False
        cutoff = account.get("opening_balance_date")
        return cutoff is None or movement["transaction_date"] >= cutoff

    for movement in transactions:
        if movement["status"] != "POSTED":
            continue
        source = movement["account_id"]
        target = movement.get("target_account_id")
        amount = Decimal(movement["amount"])
        kind = movement["type"]
        if kind == "TRANSFER":
            if eligible(source, movement):
                expected[source] -= amount
            if eligible(target, movement):
                expected[target] += amount
        elif eligible(source, movement):
            if kind in {"INCOME", "YIELD", "REFUND", "ADJUSTMENT"}:
                expected[source] += amount
            elif kind in {"EXPENSE", "CARD_PAYMENT"}:
                expected[source] -= amount

    balances = []
    for account in sorted(account_rows, key=lambda row: row["name"]):
        identity = account["id"]
        actual = recorded.get(identity)
        calculated = expected[identity]
        difference = None if actual is None else actual - calculated
        balances.append(
            {
                "account_id": str(identity),
                "account_name": account["name"],
                "include_in_summary": account["include_in_summary"],
                "recorded": None if actual is None else str(actual),
                "calculated": str(calculated),
                "difference": None if difference is None else str(difference),
                "matches": difference == ZERO,
            }
        )

    # Identical purchases are only candidates: separate real purchases can match.
    buckets: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for movement in transactions:
        if movement["type"] != "EXPENSE" or movement["status"] == "CANCELLED":
            continue
        key = (
            movement["account_id"],
            movement["transaction_date"],
            Decimal(movement["amount"]),
            " ".join(movement["description"].casefold().split()),
        )
        buckets[key].append(movement)
    duplicate_groups = []
    for group in buckets.values():
        if len(group) < 2:
            continue
        group.sort(key=lambda row: str(row["id"]))
        duplicate_groups.append(
            {
                "transaction_ids": [str(row["id"]) for row in group],
                "date": str(group[0]["transaction_date"]),
                "description": group[0]["description"],
                "amount": str(group[0]["amount"]),
                "reason": "same_account_date_amount_normalized_description",
                "requires_review": True,
            }
        )
    duplicate_groups.sort(key=lambda row: (row["date"], row["transaction_ids"]))

    recurrence_mismatches = []
    for movement in transactions:
        rule_id = movement.get("recurrence_id")
        if rule_id is None or movement["status"] == "CANCELLED":
            continue
        rule = rules.get(rule_id)
        if rule is None:
            continue
        if movement["account_id"] != rule["account_id"]:
            recurrence_mismatches.append(
                {
                    "transaction_id": str(movement["id"]),
                    "recurrence_id": str(rule_id),
                    "transaction_account_id": str(movement["account_id"]),
                    "rule_account_id": str(rule["account_id"]),
                    "status": movement["status"],
                    "requires_review": True,
                }
            )
    recurrence_mismatches.sort(key=lambda row: row["transaction_id"])

    return {
        "read_only": True,
        "summary": {
            "accounts_checked": len(balances),
            "balance_mismatches": sum(not row["matches"] for row in balances),
            "potential_duplicate_groups": len(duplicate_groups),
            "recurrence_account_mismatches": len(recurrence_mismatches),
        },
        "balances": balances,
        "potential_duplicates": duplicate_groups[:limit],
        "recurrence_account_mismatches": recurrence_mismatches[:limit],
        "truncated": {
            "potential_duplicates": len(duplicate_groups) > limit,
            "recurrence_account_mismatches": len(recurrence_mismatches) > limit,
        },
    }


def integrity_router(dependency: Any) -> APIRouter:
    router = APIRouter(prefix="/api/v1")
    Service = Annotated[Ledger, Depends(dependency)]

    @router.get("/audit/integrity")
    def integrity(
        service: Service,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> dict[str, Any]:
        return inspect_integrity(service, limit=limit)

    return router
