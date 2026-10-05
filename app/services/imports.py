import hashlib
import json
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import insert, select, text

from app.schemas.inputs import Input
from app.services.ledger import Ledger


class Snapshot(Input):
    accounts: list[dict[str, Any]]
    categories: list[dict[str, Any]]
    transactions: list[dict[str, Any]]
    exported_at: str | None = None


def normalize(snapshot: Snapshot) -> list[dict[str, Any]]:
    accounts = {r["id"] for r in snapshot.accounts}
    categories = {r["id"] for r in snapshot.categories}
    result = []
    seen = set()
    for row in snapshot.transactions:
        identity = str(UUID(row["id"]))
        if identity in seen:
            raise ValueError("Duplicate source ID")
        seen.add(identity)
        amount = Decimal(str(row["amount"]))
        if (
            not amount.is_finite()
            or amount <= 0
            or amount >= 10**12
            or amount != amount.quantize(Decimal(".01"))
        ):
            raise ValueError("Invalid amount; signed adjustments require explicit mapping")
        if row["intent"] not in {"EXPENSE", "INCOME", "ADJUSTMENT", "REFUND", "YIELD"}:
            raise ValueError("Unsupported transaction type")
        if row["status"] not in {"POSTED", "PENDING", "CANCELLED"} or row["currencyCode"] != "BRL":
            raise ValueError("Unsupported status or currency")
        account = row.get("account", {}).get("id")
        category = row.get("category", {}).get("id")
        if category and category not in categories:
            candidates = [
                c["id"]
                for c in snapshot.categories
                if c["name"] == row.get("category", {}).get("name")
            ]
            if len(candidates) == 1:
                category = candidates[0]
        if account not in accounts or (category and category not in categories):
            raise ValueError("Missing source reference")
        result.append(
            {
                "external_id": identity,
                "amount": amount,
                "type": row["intent"],
                "status": row["status"],
                "transaction_date": date.fromisoformat(row["date"]),
                "description": row.get("description") or "Sem descrição",
                "account_id": account,
                "category_id": category,
                "notes": row.get("notes"),
                "currency_code": "BRL",
            }
        )
    return result


def totals(rows: list[dict[str, Any]]) -> dict[str, str]:
    values: dict[str, Decimal] = {}
    for row in rows:
        key = f"{row['type']}:{row['status']}"
        values[key] = values.get(key, Decimal("0")) + Decimal(str(row["amount"]))
    return {key: str(value.quantize(Decimal(".01"))) for key, value in sorted(values.items())}


def imports_router(dependency: Any) -> APIRouter:
    router = APIRouter(prefix="/api/v1")

    def validate(body: Snapshot) -> list[dict[str, Any]]:
        try:
            return normalize(body)
        except (ValueError, KeyError, TypeError, InvalidOperation) as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.post("/imports/clofin/preview")
    def preview(body: Snapshot, service: Ledger = Depends(dependency)) -> dict[str, Any]:
        rows = validate(body)
        return {
            "valid": True,
            "count": len(rows),
            "totals": totals(rows),
            "accounts": len(body.accounts),
            "categories": len(body.categories),
        }

    @router.post("/imports/clofin/commit")
    def commit(body: Snapshot, service: Ledger = Depends(dependency)) -> dict[str, Any]:
        rows = validate(body)
        service.connection.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:u, 0))"),
            {"u": str(service.user_id)},
        )
        mappings: dict[str, UUID] = {}
        for account in body.accounts:
            table = service.table("accounts")
            existing = (
                service.connection.execute(
                    select(table).where(
                        table.c.user_id == service.user_id, table.c.name == account["name"]
                    )
                )
                .mappings()
                .first()
            )
            opening = Decimal(str(account.get("balances", {}).get("opening", 0)))
            if account["currencyCode"] != "BRL" or not opening.is_finite():
                raise HTTPException(422, "Unsupported account currency or balance")
            if existing:
                if existing["opening_balance"] != opening:
                    raise HTTPException(409, "Opening balance conflict")
                mappings[account["id"]] = existing["id"]
            else:
                created = service.create(
                    "accounts",
                    {
                        "name": account["name"],
                        "type": "CASH",
                        "opening_balance": opening,
                        "currency_code": "BRL",
                    },
                )
                mappings[account["id"]] = created["id"]
        pending = list(body.categories)
        while pending:
            progress = False
            for category in pending[:]:
                parent = category.get("parentId")
                if parent and parent not in mappings:
                    continue
                table = service.table("categories")
                parent_id = mappings.get(parent) if parent else None
                existing = (
                    service.connection.execute(
                        select(table).where(
                            table.c.user_id == service.user_id,
                            table.c.name == category["name"],
                            table.c.parent_id == parent_id,
                        )
                    )
                    .mappings()
                    .first()
                )
                if existing:
                    mappings[category["id"]] = existing["id"]
                else:
                    classification = category["subtype"].removesuffix("_EXPENSE")
                    created = service.create(
                        "categories",
                        {
                            "name": category["name"],
                            "parent_id": parent_id,
                            "active": not category.get("archived", False),
                            "expense_class": classification
                            if classification in {"ESSENTIAL", "FUNDAMENTAL", "SUPERFLUOUS"}
                            else None,
                        },
                    )
                    mappings[category["id"]] = created["id"]
                pending.remove(category)
                progress = True
            if not progress:
                raise HTTPException(422, "Missing category parent or cycle")
        batch = service.create(
            "import_batches",
            {
                "source": "CLOFIN",
                "checksum": hashlib.sha256(body.model_dump_json().encode()).hexdigest(),
            },
        )
        imported = skipped = 0
        table = service.table("transactions")
        for row in rows:
            data = {
                **row,
                "account_id": mappings[row["account_id"]],
                "category_id": mappings.get(row["category_id"]),
                "source": "CLOFIN",
            }
            prior = (
                service.connection.execute(
                    select(table).where(
                        table.c.user_id == service.user_id,
                        table.c.source == "CLOFIN",
                        table.c.external_id == row["external_id"],
                    )
                )
                .mappings()
                .first()
            )
            if prior:
                if any(prior[k] != v for k, v in data.items()):
                    raise HTTPException(409, "Source transaction changed; reconcile before update")
                identity = prior["id"]
                skipped += 1
            else:
                identity = service.create("transactions", data)["id"]
                imported += 1
            service.connection.execute(
                insert(service.table("import_row_map")).values(
                    batch_id=batch["id"],
                    external_id=row["external_id"],
                    transaction_id=identity,
                    raw_payload=json.loads(json.dumps(data, default=str)),
                )
            )
        service.connection.execute(
            text("UPDATE import_batches SET imported_rows=:n WHERE id=:id"),
            {"n": imported, "id": batch["id"]},
        )
        return {
            "batch_id": batch["id"],
            "imported": imported,
            "skipped": skipped,
            "source_count": len(rows),
            "source_totals": totals(rows),
        }

    @router.get("/imports/{identity}/reconciliation")
    def reconcile(identity: UUID, service: Ledger = Depends(dependency)) -> dict[str, Any]:
        service.get("import_batches", identity)
        links = service.table("import_row_map")
        table = service.table("transactions")
        pairs = list(
            service.connection.execute(
                select(links.c.raw_payload, table)
                .join(table, links.c.transaction_id == table.c.id)
                .where(links.c.batch_id == identity, table.c.user_id == service.user_id)
            ).mappings()
        )
        source = [r["raw_payload"] for r in pairs]
        destination = [dict(r) for r in pairs]
        differences = sum(
            any(
                (
                    Decimal(str(r[k])) != Decimal(str(r["raw_payload"][k]))
                    if k == "amount"
                    else str(r[k]) != str(r["raw_payload"][k])
                )
                for k in [
                    "amount",
                    "type",
                    "status",
                    "transaction_date",
                    "account_id",
                    "category_id",
                ]
            )
            for r in pairs
        )
        return {
            "count": len(pairs),
            "source_totals": totals(source),
            "destination_totals": totals(destination),
            "different_rows": differences,
            "reconciled": differences == 0,
        }

    return router
