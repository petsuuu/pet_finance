from calendar import monthrange
from datetime import date, datetime
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from app.schemas.inputs import Input, Name
from app.services.ledger import Ledger
from app.services.occurrences import occurrence_dates


def month_date(anchor: date, offset: int, day: int | None = None) -> date:
    index = anchor.year * 12 + anchor.month - 1 + offset
    year, month = divmod(index, 12)
    return date(year, month + 1, min(day or anchor.day, monthrange(year, month + 1)[1]))


class TagCreate(Input):
    name: Name


def schedules_router(dependency: Any) -> APIRouter:
    router = APIRouter(prefix="/api/v1")

    @router.post("/tags", status_code=201)
    def create_tag(body: TagCreate, service: Ledger = Depends(dependency)) -> dict[str, Any]:
        return service.create("tags", body.model_dump())

    @router.get("/tags")
    def tags(service: Ledger = Depends(dependency)) -> list[dict[str, Any]]:
        table = service.table("tags")
        return [
            dict(r)
            for r in service.connection.execute(
                select(table).where(table.c.user_id == service.user_id).order_by(table.c.name)
            ).mappings()
        ]

    @router.post("/installments/{identity}/generate")
    def generate(identity: UUID, service: Ledger = Depends(dependency)) -> dict[str, Any]:
        plan = service.get("installment_plans", identity)
        if not plan["active"]:
            raise HTTPException(422, "Inactive plan")
        service.reference("accounts", plan["account_id"])
        service.reference("categories", plan["category_id"])
        table = service.table("transactions")
        existing = {
            r["installment_number"]
            for r in service.connection.execute(
                select(table).where(
                    table.c.user_id == service.user_id, table.c.installment_plan_id == identity
                )
            ).mappings()
        }
        created = []
        for number in range(1, plan["total_installments"] + 1):
            if number in existing:
                continue
            created.append(
                service.create(
                    "transactions",
                    {
                        "description": (
                            f"{plan['description']} — {number}/{plan['total_installments']}"
                        ),
                        "type": "EXPENSE",
                        "status": "PENDING",
                        "amount": plan["installment_amount"],
                        "transaction_date": month_date(plan["first_installment_date"], number - 1),
                        "account_id": plan["account_id"],
                        "category_id": plan["category_id"],
                        "installment_plan_id": identity,
                        "installment_number": number,
                        "source": "SYSTEM",
                    },
                )
            )
        return {"created_count": len(created), "transactions": created}

    @router.get("/audit/installments")
    def installment_audit(
        identity: UUID, service: Ledger = Depends(dependency)
    ) -> list[dict[str, Any]]:
        plan = service.get("installment_plans", identity)
        table = service.table("transactions")
        rows = {
            r["installment_number"]: r
            for r in service.connection.execute(
                select(table).where(
                    table.c.user_id == service.user_id, table.c.installment_plan_id == identity
                )
            ).mappings()
        }
        result = []
        for n in range(1, plan["total_installments"] + 1):
            due = month_date(plan["first_installment_date"], n - 1)
            row = rows.get(n)
            status = "MISSING" if row is None else row["status"]
            if row and (
                row["amount"] != plan["installment_amount"] or row["transaction_date"] != due
            ):
                status = "INCONSISTENT"
            result.append({"number": n, "due_date": due, "status": status})
        return result

    @router.get("/audit/recurrences")
    def recurrence_audit(
        start_date: date,
        end_date: date,
        as_of: date | None = None,
        service: Ledger = Depends(dependency),
    ) -> list[dict[str, Any]]:
        if end_date < start_date or (end_date - start_date).days > 366:
            raise HTTPException(422, "Range must be ordered and at most 366 days")
        today = as_of or datetime.now(ZoneInfo("America/Sao_Paulo")).date()
        rules = service.table("recurring_transactions")
        table = service.table("transactions")
        result = []
        for rule in service.connection.execute(
            select(rules).where(rules.c.user_id == service.user_id, rules.c.active.is_(True))
        ).mappings():
            for due in occurrence_dates(dict(rule), start_date, end_date):
                rows = list(
                    service.connection.execute(
                        select(table).where(
                            table.c.user_id == service.user_id,
                            table.c.recurrence_id == rule["id"],
                            table.c.transaction_date >= due.replace(day=1),
                            table.c.transaction_date < month_date(due, 1, 1),
                            table.c.status != "CANCELLED",
                        )
                    ).mappings()
                )
                row = rows[0] if rows else None
                status = "OVERDUE" if due < today else "PENDING"
                if row and row["status"] == "POSTED":
                    status = (
                        "MATCHED"
                        if abs(row["amount"] - rule["expected_amount"]) <= rule["tolerance_amount"]
                        else "AMOUNT_MISMATCH"
                    )
                result.append({"recurrence_id": rule["id"], "due_date": due, "status": status})
        return result

    return router
