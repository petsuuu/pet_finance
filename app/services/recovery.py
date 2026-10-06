"""Read-only recovery plan with explicit variable-spending assumptions."""

from decimal import ROUND_DOWN, Decimal
from typing import Any

ZERO = Decimal("0")
CENT = Decimal("0.01")


def stable_date(days: list[dict[str, Any]], threshold: Decimal) -> Any:
    lowest = None
    candidate = None
    for row in reversed(days):
        lowest = row["balance"] if lowest is None else min(lowest, row["balance"])
        if lowest >= threshold:
            candidate = row["date"]
    return candidate


def recovery_plan(
    flow: dict[str, Any],
    budgets: dict[str, Any],
    categories: list[dict[str, Any]],
    estimated_bank_charges: Decimal | None = None,
) -> dict[str, Any]:
    charges = estimated_bank_charges or ZERO
    margin = flow["safety_margin"]
    target = budgets["savings_target"]
    desired = target or ZERO
    remaining = budgets["unrecorded_planned_spending"]
    closing = flow["forecast_closing_balance"] - charges
    after_plan = closing - remaining - margin
    needed = max(ZERO, desired - after_plan)
    classes = {r["id"]: r.get("expense_class") for r in categories}
    adjustable = [
        r
        for r in budgets["items"]
        if r["category_active"]
        and r["status"] != "NEEDS_REVIEW"
        and classes.get(r["category_id"]) == "SUPERFLUOUS"
        and r["category"] != "Refeições fora"
        and r["remaining"] > ZERO
    ]
    pool = sum((r["remaining"] for r in adjustable), ZERO)
    cut_total = min(needed, pool)
    proposals = []
    assigned = ZERO
    for i, row in enumerate(sorted(adjustable, key=lambda r: r["category"])):
        cut = (
            cut_total - assigned
            if i == len(adjustable) - 1
            else (cut_total * row["remaining"] / pool).quantize(CENT, rounding=ROUND_DOWN)
        )
        cut = min(cut, row["remaining"])
        assigned += cut
        if cut > ZERO:
            proposals.append(
                {
                    "category": row["category"],
                    "suggested_reduction": cut,
                    "remaining_after_reduction": row["remaining"] - cut,
                }
            )

    def scenario(planned_spending: Decimal) -> list[dict[str, Any]]:
        # Unknown variable-spending dates: an even daily allowance is an explicit scenario,
        # not an inferred due date. The last day absorbs cent rounding exactly.
        count = len(flow["days"])
        daily = (planned_spending / count).quantize(CENT, rounding=ROUND_DOWN)
        return [
            {
                "date": r["date"],
                "balance": r["closing_balance"]
                - charges
                - (planned_spending if i == count - 1 else daily * (i + 1)),
            }
            for i, r in enumerate(flow["days"])
        ]

    baseline_days = scenario(remaining)
    reduced_days = scenario(remaining - assigned)
    forecast_days = [
        {"date": r["date"], "balance": r["closing_balance"] - charges} for r in flow["days"]
    ]
    received = flow["recorded_balance_as_of"]
    savings_date = stable_date(reduced_days, margin + desired) if target is not None else None
    # Do not suggest saving today while the actual recorded account is negative, even
    # if today's unreceived income makes the end-of-day projection positive.
    if received < margin + desired and savings_date == flow["as_of"]:
        savings_date = next(
            (
                r["date"]
                for r in reduced_days[1:]
                if stable_date(
                    [d for d in reduced_days if d["date"] >= r["date"]], margin + desired
                )
                == r["date"]
            ),
            None,
        )
    incomes = [
        {
            "date": r["date"],
            "description": e["description"],
            "amount": e["change"],
            "estimated": e["estimated"],
            "overdue": e["overdue"],
        }
        for r in flow["days"]
        for e in r["events"]
        if e["change"] > ZERO
    ]
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
    first_income = incomes[0]["date"] if incomes else None
    before_income = sum(
        (r["amount"] for r in obligations if first_income is None or r["date"] <= first_income),
        ZERO,
    )
    warnings = []
    if estimated_bank_charges is None:
        warnings.append("Juros e encargos desconhecidos; não incluídos no cenário.")
    if margin == ZERO:
        warnings.append("Margem para imprevistos não reservada neste cenário.")
    if any(r["status"] == "NEEDS_REVIEW" for r in budgets["items"]):
        warnings.append(
            "Há categorias sem histórico suficiente; necessidades ausentes podem alterar o plano."
        )
    if target is None:
        warnings.append("Meta de poupança não configurada.")
    if received < ZERO:
        action = (
            "Evite novos gastos opcionais até a entrada ser recebida e o saldo "
            "regularizado. Preserve as contas essenciais."
        )
    else:
        action = (
            "Proteja as próximas contas antes de usar a sobra; confira se as receitas "
            "previstas foram recebidas."
        )
    return {
        "as_of": flow["as_of"],
        "end_date": flow["end_date"],
        "recorded_balance": received,
        "recorded_deficit": max(ZERO, -received),
        "estimated_bank_charges": estimated_bank_charges,
        "safety_margin": margin,
        "savings_target": target,
        "recovery_date_known_obligations": stable_date(forecast_days, margin),
        "recovery_date_with_planned_spending": stable_date(baseline_days, margin),
        "recovery_date_with_suggested_reductions": stable_date(reduced_days, margin),
        "conditional_savings_date": savings_date,
        "first_expected_income_date": first_income,
        "obligations_until_first_income": before_income,
        "expected_incomes": incomes,
        "protected_obligations": obligations,
        "remaining_planned_spending": remaining,
        "forecast_after_planned_spending": after_plan,
        "required_reduction_for_goal": needed,
        "optional_spending_to_defer_while_negative": pool if received < ZERO else ZERO,
        "protected_recorded_obligations_total": sum((r["amount"] for r in obligations), ZERO),
        "protected_unrecorded_planning": max(ZERO, remaining - pool),
        "suggested_reduction_total": assigned,
        "suggested_reductions": proposals,
        "unresolved_shortfall_for_goal": max(ZERO, needed - assigned),
        "forecast_after_reductions_and_goal": after_plan + assigned - desired,
        "action_today": action,
        "warnings": warnings,
        "basis": "Plano consolidado até o fim do mês, sem movimentar dinheiro. "
        "Datas de recuperação são estimativas que permanecem positivas até o fim do horizonte. "
        "Gastos variáveis restantes são distribuídos igualmente por dia; "
        "não são vencimentos reais. "
        "Cortes sugeridos afetam somente gastos ajustáveis ainda não registrados; obrigações "
        "e alimentação necessária são preservadas. Receitas previstas não são dinheiro recebido. "
        "Guardar a meta depende de confirmar a entrada e o saldo; "
        "não financiar poupança com dívida. "
        "Sem conciliação bancária, crédito ou juros presumidos. Data ausente significa "
        "recuperação ou poupança não demonstrada neste horizonte, não impossibilidade definitiva.",
    }
