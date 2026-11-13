"""Audited, atomic envelope reassignment for pending expenses only."""

from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select, text

from app.services.ledger import Ledger


def reassign_pending_account(
    service: Ledger,
    identity: UUID,
    target_account_id: UUID,
    *,
    apply_to_recurrence: bool = False,
    preview: bool = True,
) -> dict[str, Any]:
    # Serialize with payment and recurrence operations for this owner.
    service.connection.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:owner, 0))"),
        {"owner": str(service.user_id)},
    )
    transaction = service.get("transactions", identity)
    if transaction["status"] != "PENDING" or transaction["type"] != "EXPENSE":
        raise HTTPException(409, "Only pending expenses can change envelopes")
    if transaction.get("archived_at") is not None:
        raise HTTPException(409, "Archived expenses cannot be reassigned")
    service.reference("accounts", target_account_id)

    rule_id = transaction.get("recurrence_id")
    if rule_id and not apply_to_recurrence:
        raise HTTPException(
            422,
            "Linked recurring expenses require apply_to_recurrence=true",
        )
    if not rule_id and apply_to_recurrence:
        raise HTTPException(422, "Expense has no recurrence to update")

    affected = [transaction]
    rule = None
    if rule_id:
        rule = service.get("recurring_transactions", rule_id)
        if rule["account_id"] != transaction["account_id"]:
            raise HTTPException(409, "Selected occurrence does not match recurrence account")
        table = service.table("transactions")
        affected = [
            dict(row)
            for row in service.connection.execute(
                select(table)
                .where(
                    table.c.user_id == service.user_id,
                    table.c.recurrence_id == rule_id,
                    table.c.status == "PENDING",
                    table.c.archived_at.is_(None),
                    table.c.transaction_date >= transaction["transaction_date"],
                )
                .order_by(table.c.transaction_date, table.c.id)
                .with_for_update()
            ).mappings()
        ]
        # Protect against mixed-account recurrence data introduced by previous edits.
        if any(row["account_id"] != rule["account_id"] for row in affected):
            raise HTTPException(409, "Pending recurrence has mixed source accounts")

    changing = [row for row in affected if row["account_id"] != target_account_id]
    rule_change = bool(rule and rule["account_id"] != target_account_id)
    result: dict[str, Any] = {
        "preview": preview,
        "source_account_id": transaction["account_id"],
        "target_account_id": target_account_id,
        "selected_transaction_id": identity,
        "recurrence_id": rule_id if apply_to_recurrence else None,
        "affected_count": len(changing),
        "transaction_ids": [row["id"] for row in changing],
        "recurrence_will_change": rule_change,
        "balances_changed": False,
        "statuses_changed": False,
    }
    if preview:
        return result

    # The request is one database transaction via the API dependency.
    # Only pending entries are changed, hence balances never move.
    if rule_change:
        service.patch("recurring_transactions", rule_id, {"account_id": target_account_id})
    for row in changing:
        service.patch("transactions", row["id"], {"account_id": target_account_id})
    return result
