"""Adopt existing monthly or yearly payments and generate missing occurrences atomically."""

from datetime import date, timedelta
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select, text

from app.schemas.inputs import TransactionCreate
from app.schemas.plans import RecurrenceCancel, RecurrenceSetup
from app.services.ledger import Ledger
from app.services.occurrences import next_occurrence, occurrence_dates
from app.services.schedules import month_date


def lock(service: Ledger) -> None:
    service.connection.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:owner, 0))"),
        {"owner": str(service.user_id)},
    )


def cancel_recurrence(service: Ledger, identity: UUID, body: RecurrenceCancel) -> dict[str, Any]:
    """Preview by default; keep paid occurrences and overdue obligations before the cutoff."""
    if body.effective_date == date.min:
        raise HTTPException(422, "effective_date must be after the earliest supported date")
    lock(service)
    rule = service.get("recurring_transactions", identity)
    table = service.table("transactions")
    rows = [
        dict(row)
        for row in service.connection.execute(
            select(table)
            .where(
                table.c.user_id == service.user_id,
                table.c.recurrence_id == identity,
                table.c.status == "PENDING",
                table.c.transaction_date >= body.effective_date,
            )
            .order_by(table.c.transaction_date, table.c.id)
            .with_for_update()
        ).mappings()
    ]
    cutoff = body.effective_date - timedelta(days=1)
    changes: dict[str, Any] = {}
    if body.effective_date <= rule["start_date"]:
        changes["active"] = False
    elif rule["end_date"] is None or cutoff < rule["end_date"]:
        changes["end_date"] = cutoff
    if changes:
        merged = {**rule, **changes}
        changes["next_due_date"] = (
            next_occurrence(merged, rule["next_due_date"] or rule["start_date"])
            if merged["active"]
            else None
        )
    if not body.preview:
        if changes:
            rule = service.patch("recurring_transactions", identity, changes)
        rows = [service.patch("transactions", row["id"], {"status": "CANCELLED"}) for row in rows]
    return {
        "preview": body.preview,
        "effective_date": body.effective_date,
        "recurrence": rule,
        "rule_changes": changes,
        "affected_count": len(rows),
        "cancelled_count": 0 if body.preview else len(rows),
        "transactions": [dict(row) for row in rows],
    }


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
        if row["installment_plan_id"] is not None:
            raise HTTPException(422, "Installments cannot be linked to a recurrence")
        if body.frequency == "YEARLY" and day.month != body.month_of_year:
            raise HTTPException(422, "Existing occurrence is outside the annual renewal month")
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
        occurrence = next_occurrence(data, body.start_date)
        data["next_due_date"] = occurrence
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
    for due in occurrence_dates(rule, start_date, end_date):
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
