"""Adopt existing monthly payments and generate missing occurrences atomically."""

from datetime import date
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select, text

from app.schemas.inputs import TransactionCreate
from app.schemas.plans import RecurrenceSetup
from app.services.ledger import Ledger
from app.services.schedules import month_date


def lock(service: Ledger) -> None:
    service.connection.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:owner, 0))"),
        {"owner": str(service.user_id)},
    )


def setup_recurrence(service: Ledger, body: RecurrenceSetup) -> dict[str, Any]:
    lock(service)
    for name, identity in [("accounts", body.account_id), ("categories", body.category_id)]:
        service.reference(name, identity)
        if identity and not service.get(name, identity)["active"]:
            raise HTTPException(422, "Inactive reference")
    rows = [service.get("transactions", identity) for identity in body.transaction_ids]
    months = set()
    linked = set()
    for row in rows:
        day = row["transaction_date"]
        month = (day.year, day.month)
        due = month_date(day, 0, body.due_day)
        if month in months:
            raise HTTPException(422, "Only one occurrence per month is allowed")
        months.add(month)
        if any(row[key] != getattr(body, key) for key in ["type", "account_id", "category_id"]):
            raise HTTPException(
                422, "Existing occurrence has a different account, type or category"
            )
        if due < body.start_date or (body.end_date and due > body.end_date):
            raise HTTPException(422, "Existing occurrence is outside the rule dates")
        if row["recurrence_id"]:
            linked.add(row["recurrence_id"])
    if len(linked) > 1:
        raise HTTPException(409, "Occurrences already belong to different rules")
    data = body.model_dump(exclude={"transaction_ids"})
    if linked:
        rule = service.get("recurring_transactions", next(iter(linked)))
        if not rule["active"] or any(rule[key] != value for key, value in data.items()):
            raise HTTPException(409, "Existing rule differs from the requested configuration")
    else:
        table = service.table("recurring_transactions")
        existing = (
            service.connection.execute(
                select(table).where(
                    table.c.user_id == service.user_id,
                    table.c.description == body.description,
                    table.c.account_id == body.account_id,
                    table.c.type == body.type,
                )
            )
            .mappings()
            .first()
        )
        if existing:
            raise HTTPException(409, "A rule with this description already exists")
        occurrence = month_date(body.start_date, 0, body.due_day)
        if occurrence < body.start_date:
            occurrence = month_date(body.start_date, 1, body.due_day)
        data["next_due_date"] = (
            occurrence if not body.end_date or occurrence <= body.end_date else None
        )
        rule = service.create("recurring_transactions", data)
    for row in rows:
        if not row["recurrence_id"]:
            service.patch("transactions", row["id"], {"recurrence_id": rule["id"]})
    return {"recurrence": rule, "linked_count": len(rows)}


def generate_recurrence(
    service: Ledger, identity: UUID, start_date: date, end_date: date
) -> dict[str, Any]:
    if end_date < start_date or (end_date - start_date).days > 366:
        raise HTTPException(422, "Range must be ordered and at most 366 days")
    lock(service)
    rule = service.get("recurring_transactions", identity)
    if not rule["active"]:
        raise HTTPException(422, "Inactive recurrence")
    table = service.table("transactions")
    rows = service.connection.execute(
        select(table).where(table.c.user_id == service.user_id, table.c.recurrence_id == identity)
    ).mappings()
    represented = {(row["transaction_date"].year, row["transaction_date"].month) for row in rows}
    created = []
    skipped = 0
    anchor = max(start_date, rule["start_date"])
    for offset in range(14):
        due = month_date(anchor, offset, rule["due_day"])
        if due < anchor:
            continue
        if due > end_date or (rule["end_date"] and due > rule["end_date"]):
            break
        if (due.year, due.month) in represented:
            skipped += 1
            continue
        created.append(
            service.transaction(
                TransactionCreate(
                    description=rule["description"],
                    type=rule["type"],
                    status="PENDING",
                    amount=rule["expected_amount"],
                    transaction_date=due,
                    account_id=rule["account_id"],
                    category_id=rule["category_id"],
                    recurrence_id=identity,
                    source="SYSTEM",
                    notes=rule["notes"],
                    idempotency_key=f"recurrence:{identity}:{due:%Y-%m}",
                )
            )
        )
    return {"created_count": len(created), "skipped_count": skipped, "transactions": created}
