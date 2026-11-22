"""Read-only envelope allocation tests: protect cash and never create transfers."""

from decimal import Decimal

from fastapi.testclient import TestClient

from app.services.envelope_allocation import envelope_allocation_preview


def envelope(
    identity: str, name: str, balance_after_commitments: str, forecast: str, pending_in: str = "0"
) -> dict[str, object]:
    return {
        "account_id": identity,
        "name": name,
        "after_known_commitments_without_income": Decimal(balance_after_commitments),
        "projected_after_unconfirmed_inflows": Decimal(forecast),
        "pending_transfer_in_not_available": Decimal(pending_in),
    }


def test_allocation_caps_recorded_cash_and_prioritizes_essential_needs() -> None:
    rows = [
        envelope("wallet", "Carteira", "100", "300"),
        envelope("essential", "Essenciais", "-90", "-70", "20"),
        envelope("optional", "Não Essenciais", "-80", "-80"),
    ]
    original = [dict(row) for row in rows]
    plan = {
        "available_without_future_income": Decimal("30"),
        "forecast_after_expected_income": Decimal("130"),
    }
    report = envelope_allocation_preview({"items": rows}, plan)
    current = report["recorded_cash_scenario"]
    conditional = report["conditional_expected_income_scenario"]
    assert current["source_ceiling"] == Decimal("30")
    assert current["suggested_total"] == Decimal("30")
    assert current["suggestions"][0]["to_account"] == "Essenciais"
    assert current["unfunded_envelopes"][0]["remaining_gap"] == Decimal("60")
    assert conditional["source_ceiling"] == Decimal("130")
    assert [r["amount"] for r in conditional["suggestions"]] == [Decimal("70"), Decimal("60")]
    assert conditional["suggested_total"] == Decimal("130")
    assert all(r["conditional_on_future_income"] for r in conditional["suggestions"])
    assert rows == original


def test_negative_global_headroom_cannot_create_funded_transfer() -> None:
    rows = [
        envelope("wallet", "Carteira", "999", "999"),
        envelope("essential", "Essenciais", "-100", "-100"),
    ]
    result = envelope_allocation_preview(
        {"items": rows},
        {
            "available_without_future_income": Decimal("0"),
            "forecast_after_expected_income": Decimal("-10"),
        },
    )
    assert result["recorded_cash_scenario"]["suggestions"] == []
    assert result["conditional_expected_income_scenario"]["suggestions"] == []
    gap = result["recorded_cash_scenario"]["unfunded_envelopes"][0]["remaining_gap"]
    assert gap == Decimal("100")


def test_missing_wallet_never_uses_other_envelopes_as_source() -> None:
    result = envelope_allocation_preview(
        {
            "items": [
                envelope("a", "Essenciais", "50", "50"),
                envelope("b", "Não Essenciais", "-40", "-40"),
            ]
        },
        {
            "available_without_future_income": Decimal("100"),
            "forecast_after_expected_income": Decimal("200"),
        },
    )
    assert result["recorded_cash_scenario"]["suggested_total"] == Decimal("0")
    assert result["conditional_expected_income_scenario"]["suggested_total"] == Decimal("0")


def test_monthly_dashboard_allocation_preview_never_writes(client: TestClient) -> None:
    wallet = client.post("/api/v1/accounts", json={"name": "Carteira", "opening_balance": "200"})
    essentials = client.post(
        "/api/v1/accounts", json={"name": "Essenciais", "opening_balance": "0"}
    )
    assert wallet.status_code == 201 and essentials.status_code == 201
    pending = client.post(
        "/api/v1/transactions",
        json={
            "description": "Conta de luz",
            "type": "EXPENSE",
            "status": "PENDING",
            "amount": "40",
            "transaction_date": "2026-11-22",
            "account_id": essentials.json()["id"],
            "idempotency_key": "pr44-electricity",
        },
    )
    assert pending.status_code == 201, pending.text
    before_accounts = client.get("/api/v1/accounts").json()
    before_transactions = client.get("/api/v1/transactions").json()
    before_history = client.get("/api/v1/audit/transactions").json()

    dashboard = client.get(
        "/api/v1/dashboard/monthly",
        params={"year": 2026, "month": 11, "as_of": "2026-11-22"},
    )
    assert dashboard.status_code == 200, dashboard.text
    result = dashboard.json()["envelope_allocation_preview"]
    assert result["recorded_cash_scenario"]["suggested_total"] == "40.00"
    proposed = result["recorded_cash_scenario"]["suggestions"][0]
    assert proposed["from_account"] == "Carteira"
    assert proposed["to_account"] == "Essenciais"
    assert proposed["action"] == "REVIEW_ONLY"
    assert client.get("/api/v1/accounts").json() == before_accounts
    assert client.get("/api/v1/transactions").json() == before_transactions
    assert client.get("/api/v1/audit/transactions").json() == before_history
