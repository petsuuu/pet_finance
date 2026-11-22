"""Regression tests: salary-based alerts do not move any money."""

from datetime import date
from decimal import Decimal
from typing import Any

from fastapi.testclient import TestClient

from app.services.post_salary_alerts import post_salary_envelope_alerts

Report = dict[str, Any]


def reports(received: str, pending: str, salary: bool = True) -> tuple[Report, Report, Report]:
    return (
        {
            "recurring_income_checks": [
                {
                    "description": "Salário mensal" if salary else "Aluguel",
                    "expected_amount": Decimal("1000"),
                    "due_date": date(2026, 11, 25),
                    "received_amount": Decimal(received),
                    "pending_amount": Decimal(pending),
                }
            ]
        },
        {
            "items": [
                {
                    "account_id": "essential",
                    "name": "Essenciais",
                    "after_known_commitments_without_income": Decimal("-200"),
                },
                {
                    "account_id": "optional",
                    "name": "Não Essenciais",
                    "after_known_commitments_without_income": Decimal("-50"),
                },
            ]
        },
        {
            "recorded_cash_scenario": {
                "suggested_total": Decimal("100"),
                "unfunded_envelopes": [
                    {"account_id": "essential", "remaining_gap": Decimal("100")},
                    {"account_id": "optional", "remaining_gap": Decimal("50")},
                ],
            },
            "conditional_expected_income_scenario": {
                "unfunded_envelopes": [
                    {"account_id": "essential", "remaining_gap": Decimal("0")},
                    {"account_id": "optional", "remaining_gap": Decimal("20")},
                ]
            },
        },
    )


def test_pending_salary_never_unblocks_current_cash() -> None:
    source = reports("0", "900")
    original = str(source)
    data = post_salary_envelope_alerts(*source)
    assert data["salary"]["state"] == "AWAITING_RECEIPT"
    assert data["salary"]["not_recorded_against_expected"] == Decimal("100")
    assert data["salary"]["bank_deposit_verified"] is False
    assert data["review_gate"] == "AWAIT_SALARY_CONFIRMATION"
    assert data["total_unfunded_after_recorded_suggestions"] == Decimal("150")
    assert data["total_unfunded_after_conditional_suggestions"] == Decimal("20")
    assert data["alerts"][0]["envelope"] == "Essenciais"
    assert data["alerts"][1]["envelope"] == "Não Essenciais"
    assert str(source) == original


def test_partial_and_fully_recorded_salary_have_distinct_review_gates() -> None:
    partial = post_salary_envelope_alerts(*reports("200", "700"))
    fully_recorded = post_salary_envelope_alerts(*reports("1000", "0"))
    assert partial["salary"]["state"] == "PARTIALLY_RECORDED"
    assert partial["review_gate"] == "REVIEW_PARTIAL_RECEIPT"
    assert fully_recorded["salary"]["state"] == "SALARY_RECORDED"
    assert fully_recorded["review_gate"] == "REVIEW_RECORDED_CASH"
    assert fully_recorded["requires_user_confirmation"] is True


def test_salary_timing_uses_calendar_due_date_without_unlocking_cash() -> None:
    upcoming = post_salary_envelope_alerts(*reports("0", "900"), as_of=date(2026, 11, 22))
    upcoming_salary = upcoming["salary"]
    assert upcoming_salary["earliest_outstanding_due_date"] == date(2026, 11, 25)
    assert upcoming_salary["days_until_due"] == 3
    assert upcoming_salary["receipt_overdue"] is False
    assert upcoming_salary["overdue_days"] == 0
    assert upcoming["review_gate"] == "AWAIT_SALARY_CONFIRMATION"

    overdue = post_salary_envelope_alerts(*reports("0", "900"), as_of=date(2026, 11, 27))
    assert overdue["salary"]["receipt_overdue"] is True
    assert overdue["salary"]["days_until_due"] == -2
    assert overdue["salary"]["overdue_days"] == 2
    assert overdue["salary"]["recorded_received"] == Decimal("0")
    assert overdue["review_gate"] == "AWAIT_SALARY_CONFIRMATION"

    partial = post_salary_envelope_alerts(*reports("200", "700"), as_of=date(2026, 11, 26))
    assert partial["salary"]["receipt_overdue"] is True
    assert partial["salary"]["overdue_days"] == 1
    assert partial["review_gate"] == "REVIEW_PARTIAL_RECEIPT"

    complete = post_salary_envelope_alerts(*reports("1000", "0"), as_of=date(2026, 11, 26))
    assert complete["salary"]["earliest_outstanding_due_date"] is None
    assert complete["salary"]["days_until_due"] is None
    assert complete["salary"]["receipt_overdue"] is False


def test_salary_timing_ignores_unrelated_income_and_missing_reference_date() -> None:
    unrelated = post_salary_envelope_alerts(
        *reports("1000", "0", salary=False), as_of=date(2026, 11, 30)
    )
    assert unrelated["salary"]["earliest_outstanding_due_date"] is None
    assert unrelated["salary"]["receipt_overdue"] is False

    unknown_date = post_salary_envelope_alerts(*reports("0", "900"))
    assert unknown_date["salary"]["earliest_outstanding_due_date"] == date(2026, 11, 25)
    assert unknown_date["salary"]["days_until_due"] is None
    assert unknown_date["salary"]["overdue_days"] is None


def test_follow_up_prioritizes_safe_salary_checks_by_due_date() -> None:
    waiting = post_salary_envelope_alerts(*reports("0", "900"), as_of=date(2026, 11, 20))
    assert waiting["follow_up"]["level"] == "INFO"
    assert waiting["follow_up"]["action"] == "WAIT_FOR_SALARY"

    upcoming = post_salary_envelope_alerts(*reports("0", "900"), as_of=date(2026, 11, 22))
    assert upcoming["follow_up"]["level"] == "UPCOMING"
    assert upcoming["follow_up"]["action"] == "CHECK_UPCOMING_SALARY"
    assert upcoming["follow_up"]["read_only"] is True
    assert upcoming["follow_up"]["requires_user_confirmation"] is True
    assert upcoming["review_gate"] == "AWAIT_SALARY_CONFIRMATION"

    due_today = post_salary_envelope_alerts(*reports("0", "900"), as_of=date(2026, 11, 25))
    assert due_today["follow_up"]["action"] == "CHECK_UPCOMING_SALARY"

    overdue = post_salary_envelope_alerts(*reports("0", "900"), as_of=date(2026, 11, 27))
    assert overdue["follow_up"]["level"] == "ATTENTION"
    assert overdue["follow_up"]["action"] == "VERIFY_OVERDUE_SALARY"
    assert overdue["salary"]["bank_deposit_verified"] is False
    assert overdue["review_gate"] == "AWAIT_SALARY_CONFIRMATION"


def test_follow_up_distinguishes_partial_full_and_unknown_salary() -> None:
    partial = post_salary_envelope_alerts(*reports("200", "700"), as_of=date(2026, 11, 20))
    assert partial["follow_up"]["action"] == "RECONCILE_PARTIAL_SALARY"
    assert partial["review_gate"] == "REVIEW_PARTIAL_RECEIPT"

    partial_overdue = post_salary_envelope_alerts(*reports("200", "700"), as_of=date(2026, 11, 27))
    assert partial_overdue["follow_up"]["action"] == "VERIFY_OVERDUE_SALARY"

    fully_recorded = post_salary_envelope_alerts(*reports("1000", "0"), as_of=date(2026, 11, 27))
    assert fully_recorded["follow_up"]["action"] == "REVIEW_ENVELOPE_SUGGESTIONS"
    assert fully_recorded["salary"]["bank_deposit_verified"] is False
    assert fully_recorded["follow_up"]["read_only"] is True

    income, envelope, allocations = reports("1000", "0")
    allocations["recorded_cash_scenario"]["suggested_total"] = Decimal("0")
    no_cash_to_review = post_salary_envelope_alerts(
        income, envelope, allocations, as_of=date(2026, 11, 27)
    )
    assert no_cash_to_review["follow_up"]["action"] == "REVIEW_RECORDED_SALARY"

    unidentified = post_salary_envelope_alerts(
        *reports("0", "0", salary=False), as_of=date(2026, 11, 27)
    )
    assert unidentified["follow_up"]["action"] == "VERIFY_SALARY_RECURRENCE"
    assert unidentified["follow_up"]["level"] == "REVIEW"
    assert unidentified["review_gate"] == "AWAIT_SALARY_CONFIRMATION"


def test_unrelated_income_does_not_count_as_salary() -> None:
    data = post_salary_envelope_alerts(*reports("1000", "0", salary=False))
    assert data["salary"]["state"] == "SALARY_RULE_NOT_IDENTIFIED"
    assert data["salary"]["recorded_received"] == Decimal("0")
    assert data["review_gate"] == "AWAIT_SALARY_CONFIRMATION"


def test_no_funded_suggestions_even_after_salary_stays_review_only() -> None:
    income, envelopes, allocations = reports("1000", "0")
    allocations["recorded_cash_scenario"]["suggested_total"] = Decimal("0")
    data = post_salary_envelope_alerts(income, envelopes, allocations)
    assert data["review_gate"] == "REVIEW_SHORTFALLS"
    assert not data["recorded_cash_suggestions_available"]


def test_dashboard_alerts_do_not_mutate_financial_records(client: TestClient) -> None:
    account = client.post("/api/v1/accounts", json={"name": "Carteira", "opening_balance": "200"})
    essential = client.post("/api/v1/accounts", json={"name": "Essenciais", "opening_balance": "0"})
    assert account.status_code == 201 and essential.status_code == 201
    recurrence = client.post(
        "/api/v1/recurrences",
        json={
            "description": "Salário mensal",
            "type": "INCOME",
            "account_id": account.json()["id"],
            "expected_amount": "1000",
            "due_day": 25,
            "start_date": "2026-11-01",
        },
    )
    assert recurrence.status_code == 201, recurrence.text
    pending = client.post(
        "/api/v1/transactions",
        json={
            "description": "Conta mensal",
            "type": "EXPENSE",
            "status": "PENDING",
            "amount": "40",
            "transaction_date": "2026-11-22",
            "account_id": essential.json()["id"],
            "idempotency_key": "pr45-essentials",
        },
    )
    assert pending.status_code == 201, pending.text
    before_accounts = client.get("/api/v1/accounts").json()
    before_transactions = client.get("/api/v1/transactions").json()
    before_history = client.get("/api/v1/audit/transactions").json()
    response = client.get(
        "/api/v1/dashboard/monthly",
        params={"year": 2026, "month": 11, "as_of": "2026-11-22"},
    )
    assert response.status_code == 200, response.text
    data = response.json()["post_salary_envelope_alerts"]
    assert data["salary"]["state"] == "AWAITING_RECEIPT"
    assert data["salary"]["recorded_received"] == "0"
    assert data["salary"]["bank_deposit_verified"] is False
    assert data["salary"]["earliest_outstanding_due_date"] == "2026-11-25"
    assert data["salary"]["days_until_due"] == 3
    assert data["salary"]["receipt_overdue"] is False
    assert data["follow_up"]["action"] == "CHECK_UPCOMING_SALARY"
    assert data["follow_up"]["read_only"] is True
    assert data["shortfall_alert_count"] == 1
    assert data["alerts"][0]["envelope"] == "Essenciais"
    assert client.get("/api/v1/accounts").json() == before_accounts
    assert client.get("/api/v1/transactions").json() == before_transactions
    assert client.get("/api/v1/audit/transactions").json() == before_history
