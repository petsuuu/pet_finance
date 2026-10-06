"""Read-only immediate-purchase analysis; expected income is never cash today."""

from calendar import monthrange
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query

from app.schemas.inputs import Money
from app.services.budgets import rows
from app.services.cashflow import daily_cashflow
from app.services.ledger import Ledger

ZERO = Decimal("0")


def assess_purchase(
    flow: dict[str, Any],
    dashboard: dict[str, Any],
    amount: Decimal,
    category_id: UUID | None = None,
) -> dict[str, Any]:
    budgets = dashboard["category_budgets"]
    category = next((r for r in budgets["items"] if r["category_id"] == category_id), None)
    known = category is not None and category["status"] != "NEEDS_REVIEW"
    allowance = category["remaining"] if known and category is not None else ZERO
    planned = dashboard["budget_planning"]["forecast_after_category_budgets_and_goal"]
    # A purchase consumes its already-reserved category allowance. Do not deduct it twice.
    after_plan = planned - max(ZERO, amount - allowance)
    today = flow["days"][0]
    immediate = flow["recorded_balance_as_of"] - today["outflow"] - flow["safety_margin"]
    dated = min(r["after_safety_margin"] for r in flow["days"])
    cash_capacity = max(ZERO, min(immediate, dated))
    planning_capacity = max(ZERO, planned + allowance)
    capacity = min(cash_capacity, planning_capacity)
    if known:
        capacity = min(capacity, allowance)
    reasons = []
    if flow["recorded_balance_as_of"] < ZERO:
        reasons.append("Saldo registrado negativo; a compra aumentaria o déficit.")
    if amount > cash_capacity:
        reasons.append("A compra não cabe no caixa protegido até o fim do mês.")
    if known and amount > allowance:
        reasons.append("A compra ultrapassa o restante da categoria.")
    if after_plan < ZERO:
        reasons.append("O cenário não cobre o orçamento restante, a meta e a margem informada.")
    if not known:
        reasons.append(
            "Categoria sem limite validado; a adequação do orçamento precisa de revisão."
        )
    restricted = amount > cash_capacity or after_plan < ZERO or (known and amount > allowance)
    decision = "NAO_RECOMENDADO" if restricted else "REVISAR" if not known else "CABE_NO_CENARIO"
    affected = next((r for r in flow["days"] if r["after_safety_margin"] - amount < ZERO), None)
    obligations = [
        {
            "date": r["date"],
            "description": e["description"],
            "amount": -e["change"],
            "estimated": e["estimated"],
            "overdue": e["overdue"],
        }
        for r in flow["days"]
        for e in r["events"]
        if e["change"] < ZERO
    ]
    return {
        "as_of": flow["as_of"],
        "end_date": flow["end_date"],
        "amount": amount,
        "decision": decision,
        "reasons": reasons,
        "recorded_balance": flow["recorded_balance_as_of"],
        "recorded_balance_after_purchase": flow["recorded_balance_as_of"] - amount,
        "maximum_within_scenario": capacity,
        "category": category["category"] if category else None,
        "category_remaining": allowance if known else None,
        "category_remaining_after_purchase": max(ZERO, allowance - amount) if known else None,
        "savings_target": budgets["savings_target"],
        "safety_margin": flow["safety_margin"],
        "forecast_after_plans_and_purchase": after_plan,
        "lowest_balance_after_purchase": flow["lowest_balance"] - amount,
        "first_shortfall_date": affected["date"] if affected else None,
        "next_obligations": obligations[:5],
        "obligations_count": len(obligations),
        "basis": "Simulação de pagamento imediato, consolidada até o fim do mês. "
        "Receitas previstas não aumentam o caixa disponível hoje. Obrigações vencidas "
        "são consideradas hoje; ordem intradiária desconhecida. Margem padrão zero não "
        "significa reserva existente. Categorias desconhecidas requerem revisão. "
        "Não concilia extrato, não inclui gastos ausentes ou juros desconhecidos e não "
        "registra a compra. Capacidade condicionada às receitas e dados registrados.",
    }


def spending_router(dependency: Any) -> APIRouter:
    router = APIRouter(prefix="/api/v1")

    @router.get("/dashboard/purchase-check")
    def check(
        amount: Annotated[Money, Query(gt=0)],
        category_id: UUID | None = None,
        as_of: date | None = None,
        safety_margin: Annotated[Money, Query(ge=0)] = ZERO,
        service: Ledger = Depends(dependency),
    ) -> dict[str, Any]:
        from app.services.dashboard import monthly_dashboard

        today = datetime.now(ZoneInfo("America/Sao_Paulo")).date()
        day = as_of or today
        if day != today:
            raise HTTPException(422, "Purchase check requires the current local date")
        if category_id is not None:
            category = next(
                (r for r in rows(service, "categories") if r["id"] == category_id), None
            )
            if category is None:
                raise HTTPException(404, "Category not found")
            if not category["active"]:
                raise HTTPException(422, "Inactive category")
        end = date(day.year, day.month, monthrange(day.year, day.month)[1])
        flow = daily_cashflow(
            rows(service, "accounts"),
            rows(service, "transactions"),
            rows(service, "recurring_transactions"),
            rows(service, "installment_plans"),
            rows(service, "categories"),
            day,
            end,
            safety_margin,
        )
        dashboard = monthly_dashboard(service, day.year, day.month, day, safety_margin)
        return assess_purchase(flow, dashboard, amount, category_id)

    return router
