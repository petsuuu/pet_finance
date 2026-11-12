"""Explicit, reversible ledger rebase from an independently verified source.

The endpoint never deletes historical rows and never turns opening balances into
income. It is intentionally opt-in, guarded by snapshot counts, backed by a
separate audit log, and atomic with the account/category/transaction import.
"""

import hashlib
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field, model_validator
from sqlalchemy import func, insert, select, text, update

from app.schemas.inputs import Input, Money
from app.services.ledger import Ledger

Kind = Literal["EXPENSE", "INCOME", "REFUND", "YIELD"]
State = Literal["POSTED", "PENDING"]
AccountType = Literal["CHECKING", "SAVINGS", "CASH", "INVESTMENT", "OTHER"]


class BaselineAccount(Input):
    name: str = Field(min_length=1, max_length=200)
    opening_balance: Money
    include_in_summary: bool = True
    type: AccountType = "CASH"


class BaselineMovement(Input):
    source_id: str = Field(min_length=1, max_length=200)
    transaction_date: date
    description: str = Field(min_length=1, max_length=200)
    amount: Money = Field(gt=0)
    type: Kind
    status: State
    account_name: str = Field(min_length=1, max_length=200)
    category: str | None = Field(default=None, max_length=200)
    subcategory: str | None = Field(default=None, max_length=200)
    expense_class: Literal["ESSENTIAL", "FUNDAMENTAL", "SUPERFLUOUS"] | None = None
    tags: list[str] = Field(default_factory=list, max_length=20)
    notes: str | None = Field(default=None, max_length=5000)


class FinancialRebase(Input):
    batch_key: str = Field(min_length=8, max_length=120)
    as_of_date: date
    accounts: list[BaselineAccount] = Field(min_length=1, max_length=25)
    transactions: list[BaselineMovement] = Field(min_length=1, max_length=500)
    expected_active_transactions: int = Field(ge=0)
    backup_confirmed: bool = False

    @model_validator(mode="after")
    def validate_references(self) -> "FinancialRebase":
        names = [a.name for a in self.accounts]
        if len(names) != len(set(names)):
            raise ValueError("Duplicate account name")
        identities = [r.source_id for r in self.transactions]
        if len(identities) != len(set(identities)):
            raise ValueError(
                "Duplicate source IDs; distinct purchases require different source IDs"
            )
        for row in self.transactions:
            if row.account_name not in names:
                raise ValueError(f"Unknown account: {row.account_name}")
            if row.status == "POSTED" and row.transaction_date > self.as_of_date:
                raise ValueError("Future POSTED movement is inconsistent with cut-off balances")
            if row.subcategory and not row.category:
                raise ValueError("Subcategory requires a parent category")
            if len(set(row.tags)) != len(row.tags):
                raise ValueError("Duplicate tag in source row")
        return self


def digest(body: FinancialRebase) -> str:
    canonical = body.model_dump(mode="json")
    encoded = json.dumps(canonical, sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def totals(body: FinancialRebase) -> dict[str, str]:
    result: dict[str, Decimal] = {}
    for row in body.transactions:
        key = f"{row.type}:{row.status}"
        result[key] = result.get(key, Decimal("0")) + Decimal(row.amount)
    return {k: str(v) for k, v in sorted(result.items())}


def inspection(service: Ledger, body: FinancialRebase) -> dict[str, Any]:
    transactions = service.table("transactions")
    current = service.connection.execute(
        select(func.count())
        .select_from(transactions)
        .where(
            transactions.c.user_id == service.user_id,
            transactions.c.archived_at.is_(None),
        )
    ).scalar_one()
    account_table = service.table("accounts")
    names = [
        row["name"]
        for row in service.connection.execute(
            select(account_table).where(
                account_table.c.user_id == service.user_id,
                account_table.c.active.is_(True),
            )
        ).mappings()
    ]
    return {
        "safe_to_commit": body.backup_confirmed and current == body.expected_active_transactions,
        "backup_confirmed": body.backup_confirmed,
        "existing_live_transactions": current,
        "expected_live_transactions": body.expected_active_transactions,
        "existing_account_names": sorted(names),
        "new_accounts": [
            {
                "name": a.name,
                "opening_balance": str(a.opening_balance),
                "include_in_summary": a.include_in_summary,
            }
            for a in body.accounts
        ],
        "new_transactions": len(body.transactions),
        "source_totals": totals(body),
        "cutoff": body.as_of_date.isoformat(),
        "included_starting_balance": str(
            sum((a.opening_balance for a in body.accounts if a.include_in_summary), Decimal("0"))
        ),
    }


def commit(service: Ledger, body: FinancialRebase) -> dict[str, Any]:
    # Externally audited backup and a fixed source count are mandatory.
    if not body.backup_confirmed:
        raise HTTPException(422, "Commit requires backup_confirmed=true")
    service.connection.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:u,0))"),
        {"u": str(service.user_id)},
    )
    batches = service.table("financial_rebases")
    identifier = digest(body)
    existing = (
        service.connection.execute(
            select(batches).where(
                batches.c.user_id == service.user_id, batches.c.batch_key == body.batch_key
            )
        )
        .mappings()
        .first()
    )
    if existing is not None:
        if existing["payload_hash"] != identifier:
            raise HTTPException(409, "Rebase key already used with a different payload")
        return dict(existing["result"])
    plan = inspection(service, body)
    if not plan["safe_to_commit"]:
        raise HTTPException(409, {"error": "Snapshot mismatch; preview again", "preview": plan})
    stamp = datetime.now(timezone.utc)

    # Preserve the old account IDs and transaction links for audit, but free
    # original names for the fresh accounts. Never delete user data.
    old_accounts = service.table("accounts")
    old_rows = list(
        service.connection.execute(
            select(old_accounts).where(old_accounts.c.user_id == service.user_id)
        ).mappings()
    )
    for old_account in old_rows:
        archived_name = (
            f"Arquivo {body.as_of_date.isoformat()} "
            f"{str(old_account['id'])[:8]} - {old_account['name']}"
        )
        service.patch(
            "accounts",
            old_account["id"],
            {"name": archived_name[:200], "active": False, "include_in_summary": False},
        )
    archived: dict[str, int] = {}
    for table_name in ("transactions", "recurring_transactions", "installment_plans", "budgets"):
        table = service.table(table_name)
        result = service.connection.execute(
            update(table)
            .where(table.c.user_id == service.user_id, table.c.archived_at.is_(None))
            .values(archived_at=stamp)
        )
        archived[table_name] = result.rowcount or 0
    preference = service.table("budget_preferences")
    service.connection.execute(
        update(preference).where(preference.c.user_id == service.user_id).values(enabled=False)
    )

    ids: dict[str, UUID] = {}
    for account in body.accounts:
        created = service.create(
            "accounts",
            {
                "name": account.name,
                "type": account.type,
                "currency_code": "BRL",
                "opening_balance": account.opening_balance,
                "opening_balance_date": body.as_of_date,
                "include_in_summary": account.include_in_summary,
                "active": True,
            },
        )
        ids[account.name] = created["id"]

    categories = service.table("categories")
    cat_cache: dict[tuple[str, UUID | None], UUID] = {}
    for c in service.connection.execute(
        select(categories).where(categories.c.user_id == service.user_id)
    ).mappings():
        cat_cache[(c["name"], c["parent_id"])] = c["id"]

    def category_id(name: str | None, parent: UUID | None, kind: str | None) -> UUID | None:
        if not name:
            return None
        key = (name, parent)
        if key not in cat_cache:
            created = service.create(
                "categories",
                {
                    "name": name,
                    "parent_id": parent,
                    "expense_class": kind,
                    "active": True,
                },
            )
            cat_cache[key] = created["id"]
        return cat_cache[key]

    tags = service.table("tags")
    tag_ids = {
        t["name"]: t["id"]
        for t in service.connection.execute(
            select(tags).where(tags.c.user_id == service.user_id)
        ).mappings()
    }
    tx_tags = service.table("transaction_tags")
    for row in body.transactions:
        parent = category_id(row.category, None, row.expense_class)
        leaf = category_id(row.subcategory, parent, row.expense_class)
        transaction = service.create(
            "transactions",
            {
                "type": row.type,
                "status": row.status,
                "description": row.description,
                "amount": row.amount,
                "currency_code": "BRL",
                "transaction_date": row.transaction_date,
                "account_id": ids[row.account_name],
                "category_id": leaf or parent,
                "source": "IMPORT",
                "baseline_included": row.status == "POSTED",
                "external_id": f"{body.batch_key}:{row.source_id}",
                "notes": row.notes,
            },
        )
        for tag in row.tags:
            if tag not in tag_ids:
                tag_ids[tag] = service.create("tags", {"name": tag, "active": True})["id"]
            service.connection.execute(
                insert(tx_tags).values(transaction_id=transaction["id"], tag_id=tag_ids[tag])
            )

    data = {
        "batch_key": body.batch_key,
        "cutoff": body.as_of_date.isoformat(),
        "archived": archived,
        "new_accounts": [
            {
                "name": a.name,
                "id": str(ids[a.name]),
                "opening_balance": str(a.opening_balance),
                "include_in_summary": a.include_in_summary,
            }
            for a in body.accounts
        ],
        "imported_transactions": len(body.transactions),
        "source_totals": totals(body),
        "starting_summary_balance": str(
            sum((a.opening_balance for a in body.accounts if a.include_in_summary), Decimal("0"))
        ),
        "historical_data_preserved": True,
    }
    service.connection.execute(
        insert(batches).values(
            user_id=service.user_id,
            batch_key=body.batch_key,
            payload_hash=identifier,
            as_of_date=body.as_of_date,
            archived_at=stamp,
            result=data,
        )
    )
    return data


def finance_rebase_router(dependency: Any) -> APIRouter:
    router = APIRouter(prefix="/api/v1")

    @router.post("/finance/rebase/preview")
    def preview(body: FinancialRebase, service: Ledger = Depends(dependency)) -> dict[str, Any]:
        return inspection(service, body)

    @router.post("/finance/rebase/commit")
    def apply(body: FinancialRebase, service: Ledger = Depends(dependency)) -> dict[str, Any]:
        return commit(service, body)

    @router.get("/finance/rebases")
    def history(service: Ledger = Depends(dependency)) -> list[dict[str, Any]]:
        table = service.table("financial_rebases")
        return [
            dict(r)
            for r in service.connection.execute(
                select(table)
                .where(table.c.user_id == service.user_id)
                .order_by(table.c.created_at.desc())
            ).mappings()
        ]

    return router
