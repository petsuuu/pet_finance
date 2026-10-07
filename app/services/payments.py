"""Atomic, owner-scoped settlement of existing obligations and readable history."""

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field
from sqlalchemy import Date, cast, func, select

from app.schemas.inputs import Input, Money
from app.services.ledger import Ledger
from app.services.recurrences import lock


class PaymentInput(Input):
    amount: Annotated[Money, Field(gt=0)]
    payment_date: date
    idempotency_key: Annotated[str, Field(min_length=1, max_length=200)]


def settle(service: Ledger, identity: UUID, body: PaymentInput) -> dict[str, Any]:
    lock(service)
    operations = service.table("payment_operations")
    digest = hashlib.sha256((str(identity) + body.model_dump_json()).encode()).hexdigest()
    prior = (
        service.connection.execute(
            select(operations).where(
                operations.c.user_id == service.user_id, operations.c.key == body.idempotency_key
            )
        )
        .mappings()
        .first()
    )
    if prior:
        if prior["payload_hash"] != digest:
            raise HTTPException(409, "Payment key already used for another operation")
        return dict(prior["result"])
    original = service.get("transactions", identity)
    if original["status"] != "PENDING" or original["type"] not in {"EXPENSE", "INCOME"}:
        raise HTTPException(422, "Only pending expenses or income can be settled")
    if body.amount > original["amount"]:
        raise HTTPException(422, "Payment exceeds remaining obligation")
    if body.payment_date > datetime.now(ZoneInfo("America/Sao_Paulo")).date():
        raise HTTPException(422, "Cannot confirm a future payment")
    service.reference("accounts", original["account_id"])
    remaining = original["amount"] - body.amount
    context = (
        f"Settlement of obligation {identity}; previous outstanding {original['amount']}; "
        f"paid {body.amount} on {body.payment_date}; remaining {remaining}."
    )
    note = "\n".join(filter(None, [original.get("notes"), context]))
    if remaining:
        # The canonical pending row retains recurrence/installment identity and due date.
        # Its paid fragment is linked through the operation, not another occurrence.
        payment = service.create(
            "transactions",
            {
                "type": original["type"],
                "status": "POSTED",
                "description": original["description"],
                "amount": body.amount,
                "transaction_date": body.payment_date,
                "account_id": original["account_id"],
                "category_id": original["category_id"],
                "merchant_id": original.get("merchant_id"),
                "source": "CHATGPT",
                "notes": note,
            },
        )
        # Avoid falsely assigning one installment's number to two transaction rows.
        pending = service.patch("transactions", identity, {"amount": remaining, "notes": note})
        tags = service.table("transaction_tags")
        tag_ids: list[UUID] = list(
            service.connection.execute(
                select(tags.c.tag_id).where(tags.c.transaction_id == identity)
            ).scalars()
        )
        for tag in tag_ids:
            service.connection.execute(
                tags.insert().values(transaction_id=payment["id"], tag_id=tag)
            )
    else:
        payment = service.patch(
            "transactions",
            identity,
            {
                "status": "POSTED",
                "transaction_date": body.payment_date,
                "notes": note,
            },
        )
        pending = None
    result = json.loads(
        json.dumps(
            {
                "obligation_id": identity,
                "original_amount": original["amount"],
                "original_due_date": original["transaction_date"],
                "payment": payment,
                "pending": pending,
                "paid_now": body.amount,
                "remaining": remaining,
                "status": "PARTIALLY_PAID" if remaining else "SETTLED",
            },
            default=str,
        )
    )
    service.create(
        "payment_operations",
        {
            "key": body.idempotency_key,
            "payload_hash": digest,
            "obligation_id": identity,
            "payment_id": payment["id"],
            "result": result,
        },
    )
    return dict(result)


def payment_router(dependency: Any) -> APIRouter:
    router = APIRouter(prefix="/api/v1")

    @router.post("/transactions/{identity}/settle")
    def payment(
        identity: UUID, body: PaymentInput, service: Ledger = Depends(dependency)
    ) -> dict[str, Any]:
        return settle(service, identity, body)

    @router.get("/transactions/history")
    def history(
        identity: UUID | None = None,
        since: date | None = None,
        until: date | None = None,
        limit: int = Query(100, ge=1, le=500),
        offset: int = Query(0, ge=0),
        service: Ledger = Depends(dependency),
    ) -> dict[str, Any]:
        if since and until and since > until:
            raise HTTPException(422, "Invalid history interval")
        table = service.table("audit_log")
        query = select(table).where(
            table.c.user_id == service.user_id, table.c.entity_type == "transactions"
        )
        if identity:
            service.get("transactions", identity)
            query = query.where(table.c.entity_id == identity)
        # Dates refer to the change date in Sao Paulo, not the payment/due date.
        changed_day = cast(func.timezone("America/Sao_Paulo", table.c.created_at), Date)
        if since:
            query = query.where(changed_day >= since)
        if until:
            query = query.where(changed_day <= until)
        query = (
            query.order_by(table.c.created_at.desc(), table.c.id.desc()).limit(limit).offset(offset)
        )
        items = [dict(r) for r in service.connection.execute(query).mappings()]
        return {
            "items": items,
            "limit": limit,
            "offset": offset,
            "scope": "Recorded changes only; legacy imports may lack prior history.",
        }

    return router


def payment_groups(
    transactions: list[dict[str, Any]], operations: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Explicit groups for display; canonical installments for the ending radar only."""
    by_id = {str(r["id"]): r for r in transactions}
    groups: dict[str, list[dict[str, Any]]] = {}
    for operation in sorted(operations, key=lambda r: (r["created_at"], str(r["id"]))):
        groups.setdefault(str(operation["obligation_id"]), []).append(operation)
    summaries = []
    fragments: set[str] = set()
    original_amounts: dict[str, Decimal] = {}
    for identity, group in groups.items():
        canonical = by_id.get(identity)
        if canonical is None:
            continue
        total = Decimal(group[0]["result"]["original_amount"])
        payment_ids = {str(r["payment_id"]) for r in group}
        paid = sum(
            (
                Decimal(by_id[p]["amount"])
                for p in payment_ids
                if p in by_id and by_id[p]["status"] == "POSTED"
            ),
            Decimal("0"),
        )
        remaining = canonical["amount"] if canonical["status"] == "PENDING" else Decimal("0")
        state = "PARTIALLY_PAID" if remaining else "SETTLED"
        if paid + remaining != total or canonical["status"] == "CANCELLED":
            state = "NEEDS_REVIEW"
        summaries.append(
            {
                "obligation_id": identity,
                "description": canonical["description"],
                "total": total,
                "paid": paid,
                "remaining": remaining,
                "original_due_date": group[0]["result"]["original_due_date"],
                "status": state,
                "payment_ids": sorted(payment_ids),
            }
        )
        fragments.update(payment_ids - {identity})
        if state != "NEEDS_REVIEW":
            original_amounts[identity] = total
    radar_rows = [
        {**r, "amount": original_amounts.get(str(r["id"]), r["amount"])}
        for r in transactions
        if str(r["id"]) not in fragments
    ]
    return summaries, radar_rows
