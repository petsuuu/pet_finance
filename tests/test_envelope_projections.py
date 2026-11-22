"""Envelope projection and partial receipt read-only regressions."""

from datetime import date
from decimal import Decimal
from uuid import uuid4

from fastapi.testclient import TestClient

from app.services.monthly_planning import envelope_projections, income_reconciliation


def item(
    account_id: object,
    amount: str,
    kind: str,
    status: str,
    day: int,
    **extra: object,
) -> dict[str, object]:
    return {
        "id": uuid4(),
        "account_id": account_id,
        "amount": Decimal(amount),
        "type": kind,
        "status": status,
        "transaction_date": date(2026, 11, day),
        "recurrence_id": None,
        **extra,
    }


def test_envelopes_reserve_pending_expenses_and_outgoing_transfer_only() -> None:
    essential, wallet = uuid4(), uuid4()
    accounts = [
        {"id": essential, "name": "Essenciais", "opening_balance": Decimal("100")},
        {"id": wallet, "name": "Carteira", "opening_balance": Decimal("200")},
    ]
    rows = [
        item(essential, "40", "EXPENSE", "PENDING", 22),
        item(essential, "20", "EXPENSE", "POSTED", 21),
        item(wallet, "30", "TRANSFER", "PENDING", 22, target_account_id=essential),
        item(wallet, "50", "INCOME", "PENDING", 25),
        item(wallet, "25", "TRANSFER", "POSTED", 20, target_account_id=essential),
        item(essential, "999", "EXPENSE", "CANCELLED", 24),
    ]
    original = [dict(row) for row in rows]
    result = envelope_projections(
        accounts, rows, [], date(2026, 11, 1), date(2026, 11, 30), date(2026, 11, 22)
    )
    named = {row["name"]: row for row in result["items"]}
    assert named["Essenciais"]["recorded_balance"] == Decimal("105")
    assert named["Essenciais"]["after_known_commitments_without_income"] == Decimal("65")
    assert named["Essenciais"]["pending_transfer_in_not_available"] == Decimal("30")
    assert named["Essenciais"]["projected_after_unconfirmed_inflows"] == Decimal("95")
    assert named["Carteira"]["recorded_balance"] == Decimal("175")
    assert named["Carteira"]["after_known_commitments_without_income"] == Decimal("145")
    assert named["Carteira"]["expected_income_not_available"] == Decimal("50")
    assert result["totals"]["recorded_balance"] == Decimal("280")
    assert result["totals"]["pending_transfer_out"] == Decimal("30")
    assert result["totals"]["pending_transfer_in_not_available"] == Decimal("30")
    assert rows == original


def test_envelopes_do_not_count_cutoff_history_or_recreate_cancelled_rule() -> None:
    account, rule_id = uuid4(), uuid4()
    accounts = [
        {
            "id": account,
            "name": "Essenciais",
            "opening_balance": Decimal("200"),
            "opening_balance_date": date(2026, 11, 12),
        }
    ]
    rule = {
        "id": rule_id,
        "account_id": account,
        "description": "Internet",
        "type": "EXPENSE",
        "frequency": "MONTHLY",
        "due_day": 20,
        "start_date": date(2026, 11, 1),
        "end_date": None,
        "active": True,
        "expected_amount": Decimal("35"),
    }
    rows = [
        item(account, "80", "EXPENSE", "POSTED", 8),
        item(account, "35", "EXPENSE", "CANCELLED", 20, recurrence_id=rule_id),
    ]
    result = envelope_projections(
        accounts, rows, [rule], date(2026, 11, 1), date(2026, 11, 30), date(2026, 11, 22)
    )
    assert result["items"][0]["recorded_balance"] == Decimal("200")
    assert result["items"][0]["unrecorded_recurring_expenses"] == Decimal("0")
    assert result["items"][0]["after_known_commitments_without_income"] == Decimal("200")


def test_partial_receipt_does_not_invent_the_unrecorded_difference() -> None:
    account, rule_id = uuid4(), uuid4()
    rule = {
        "id": rule_id,
        "account_id": account,
        "description": "Salary",
        "type": "INCOME",
        "frequency": "MONTHLY",
        "due_day": 25,
        "start_date": date(2026, 11, 1),
        "end_date": None,
        "active": True,
        "expected_amount": Decimal("1000"),
    }
    rows = [
        item(account, "200", "INCOME", "POSTED", 20, recurrence_id=rule_id),
        item(account, "300", "INCOME", "PENDING", 25, recurrence_id=rule_id),
    ]
    result = income_reconciliation(
        rows, [rule], [{"id": account}], date(2026, 11, 1), date(2026, 11, 30), date(2026, 11, 22)
    )
    check = result["recurring_income_checks"][0]
    assert check["state"] == "PARTIALLY_RECEIVED"
    assert check["received_amount"] == Decimal("200")
    assert check["remaining_against_expected"] == Decimal("800")
    assert check["remaining_after_recorded_pending"] == Decimal("500")
    assert check["unrecorded_forecast"] == Decimal("0")
    assert result["total_expected_not_received"] == Decimal("300")


def test_monthly_dashboard_envelope_projection_is_read_only(client: TestClient) -> None:
    account = client.post("/api/v1/accounts", json={"name": "Essenciais", "opening_balance": "100"})
    assert account.status_code == 201, account.text
    account_id = account.json()["id"]
    transaction = client.post(
        "/api/v1/transactions",
        json={
            "description": "Internet",
            "amount": "30",
            "type": "EXPENSE",
            "status": "PENDING",
            "transaction_date": "2026-11-22",
            "account_id": account_id,
            "idempotency_key": "pr43-internet",
        },
    )
    assert transaction.status_code == 201, transaction.text
    accounts_before = client.get("/api/v1/accounts").json()
    transactions_before = client.get("/api/v1/transactions").json()
    history_before = client.get("/api/v1/audit/transactions").json()
    report = client.get(
        "/api/v1/dashboard/monthly",
        params={"year": 2026, "month": 11, "as_of": "2026-11-22"},
    )
    assert report.status_code == 200, report.text
    item_data = report.json()["envelope_projections"]["items"][0]
    assert item_data["name"] == "Essenciais"
    assert item_data["recorded_balance"] == "100.00"
    assert item_data["pending_expenses"] == "30.00"
    assert item_data["after_known_commitments_without_income"] == "70.00"
    assert client.get("/api/v1/accounts").json() == accounts_before
    assert client.get("/api/v1/transactions").json() == transactions_before
    assert client.get("/api/v1/audit/transactions").json() == history_before
