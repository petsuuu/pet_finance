"""Scope operational finance calculations to active, included, non-archived data.

Historical and excluded-account transactions remain available in explicit
history queries but cannot inflate the available cash or projections.
"""

from decimal import Decimal
from typing import Any

from sqlalchemy import or_, select

from app.services.ledger import Ledger


def rows(service: Ledger, name: str) -> list[dict[str, Any]]:
    table = service.table(name)
    query = select(table).where(table.c.user_id == service.user_id)
    if name == "accounts":
        query = query.where(table.c.active.is_(True))
        if "include_in_summary" in table.c:
            query = query.where(table.c.include_in_summary.is_(True))
    elif name in {"transactions", "recurring_transactions", "installment_plans"}:
        accounts = service.table("accounts")
        eligible = select(accounts.c.id).where(
            accounts.c.user_id == service.user_id,
            accounts.c.active.is_(True),
        )
        if "include_in_summary" in accounts.c:
            eligible = eligible.where(accounts.c.include_in_summary.is_(True))
        if name == "transactions":
            query = query.where(
                or_(table.c.account_id.in_(eligible), table.c.target_account_id.in_(eligible))
            )
        else:
            query = query.where(table.c.account_id.in_(eligible))
        if "archived_at" in table.c:
            query = query.where(table.c.archived_at.is_(None))
    elif name == "budgets" and "archived_at" in table.c:
        query = query.where(table.c.archived_at.is_(None))
    return [dict(r) for r in service.connection.execute(query).mappings()]


def posted_after_opening_balance(row: dict[str, Any], accounts: list[dict[str, Any]]) -> bool:
    """Whether a posted movement belongs to the operating period after a rebase.

    The opening balance already incorporates older payments. This filter is for
    monthly spending/reporting only; account_balance independently protects cash.
    Pending obligations remain visible until they are reconciled.
    """
    if row.get("status") != "POSTED":
        return True
    if row.get("baseline_included"):
        return False
    account = next((a for a in accounts if a.get("id") == row.get("account_id")), None)
    if account is None:
        return False
    cutoff = account.get("opening_balance_date")
    return cutoff is None or row["transaction_date"] >= cutoff

def account_balance(
    accounts: list[dict[str, Any]], transactions: list[dict[str, Any]], as_of: Any
) -> Decimal:
    zero = Decimal("0")
    by_id = {a["id"]: a for a in accounts}
    total = sum((Decimal(a["opening_balance"]) for a in accounts), zero)

    def eligible(account: dict[str, Any] | None, row: dict[str, Any]) -> bool:
        if account is None or row.get("baseline_included"):
            return False
        cutoff = account.get("opening_balance_date")
        return cutoff is None or row["transaction_date"] >= cutoff

    for row in transactions:
        if row["status"] != "POSTED" or row["transaction_date"] > as_of:
            continue
        source = by_id.get(row["account_id"])
        target = by_id.get(row.get("target_account_id"))
        amount = Decimal(row["amount"])
        if row["type"] == "TRANSFER":
            if eligible(source, row):
                total -= amount
            if eligible(target, row):
                total += amount
            continue
        if not eligible(source, row):
            continue
        if row["type"] in {"INCOME", "YIELD", "REFUND", "ADJUSTMENT"}:
            total += amount
        elif row["type"] in {"EXPENSE", "CARD_PAYMENT"}:
            total -= amount
    return total
