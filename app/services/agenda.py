"""Read-only expense agenda: persisted entries and explicitly labelled missing forecasts."""

from calendar import monthrange
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select

from app.services.ledger import Ledger
from app.services.occurrences import occurrence_dates
from app.services.schedules import month_date


def expense_agenda(
    transactions: list[dict[str, Any]],
    rules: list[dict[str, Any]],
    plans: list[dict[str, Any]],
    categories: list[dict[str, Any]],
    start: date,
    end: date,
    as_of: date,
) -> dict[str, Any]:
    """Inputs must already be scoped to the owner. No records are written."""
    names = {r["id"]: r["name"] for r in categories}
    items: list[dict[str, Any]] = []

    def add(row: dict[str, Any], source: str, due: date, recorded: bool) -> None:
        if names.get(row.get("category_id")) == "Ajuste de Saldo":
            return
        status = row.get("status", "PENDING")
        if status == "CANCELLED":
            return
        # Carry old unpaid bills forward, but never count past payments in this month.
        if not start <= due <= end and not (status == "PENDING" and due < start):
            return
        state = "PAID" if status == "POSTED" else "OVERDUE" if due < as_of else "PENDING"
        items.append(
            {
                "transaction_id": row.get("id") if recorded else None,
                "description": row["description"],
                "date": due,
                "status": state,
                "amount": row["amount"] if recorded else row["expected_amount"],
                "source": source,
                "recorded": recorded,
                "recurrence_id": row.get("recurrence_id"),
                "installment_plan_id": row.get("installment_plan_id"),
                "installment_number": row.get("installment_number"),
                "category": names.get(row.get("category_id"), "Sem categoria"),
            }
        )

    represented_rules = {
        (r["recurrence_id"], r["transaction_date"].year, r["transaction_date"].month)
        for r in transactions
        if r.get("recurrence_id")
    }
    represented_plans = {
        (r["installment_plan_id"], r.get("installment_number"))
        for r in transactions
        if r.get("installment_plan_id")
    }
    for row in transactions:
        if row["type"] != "EXPENSE":
            continue
        source = (
            "INSTALLMENT"
            if row.get("installment_plan_id")
            else "RECURRENCE"
            if row.get("recurrence_id")
            else "ONE_OFF"
        )
        add(row, source, row["transaction_date"], True)
    for rule in rules:
        if not rule["active"] or rule["type"] != "EXPENSE":
            continue
        for due in occurrence_dates(rule, start, end):
            if (rule["id"], due.year, due.month) not in represented_rules:
                add({**rule, "recurrence_id": rule["id"]}, "RECURRENCE", due, False)
    for plan in plans:
        if not plan["active"]:
            continue
        for number in range(plan.get("first_tracked_number", 1), plan["total_installments"] + 1):
            due = month_date(plan["first_installment_date"], number - 1)
            if start <= due <= end and (plan["id"], number) not in represented_plans:
                add(
                    {
                        **plan,
                        "expected_amount": plan["installment_amount"],
                        "installment_plan_id": plan["id"],
                        "installment_number": number,
                    },
                    "INSTALLMENT",
                    due,
                    False,
                )
    items.sort(key=lambda r: (r["date"], r["description"], str(r["transaction_id"] or "")))
    totals = {
        state.lower(): {
            "count": sum(r["status"] == state for r in items),
            "amount": sum((r["amount"] for r in items if r["status"] == state), Decimal("0")),
        }
        for state in ("PAID", "PENDING", "OVERDUE")
    }
    missing = [r for r in items if not r["recorded"]]
    return {
        "start_date": start,
        "end_date": end,
        "as_of": as_of,
        "summary": totals,
        "due_today": [r for r in items if r["status"] == "PENDING" and r["date"] == as_of],
        "next_seven_days": [
            r
            for r in items
            if r["status"] == "PENDING" and as_of < r["date"] <= as_of + timedelta(days=7)
        ],
        "missing_forecasts": missing,
        "items": items,
        "basis": "Expense transaction dates; prior recorded arrears included. Missing forecasts "
        "are estimates, never saved. Transfers and card payments are excluded.",
    }


def agenda_router(dependency: Any) -> APIRouter:
    router = APIRouter(prefix="/api/v1")

    @router.get("/dashboard/cashflow")
    def cashflow(
        as_of: date | None = None,
        safety_margin: Decimal = Query(Decimal("0"), ge=0, max_digits=14, decimal_places=2),
        service: Ledger = Depends(dependency),
    ) -> dict[str, Any]:
        from app.services.cashflow import daily_cashflow

        today = as_of or datetime.now(ZoneInfo("America/Sao_Paulo")).date()
        if not 2000 <= today.year <= 2100:
            raise HTTPException(422, "as_of year must be between 2000 and 2100")
        end = date(today.year, today.month, monthrange(today.year, today.month)[1])

        def rows(name: str) -> list[dict[str, Any]]:
            table = service.table(name)
            return [
                dict(r)
                for r in service.connection.execute(
                    select(table).where(table.c.user_id == service.user_id)
                ).mappings()
            ]

        return daily_cashflow(
            rows("accounts"),
            rows("transactions"),
            rows("recurring_transactions"),
            rows("installment_plans"),
            rows("categories"),
            today,
            end,
            safety_margin,
        )

    @router.get("/dashboard/agenda")
    def agenda(
        year: int = Query(ge=2000, le=2100),
        month: int = Query(ge=1, le=12),
        as_of: date | None = None,
        service: Ledger = Depends(dependency),
    ) -> dict[str, Any]:
        start, end = date(year, month, 1), date(year, month, monthrange(year, month)[1])
        today = as_of or datetime.now(ZoneInfo("America/Sao_Paulo")).date()
        if as_of is None:
            today = min(max(today, start), end)
        if not start <= today <= end:
            raise HTTPException(422, "as_of must belong to requested month")

        def rows(name: str) -> list[dict[str, Any]]:
            table = service.table(name)
            return [
                dict(r)
                for r in service.connection.execute(
                    select(table).where(table.c.user_id == service.user_id)
                ).mappings()
            ]

        return expense_agenda(
            rows("transactions"),
            rows("recurring_transactions"),
            rows("installment_plans"),
            rows("categories"),
            start,
            end,
            today,
        )

    return router
