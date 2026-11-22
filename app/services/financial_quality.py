"""Read-only financial risk signals composed from existing validated dashboards.

Balances below zero are liquidity warnings, not accounting inconsistencies.
All amounts are Decimal and future income never becomes spendable cash.
"""

from decimal import Decimal
from typing import Any

ZERO = Decimal("0")


def financial_quality_signals(quality: dict[str, Any], monthly: dict[str, Any]) -> dict[str, Any]:
    """Combine integrity findings and cash-risk signals without writes."""
    alerts: list[dict[str, Any]] = []
    projections = monthly["envelope_projections"]["items"]
    for envelope in projections:
        account_id = envelope["account_id"]
        name = envelope["name"]
        balance = Decimal(envelope["recorded_balance"])
        if balance < ZERO:
            alerts.append(
                {
                    "code": "NEGATIVE_ENVELOPE_BALANCE",
                    "severity": "REVIEW",
                    "account_id": account_id,
                    "account_name": name,
                    "amount": -balance,
                    "detail": "Saldo registrado negativo; não significa divergência contábil.",
                }
            )
        shortfall = Decimal(envelope["shortfall_without_income"])
        if shortfall > ZERO:
            alerts.append(
                {
                    "code": "ENVELOPE_COMMITMENT_SHORTFALL",
                    "severity": "REVIEW",
                    "account_id": account_id,
                    "account_name": name,
                    "amount": shortfall,
                    "detail": (
                        "Compromissos conhecidos superam o saldo do envelope; "
                        "receitas futuras não estão disponíveis."
                    ),
                }
            )

    for item in monthly["category_budgets"]["items"]:
        if item["status"] == "EXCEEDED" and item["category_active"]:
            alerts.append(
                {
                    "code": "CATEGORY_BUDGET_EXCEEDED",
                    "severity": "REVIEW",
                    "category": item["category"],
                    "amount": Decimal(item["excess"]),
                    "detail": "Teto de planejamento excedido por gastos pagos e/ou pendentes.",
                }
            )
    planning = monthly["monthly_planning"]
    if Decimal(planning["forecast_shortfall"]) > ZERO:
        alerts.append(
            {
                "code": "FORECAST_SHORTFALL",
                "severity": "REVIEW",
                "amount": Decimal(planning["forecast_shortfall"]),
                "detail": "Projeção após compromissos e metas fica negativa; não é saldo atual.",
            }
        )
    accounting_status = quality["status"]
    status = (
        "CRITICAL"
        if accounting_status == "CRITICAL"
        else "REVIEW"
        if accounting_status == "REVIEW" or alerts
        else "OK"
    )
    return {
        "read_only": True,
        "status": status,
        "integrity_status": accounting_status,
        "risk_status": "REVIEW" if alerts else "OK",
        "quality": quality,
        "financial_alerts": alerts,
        "monthly_projection": {
            "year": monthly["year"],
            "month": monthly["month"],
            "as_of": monthly["as_of"],
            "recorded_balance": monthly["current_balance"],
            "expected_income_not_available": monthly["expected_income"],
            "available_without_future_income": planning["available_without_future_income"],
            "forecast_shortfall": planning["forecast_shortfall"],
            "envelopes": projections,
        },
        "disclaimer": (
            "Diagnóstico somente leitura. Candidatos a duplicidade exigem revisão; "
            "nenhum lançamento será liquidado, excluído ou reclassificado. "
            "Orçamentos não são saldo disponível e receitas previstas não são caixa."
        ),
    }
