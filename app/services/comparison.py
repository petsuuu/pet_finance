"""Three-month category consumption comparison without partial-month savings claims."""

from calendar import monthrange
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app.services.budgets import extraordinary, net_expense
from app.services.schedules import month_date

ZERO = Decimal("0")


def category_comparison(
    categories: list[dict[str, Any]],
    transactions: list[dict[str, Any]],
    as_of: date,
) -> dict[str, Any]:
    start = as_of.replace(day=1)
    months = [month_date(start, -i, 1) for i in (2, 1, 0)]
    earliest = min((r["transaction_date"] for r in transactions), default=None)
    eligible = [
        r
        for r in transactions
        if r["status"] == "POSTED"
        and r["type"] in {"EXPENSE", "REFUND"}
        and months[0] <= r["transaction_date"] <= as_of
    ]
    categories_by_id = {r["id"]: r for r in categories}
    eligible = [
        r
        for r in eligible
        if categories_by_id.get(r["category_id"], {}).get("name") != "Ajuste de Saldo"
    ]
    selected = [
        r
        for r in categories
        if r["name"] != "Ajuste de Saldo"
        and (
            r["active"]
            and r["expense_class"] is not None
            or any(t["category_id"] == r["id"] for t in eligible)
        )
    ]
    if any(r["category_id"] is None for r in eligible):
        selected.append({"id": None, "name": "Sem categoria", "active": True})
    periods: list[dict[str, Any]] = []
    for m in months:
        last = date(m.year, m.month, monthrange(m.year, m.month)[1])
        cutoff = date(m.year, m.month, min(as_of.day, last.day))
        observed = earliest is not None and earliest <= m
        periods.append(
            {
                "month": m.isoformat()[:7],
                "full_until": min(last, as_of),
                "matched_until": cutoff,
                "is_partial": min(last, as_of) < last,
                "history_observed_from_start": observed,
            }
        )
    items = []
    for cat in selected:
        own = [r for r in eligible if r["category_id"] == cat["id"]]
        amounts: list[dict[str, Any]] = []
        for p in periods:
            monthly = [r for r in own if r["transaction_date"].isoformat()[:7] == p["month"]]
            matched = [r for r in monthly if r["transaction_date"] <= p["matched_until"]]
            amounts.append(
                {
                    "month": p["month"],
                    "paid_net": sum((net_expense(r) for r in monthly), ZERO),
                    "matched_period_net": sum((net_expense(r) for r in matched), ZERO),
                    "extraordinary_net": sum(
                        (net_expense(r) for r in monthly if extraordinary(r)), ZERO
                    ),
                    "transaction_count": len(monthly),
                }
            )
        previous, current = amounts[-2], amounts[-1]
        difference = current["matched_period_net"] - previous["matched_period_net"]
        baseline = previous["matched_period_net"]
        comparable = all(p["history_observed_from_start"] for p in periods[-2:])
        percent = (
            (difference / baseline * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if comparable and baseline > ZERO
            else None
        )
        trend = (
            "SEM_BASE_COMPARAVEL"
            if not comparable or baseline <= ZERO
            else "GASTO_MENOR"
            if difference < ZERO
            else "GASTO_MAIOR"
            if difference > ZERO
            else "ESTAVEL"
        )
        items.append(
            {
                "category_id": cat["id"],
                "category": cat["name"],
                "category_active": cat["active"],
                "months": amounts,
                "matched_change_amount": difference if comparable else None,
                "matched_change_percent": percent,
                "trend": trend,
            }
        )
    totals = [
        {
            "month": p["month"],
            "paid_net": sum((r["months"][i]["paid_net"] for r in items), ZERO),
            "matched_period_net": sum((r["months"][i]["matched_period_net"] for r in items), ZERO),
        }
        for i, p in enumerate(periods)
    ]
    return {
        "as_of": as_of,
        "periods": periods,
        "items": sorted(items, key=lambda r: r["category"]),
        "totals": totals,
        "basis": "Despesas pagas menos estornos, por categoria direta, sem somar pais e filhos. "
        "Mês atual parcial; variação compara o mesmo intervalo de dias com o mês anterior. "
        "Pendências, cancelados, receitas, transferências e ajustes técnicos não são consumo. "
        "Férias identificadas permanecem no gasto real e aparecem separadamente. "
        "Histórico observado não certifica completude bancária. Ausência de base não é economia; "
        "gasto menor não comprova poupança. Valores usam datas reais registradas, "
        "inclusive pagamentos antecipados.",
    }
