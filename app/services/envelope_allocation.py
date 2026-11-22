"""Read-only suggestions to fund under-reserved envelopes, never transfers."""

from decimal import Decimal
from typing import Any

ZERO = Decimal("0")
PRIORITY = {"Essenciais": 0, "Não Essenciais": 1}


def envelope_allocation_preview(
    envelope_report: dict[str, Any], monthly_plan: dict[str, Any]
) -> dict[str, Any]:
    """Cap suggestions at the consolidated planning headroom.

    An envelope shortfall is not an instruction to move funds. Only Carteira
    can be a suggested source; savings and excluded accounts are untouched.
    The future scenario is explicitly conditional on unconfirmed receipts.
    """
    items = envelope_report["items"]
    source = next((row for row in items if row["name"] == "Carteira"), None)
    destinations = sorted(
        (row for row in items if row["name"] in PRIORITY),
        key=lambda row: (PRIORITY[row["name"]], row["name"]),
    )

    def scenario(conditional: bool) -> dict[str, Any]:
        if source is None:
            ceiling = ZERO
        elif conditional:
            ceiling = min(
                max(ZERO, Decimal(source["projected_after_unconfirmed_inflows"])),
                max(ZERO, Decimal(monthly_plan["forecast_after_expected_income"])),
            )
        else:
            ceiling = min(
                max(ZERO, Decimal(source["after_known_commitments_without_income"])),
                max(ZERO, Decimal(monthly_plan["available_without_future_income"])),
            )
        available = ceiling
        suggestions: list[dict[str, Any]] = []
        for destination in destinations:
            need = max(
                ZERO, -Decimal(destination["after_known_commitments_without_income"])
            )
            if conditional:
                # Already expected transfers into the destination should not be
                # suggested a second time in the hypothetical future plan.
                need = max(
                    ZERO,
                    need - Decimal(destination["pending_transfer_in_not_available"]),
                )
            amount = min(available, need)
            if (
                amount > ZERO
                and source is not None
                and source["account_id"] != destination["account_id"]
            ):
                suggestions.append(
                    {
                        "from_account_id": source["account_id"],
                        "from_account": source["name"],
                        "to_account_id": destination["account_id"],
                        "to_account": destination["name"],
                        "amount": amount,
                        "conditional_on_future_income": conditional,
                        "action": "REVIEW_ONLY",
                    }
                )
                available -= amount
        total = sum((row["amount"] for row in suggestions), ZERO)
        return {
            "source_ceiling": ceiling,
            "suggested_total": total,
            "unallocated_ceiling": available,
            "suggestions": suggestions,
            "unfunded_envelopes": [
                {
                    "account_id": row["account_id"],
                    "name": row["name"],
                    "remaining_gap": max(
                        ZERO,
                        -Decimal(row["after_known_commitments_without_income"])
                        - (
                            Decimal(row["pending_transfer_in_not_available"])
                            if conditional
                            else ZERO
                        )
                        - sum(
                            (
                                move["amount"]
                                for move in suggestions
                                if move["to_account_id"] == row["account_id"]
                            ),
                            ZERO,
                        ),
                    ),
                }
                for row in destinations
            ],
        }

    return {
        "recorded_cash_scenario": scenario(False),
        "conditional_expected_income_scenario": scenario(True),
        "basis": (
            "Review-only proposal; no balances, payments, transfers or savings are "
            "changed. The recorded-cash scenario is capped by the consolidated "
            "monthly planning headroom and available Carteira balance, after "
            "known commitments, budgets, savings target and margin. The second "
            "scenario is conditional on expected income actually arriving and "
            "must never be treated as currently funded. Targets are Essenciais "
            "then Não Essenciais, and exclude already pending inbound transfers "
            "from conditional gaps. No spending authorization is implied."
        ),
    }
