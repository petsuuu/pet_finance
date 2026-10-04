from calendar import monthrange
from datetime import date
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select

from app.schemas.plans import InstallmentCreate, RecurrenceCreate, RecurrencePatch
from app.services.ledger import Ledger


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
        occurrence = next_monthly(body.start_date, body.due_day)
        data["next_due_date"] = (
            occurrence if not body.end_date or occurrence <= body.end_date else None
        )
        return service.create("recurring_transactions", data)

    @router.patch("/recurrences/{identity}")
    def patch_recurrence(
        identity: UUID, body: RecurrencePatch, service: Ledger = Depends(dependency)
    ) -> dict[str, Any]:
        before = service.get("recurring_transactions", identity)
        data = body.model_dump(exclude_unset=True)
        merged = {**before, **data}
        if merged["end_date"] and merged["end_date"] < merged["start_date"]:
            raise HTTPException(422, "end_date precedes start_date")
        # Preserve the existing scheduling anchor; creation does not generate payments.
        anchor = before["next_due_date"] or before["start_date"]
        occurrence = next_monthly(anchor, merged["due_day"])
        data["next_due_date"] = (
            occurrence
            if merged["active"] and (not merged["end_date"] or occurrence <= merged["end_date"])
            else None
        )
        return service.patch("recurring_transactions", identity, data)

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
        data["total_amount"] = body.installment_amount * body.total_installments
        if data["total_amount"] >= 10**12:
            raise HTTPException(422, "Total exceeds monetary limit")
        return service.create("installment_plans", data)

    return router
