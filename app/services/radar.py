"""Evidence-based recurring cost radar; never cancels obligations or claims payoff."""

import re
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app.services.comparison import category_comparison
from app.services.occurrences import occurrence_dates
from app.services.schedules import month_date

ZERO = Decimal("0")
CENT = Decimal("0.01")


def recurring_radar(
    rules: list[dict[str, Any]],
    plans: list[dict[str, Any]],
    transactions: list[dict[str, Any]],
    categories: list[dict[str, Any]],
    as_of: date,
) -> dict[str, Any]:
    names = {r["id"]: r for r in categories}
    horizon = month_date(as_of, 12) - timedelta(days=1)
    recurrences = []
    for rule in rules:
        if not rule["active"] or rule["type"] != "EXPENSE":
            continue
        due = list(occurrence_dates(rule, as_of, horizon))
        if not due:
            continue
        amount = rule["expected_amount"]
        annual = amount * 12 if rule["frequency"] == "MONTHLY" else amount
        category = names.get(rule.get("category_id"), {})
        subscription = any(
            w in category.get("name", "").casefold()
            for w in ("assinatura", "streaming", "ia e tecnologia")
        ) or any(w in rule["description"].casefold() for w in ("duolingo", "prime video"))
        represented = {
            (r["transaction_date"].year, r["transaction_date"].month): r
            for r in transactions
            if r.get("recurrence_id") == rule["id"]
        }
        costs = []
        unpaid_dates = []
        for day in due:
            existing = represented.get((day.year, day.month))
            if existing and existing["status"] == "CANCELLED":
                continue
            # Paid occurrences are represented already; cancellation cannot recover them.
            if (
                existing
                and existing["status"] == "POSTED"
                and existing["transaction_date"] <= as_of
            ):
                continue
            costs.append(existing["amount"] if existing else amount)
            unpaid_dates.append(day)
        review = subscription and category.get("expense_class") == "SUPERFLUOUS"
        recurrences.append(
            {
                "description": rule["description"],
                "category": category.get("name", "Sem categoria"),
                "frequency": rule["frequency"],
                "expected_payment": amount,
                "monthly_equivalent": (annual / 12).quantize(CENT, rounding=ROUND_HALF_UP),
                "annual_run_rate": annual,
                "next_planned_date": unpaid_dates[0] if unpaid_dates else None,
                "planned_unpaid_cost_next_12_months": sum(costs, ZERO),
                "is_subscription_candidate": subscription,
                "review_candidate": review,
                "conditional_avoided_cost_if_cancelled": sum(costs, ZERO) if review else None,
                "basis": "Valor esperado e calendário ativo; custo anual de referência não é "
                "cobrança mensal de serviços anuais. Revisar uso/contrato antes de cancelar; "
                "estimativa não inclui multas nem devolução de valores pagos.",
            }
        )
    ending = []
    review_items = []
    window_end = month_date(as_of, 6)
    for plan in plans:
        if not plan["active"]:
            continue
        final = month_date(plan["first_installment_date"], plan["total_installments"] - 1)
        if not as_of.replace(day=1) <= final <= window_end:
            continue
        linked = [r for r in transactions if r.get("installment_plan_id") == plan["id"]]
        finals = [r for r in linked if r.get("installment_number") == plan["total_installments"]]
        if len(finals) == 1 and finals[0]["status"] == "CANCELLED":
            continue
        ending.append(
            {
                "description": plan["description"],
                "last_planned_date": final,
                "release_from_month": month_date(final, 1, 1),
                "potential_monthly_release": plan["installment_amount"],
                "final_recorded_status": finals[0]["status"]
                if len(finals) == 1
                else "MISSING_OR_MULTIPLE",
                "source": "PLAN",
                "payoff_confirmed": False,
            }
        )
    # Migrated installments are evidence from explicit PAR IDs and x/y, not recurrences.
    groups: dict[Any, list[Any]] = {}
    for row in transactions:
        if row["type"] != "EXPENSE" or row.get("installment_plan_id"):
            continue
        marker = re.search(r"\bPAR-[A-Z0-9-]+", row.get("notes") or "", re.IGNORECASE)
        number = re.search(
            r"parcela\s+(\d+)\s*/\s*(\d+)",
            row["description"] + " " + (row.get("notes") or ""),
            re.IGNORECASE,
        )
        if marker and number:
            groups.setdefault((row.get("account_id"), marker[0].upper()), []).append(
                (row, int(number[1]), int(number[2]))
            )
    for group in groups.values():
        totals = {r[2] for r in group}
        nums = [r[1] for r in group]
        finals = [r[0] for r in group if r[1] == r[2]]
        if len(totals) != 1 or len(nums) != len(set(nums)) or len(finals) != 1:
            review_items.append(
                {
                    "description": group[0][0]["description"],
                    "reason": "Parcelas migradas sem término único comprovado; revisar.",
                }
            )
            continue
        final_row = finals[0]
        if (
            final_row["status"] != "PENDING"
            or not as_of.replace(day=1) <= final_row["transaction_date"] <= window_end
        ):
            continue
        ending.append(
            {
                "description": re.sub(
                    r"\s*[—-]?\s*parcela\s+\d+\s*/\s*\d+.*$",
                    "",
                    final_row["description"],
                    flags=re.IGNORECASE,
                ),
                "last_planned_date": final_row["transaction_date"],
                "release_from_month": month_date(final_row["transaction_date"], 1, 1),
                "potential_monthly_release": final_row["amount"],
                "final_recorded_status": final_row["status"],
                "source": "MIGRATED_EVIDENCE",
                "payoff_confirmed": False,
            }
        )
    previous = as_of.replace(day=1) - timedelta(days=1)
    complete = category_comparison(categories, transactions, previous)
    sufficient = all(p["history_observed_from_start"] for p in complete["periods"])
    growing = []
    if sufficient:
        for item in complete["items"]:
            values = [m["paid_net"] for m in item["months"]]
            if ZERO < values[0] < values[1] < values[2]:
                growing.append(
                    {
                        "category": item["category"],
                        "months": item["months"],
                        "increase_over_period": values[2] - values[0],
                        "basis": "Três meses fechados de consumo registrado; verificar férias, "
                        "estornos e pagamentos antecipados antes de concluir tendência.",
                    }
                )
    return {
        "as_of": as_of,
        "cost_horizon_end": horizon,
        "recurring_costs": sorted(recurrences, key=lambda r: r["annual_run_rate"], reverse=True),
        "installments_ending_next_six_months": sorted(ending, key=lambda r: r["last_planned_date"]),
        "installment_evidence_to_review": review_items,
        "growing_categories_three_complete_months": growing,
        "growth_history_sufficient": sufficient,
        "growth_periods": complete["periods"],
        "basis": "Radar somente de leitura. Possível economia exige decisão do usuário e "
        "condições contratuais; assinatura não é presumida sem uso. Fim planejado de "
        "parcela não confirma quitação ou dinheiro livre: atrasos e novas despesas "
        "podem consumir a folga. Não cancelar empréstimos, parcelas ou serviços "
        "essenciais como se fossem assinaturas opcionais. Não duplica previsões nem grava dados.",
    }
