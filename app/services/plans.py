from calendar import monthrange
from datetime import date
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select

from app.schemas.plans import (
    InstallmentCreate,
    InstallmentSetup,
    RecurrenceCancel,
    RecurrenceCreate,
    RecurrenceGenerate,
    RecurrencePatch,
    RecurrenceSetup,
)
from app.services.installments import setup_installment
from app.services.ledger import Ledger
from app.services.occurrences import next_occurrence
from app.services.recurrences import cancel_recurrence, generate_recurrence, lock, setup_recurrence


def next_monthly(start: date, day: int) -> date:
    candidate = date(start.year, start.month, min(day, monthrange(start.year, start.month)[1]))
    if candidate < start:
        year = start.year + (start.month == 12)
        month = start.month % 12 + 1
        candidate = date(year, month, min(day, monthrange(year, month)[1]))
    return candidate


def plans_router(dependency: Any) -> APIRouter:
    router = APIRouter(prefix="/api/v1")

    def listing(service: Ledger, name: str, limit: int, offset: int) -> list[dict[str, Any]]:
        table = service.table(name)
        query = select(table).where(table.c.user_id == service.user_id)
        return [
            dict(row)
            for row in service.connection.execute(
                query.order_by(table.c.created_at, table.c.id).limit(limit).offset(offset)
            ).mappings()
        ]

    @router.get("/recurrences")
    def recurrences(
        service: Ledger = Depends(dependency),
        limit: int = Query(100, ge=1, le=500),
        offset: int = Query(0, ge=0),
    ) -> list[dict[str, Any]]:
        return listing(service, "recurring_transactions", limit, offset)

    @router.post("/recurrences", status_code=201)
    def create_recurrence(
        body: RecurrenceCreate, service: Ledger = Depends(dependency)
    ) -> dict[str, Any]:
        service.reference("accounts", body.account_id)
        service.reference("categories", body.category_id)
        data = body.model_dump()
        occurrence = next_occurrence(data, body.start_date)
        data["next_due_date"] = occurrence
        return service.create("recurring_transactions", data)

    @router.post("/recurrences/setup")
    def setup_rule(body: RecurrenceSetup, service: Ledger = Depends(dependency)) -> dict[str, Any]:
        return setup_recurrence(service, body)

    @router.post("/recurrences/{identity}/generate")
    def generate_rule(
        identity: UUID, body: RecurrenceGenerate, service: Ledger = Depends(dependency)
    ) -> dict[str, Any]:
        return generate_recurrence(service, identity, body.start_date, body.end_date)

    @router.patch("/recurrences/{identity}")
    def patch_recurrence(
        identity: UUID, body: RecurrencePatch, service: Ledger = Depends(dependency)
    ) -> dict[str, Any]:
        lock(service)
        before = service.get("recurring_transactions", identity)
        data = body.model_dump(exclude_unset=True)
        merged = {**before, **data}
        if merged["end_date"] and merged["end_date"] < merged["start_date"]:
            raise HTTPException(422, "end_date precedes start_date")
        # Preserve the existing scheduling anchor; creation does not generate payments.
        anchor = before["next_due_date"] or before["start_date"]
        occurrence = next_occurrence(merged, anchor)
        data["next_due_date"] = occurrence
        return service.patch("recurring_transactions", identity, data)

    @router.post("/recurrences/{identity}/cancel")
    def cancel_rule(
        identity: UUID, body: RecurrenceCancel, service: Ledger = Depends(dependency)
    ) -> dict[str, Any]:
        return cancel_recurrence(service, identity, body)

    @router.get("/installments")
    def installments(
        service: Ledger = Depends(dependency),
        limit: int = Query(100, ge=1, le=500),
        offset: int = Query(0, ge=0),
    ) -> list[dict[str, Any]]:
        return listing(service, "installment_plans", limit, offset)

    @router.post("/installments", status_code=201)
    def create_installment(
        body: InstallmentCreate, service: Ledger = Depends(dependency)
    ) -> dict[str, Any]:
        service.reference("accounts", body.account_id)
        service.reference("categories", body.category_id)
        data = body.model_dump()
        total = body.installment_amount * body.total_installments
        if total >= 10**12:
            raise HTTPException(422, "Total exceeds monetary limit")
        data["total_amount"] = total if body.first_tracked_number == 1 else None
        return service.create("installment_plans", data)

    @router.post("/installments/setup")
    def setup_plan(body: InstallmentSetup, service: Ledger = Depends(dependency)) -> dict[str, Any]:
        return setup_installment(service, body)

    return router
