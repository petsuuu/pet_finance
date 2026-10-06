"""Owner-scoped monthly category budgets with explicit evidence and no payment mutations."""

from calendar import monthrange
from datetime import date, datetime
from decimal import ROUND_CEILING, Decimal
from statistics import median
from typing import Annotated, Any
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field
from sqlalchemy import select

from app.schemas.inputs import Input, Money
from app.services.ledger import Ledger
from app.services.recurrences import lock
from app.services.schedules import month_date

ZERO = Decimal("0")


class BudgetGenerate(Input):
    year: Annotated[int, Field(ge=2000, le=2100)]
    month: Annotated[int, Field(ge=1, le=12)]
    savings_target: Annotated[Money, Field(ge=0)] = Decimal("500")
    preview: bool = True


class BudgetSet(Input):
    year: Annotated[int, Field(ge=2000, le=2100)]
    month: Annotated[int, Field(ge=1, le=12)]
    category_id: UUID
    limit_amount: Annotated[Money, Field(ge=0)]


def rows(service: Ledger, name: str) -> list[dict[str, Any]]:
    table = service.table(name)
    return [
        dict(r)
        for r in service.connection.execute(
            select(table).where(table.c.user_id == service.user_id)
        ).mappings()
    ]


def extraordinary(row: dict[str, Any]) -> bool:
    note = (row.get("notes") or "").casefold()
    return "férias 2026" in note and "fora" not in note


def net_expense(row: dict[str, Any]) -> Decimal:
    if row["type"] == "EXPENSE":
        return Decimal(row["amount"])
    if row["type"] == "REFUND":
        return -Decimal(row["amount"])
    return ZERO


def category_limits(
    categories: list[dict[str, Any]],
    transactions: list[dict[str, Any]],
    year: int,
    month: int,
) -> list[dict[str, Any]]:
    start = date(year, month, 1)
    earliest = min((r["transaction_date"] for r in transactions), default=start)
    # An observed month starting after day 1 is not demonstrably complete.
    months = [month_date(start, -n, 1) for n in range(1, 4)]
    months = sorted(m for m in months if m >= earliest)
    end = date(year, month, monthrange(year, month)[1])
    result = []
    for category in categories:
        if not category["active"] or category["name"] == "Ajuste de Saldo":
            continue
        relevant = [r for r in transactions if r["category_id"] == category["id"]]
        if category["expense_class"] is None and not any(r["type"] == "EXPENSE" for r in relevant):
            continue
        samples = [
            max(
                ZERO,
                sum(
                    (
                        net_expense(r)
                        for r in relevant
                        if r["status"] == "POSTED"
                        and not extraordinary(r)
                        and r["transaction_date"].year == m.year
                        and r["transaction_date"].month == m.month
                    ),
                    ZERO,
                ),
            )
            for m in months
        ]
        baseline = Decimal(median(samples)) if samples else ZERO
        # Known commitments are protected; one-off consumption is not used to raise a cap.
        committed = sum(
            (
                net_expense(r)
                for r in relevant
                if r["status"] in {"POSTED", "PENDING"}
                and start <= r["transaction_date"] <= end
                and (
                    r.get("recurrence_id")
                    or r.get("installment_plan_id")
                    or "parcela" in r["description"].casefold()
                    or "PAR-" in (r.get("notes") or "")
                )
            ),
            ZERO,
        )
        discretionary = category["expense_class"] == "SUPERFLUOUS"
        # Meals can be necessary for work; never infer they are reducible from the class alone.
        reducible = discretionary and category["name"] != "Refeições fora"
        amount = baseline * (Decimal("0.90") if reducible else Decimal("1"))
        amount = max(amount, committed, ZERO)
        amount = (amount / 5).to_integral_value(rounding=ROUND_CEILING) * 5
        confidence = "LIMITED" if len(samples) < 3 else "HISTORICAL"
        if not baseline and not committed:
            confidence = "NO_EVIDENCE"
        result.append(
            {
                "category_id": category["id"],
                "category": category["name"],
                "limit_amount": amount,
                "method": "AUTO",
                "basis": {
                    "historical_months": [m.isoformat()[:7] for m in months],
                    "median": str(baseline),
                    "known_commitments": str(committed),
                    "discretionary_reduction": "10%" if reducible else "0%",
                    "confidence": confidence,
                    "scope": "direct category only; no child rollup",
                    "excluded_history": "identified Férias 2026; technical adjustments",
                },
            }
        )
    return result


def generate_budgets(service: Ledger, body: BudgetGenerate) -> dict[str, Any]:
    lock(service)
    proposed = category_limits(
        rows(service, "categories"), rows(service, "transactions"), body.year, body.month
    )
    existing = {
        r["category_id"]: r
        for r in rows(service, "budgets")
        if (r["year"], r["month"]) == (body.year, body.month)
    }
    saved = []
    for item in proposed:
        old = existing.get(item["category_id"])
        if old:
            saved.append(
                {
                    **item,
                    "limit_amount": old["limit_amount"],
                    "method": old["method"],
                    "basis": old["basis"],
                    "preserved": True,
                }
            )
        elif body.preview:
            saved.append({**item, "preserved": False})
        else:
            data = {k: item[k] for k in ("category_id", "limit_amount", "method", "basis")}
            saved.append(
                service.create("budgets", {**data, "year": body.year, "month": body.month})
            )
    if not body.preview:
        table = service.table("budget_preferences")
        preference = (
            service.connection.execute(select(table).where(table.c.user_id == service.user_id))
            .mappings()
            .first()
        )
        if preference and (
            preference["savings_target"] != body.savings_target or not preference["enabled"]
        ):
            service.patch(
                "budget_preferences",
                preference["id"],
                {"savings_target": body.savings_target, "enabled": True},
            )
        elif not preference:
            service.create(
                "budget_preferences", {"savings_target": body.savings_target, "enabled": True}
            )
    return {
        "preview": body.preview,
        "year": body.year,
        "month": body.month,
        "savings_target": body.savings_target,
        "items": saved,
        "basis": "Planning caps, not spendable cash. Existing caps never auto-increase. "
        "Savings target is not a transfer or a guaranteed saving.",
    }


def budget_usage(service: Ledger, year: int, month: int) -> dict[str, Any]:
    transactions = rows(service, "transactions")
    categories = {r["id"]: r for r in rows(service, "categories")}
    prefs = rows(service, "budget_preferences")
    items = []
    for budget in rows(service, "budgets"):
        if (budget["year"], budget["month"]) != (year, month):
            continue
        if categories[budget["category_id"]]["name"] == "Ajuste de Saldo":
            continue
        relevant = [
            r
            for r in transactions
            if r["category_id"] == budget["category_id"]
            and (r["transaction_date"].year, r["transaction_date"].month) == (year, month)
        ]
        paid = sum((net_expense(r) for r in relevant if r["status"] == "POSTED"), ZERO)
        pending = sum((net_expense(r) for r in relevant if r["status"] == "PENDING"), ZERO)
        used = max(ZERO, paid + pending)
        cap = budget["limit_amount"]
        state = (
            "EXCEEDED"
            if used > cap
            else "AT_LIMIT"
            if used == cap and used > ZERO
            else ("WARNING" if cap > ZERO and used >= cap * Decimal("0.8") else "OK")
        )
        if budget["basis"].get("confidence") == "NO_EVIDENCE" and budget["method"] == "AUTO":
            state = "NEEDS_REVIEW"
        items.append(
            {
                **budget,
                "category": categories[budget["category_id"]]["name"],
                "category_active": categories[budget["category_id"]]["active"],
                "paid": paid,
                "pending": pending,
                "remaining": max(ZERO, cap - used),
                "excess": max(ZERO, used - cap),
                "status": state,
                "extraordinary_paid": sum(
                    (
                        net_expense(r)
                        for r in relevant
                        if r["status"] == "POSTED" and extraordinary(r)
                    ),
                    ZERO,
                ),
            }
        )
    return {
        "year": year,
        "month": month,
        "savings_target": prefs[0]["savings_target"] if prefs else None,
        "items": sorted(items, key=lambda r: r["category"]),
        "total_limit": sum((r["limit_amount"] for r in items if r["category_active"]), ZERO),
        "unrecorded_planned_spending": sum(
            (
                r["remaining"]
                for r in items
                if r["category_active"] and r["status"] != "NEEDS_REVIEW"
            ),
            ZERO,
        ),
        "scope": "Direct category only; all recorded spending counts in usage. "
        "No-evidence caps require review, not an assumption of zero needs.",
    }


def ensure_current_budgets(service: Ledger) -> None:
    prefs = rows(service, "budget_preferences")
    if not prefs or not prefs[0]["enabled"]:
        return
    today = datetime.now(ZoneInfo("America/Sao_Paulo")).date()
    generate_budgets(
        service,
        BudgetGenerate(
            year=today.year,
            month=today.month,
            savings_target=prefs[0]["savings_target"],
            preview=False,
        ),
    )


def budgets_router(dependency: Any) -> APIRouter:
    router = APIRouter(prefix="/api/v1")

    @router.get("/budgets")
    def list_budgets(
        year: int = Query(ge=2000, le=2100),
        month: int = Query(ge=1, le=12),
        service: Ledger = Depends(dependency),
    ) -> dict[str, Any]:
        return budget_usage(service, year, month)

    @router.post("/budgets/generate")
    def generate(body: BudgetGenerate, service: Ledger = Depends(dependency)) -> dict[str, Any]:
        today = datetime.now(ZoneInfo("America/Sao_Paulo")).date()
        if (body.year, body.month) > (today.year, today.month):
            raise HTTPException(422, "Future months must wait for complete history")
        return generate_budgets(service, body)

    @router.post("/budgets/set")
    def set_budget(body: BudgetSet, service: Ledger = Depends(dependency)) -> dict[str, Any]:
        lock(service)
        category = service.get("categories", body.category_id)
        if not category["active"]:
            raise HTTPException(422, "Inactive category")
        existing = next(
            (
                r
                for r in rows(service, "budgets")
                if (r["category_id"], r["year"], r["month"])
                == (body.category_id, body.year, body.month)
            ),
            None,
        )
        data = {**body.model_dump(), "method": "MANUAL", "basis": {"source": "user"}}
        if existing:
            return service.patch("budgets", existing["id"], data)
        return service.create("budgets", data)

    return router
