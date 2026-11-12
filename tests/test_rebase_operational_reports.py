"""Regression coverage for archived transactions and reconciled pre-cutoff payments.

Uses only synthetic data in the disposable test database; never production balances.
"""

from fastapi.testclient import TestClient


def test_rebased_agenda_and_dashboard_ignore_archives_and_pre_cutoff_payments(
    client: TestClient,
) -> None:
    legacy_account = client.post(
        "/api/v1/accounts",
        json={"name": "Legacy wallet", "opening_balance": "200.00"},
    ).json()["id"]
    for key, status, amount, day in [
        ("old paid item", "POSTED", "20.00", "2026-11-02"),
        ("old duplicate forecast", "PENDING", "100.00", "2026-11-03"),
    ]:
        response = client.post(
            "/api/v1/transactions",
            json={
                "description": key,
                "type": "EXPENSE",
                "status": status,
                "amount": amount,
                "transaction_date": day,
                "account_id": legacy_account,
                "idempotency_key": key,
            },
        )
        assert response.status_code == 201, response.text

    rebase = {
        "batch_key": "regression-cutoff-nov-2026",
        "as_of_date": "2026-11-12",
        "expected_active_transactions": 2,
        "backup_confirmed": True,
        "accounts": [
            {"name": "Essenciais", "opening_balance": "1142.06"},
            {"name": "Não Essenciais", "opening_balance": "356.16"},
            {"name": "Carteira", "opening_balance": "338.51"},
            {
                "name": "Cofrinho Conjunto",
                "opening_balance": "0",
                "include_in_summary": False,
            },
        ],
        "transactions": [
            {
                "source_id": "school-original",
                "transaction_date": "2026-11-03",
                "description": "Historical school bill",
                "type": "EXPENSE",
                "status": "PENDING",
                "amount": "100.00",
                "account_name": "Essenciais",
                "category": "Education",
            },
            {
                "source_id": "future-subscription",
                "transaction_date": "2026-11-16",
                "description": "Future subscription",
                "type": "EXPENSE",
                "status": "PENDING",
                "amount": "9.90",
                "account_name": "Não Essenciais",
                "category": "Subscriptions",
            },
            {
                "source_id": "future-salary",
                "transaction_date": "2026-11-27",
                "description": "Future salary estimate",
                "type": "INCOME",
                "status": "PENDING",
                "amount": "1200.00",
                "account_name": "Carteira",
                "category": "Salary",
            },
        ],
    }
    result = client.post("/api/v1/finance/rebase/commit", json=rebase)
    assert result.status_code == 200, result.text
    assert result.json()["archived"]["transactions"] == 2

    active = client.get("/api/v1/transactions").json()
    history = client.get("/api/v1/transactions", params={"archive_mode": "archived"}).json()
    assert len(active) == 3
    assert len(history) == 2
    historical = next(t for t in active if t["description"] == "Historical school bill")
    paid = client.patch(
        f"/api/v1/transactions/{historical['id']}", json={"status": "POSTED"}
    )
    assert paid.status_code == 200, paid.text

    params = {"year": 2026, "month": 11, "as_of": "2026-11-12"}
    dashboard = client.get("/api/v1/dashboard/monthly", params=params).json()
    assert dashboard["current_balance"] == "1836.73"
    assert dashboard["expense_month"] == "0"
    assert dashboard["income_month"] == "0"
    assert dashboard["pending_commitments"] == "9.90"
    assert dashboard["expected_income"] == "1200.00"
    assert dashboard["top_categories"] == []
    assert dashboard["category_comparison"]["totals"][-1]["paid_net"] == "0"

    agenda = client.get("/api/v1/dashboard/agenda", params=params).json()
    assert agenda["summary"]["paid"] == {"count": 0, "amount": "0"}
    assert agenda["summary"]["pending"] == {"count": 1, "amount": "9.90"}
    assert agenda["summary"]["overdue"] == {"count": 0, "amount": "0"}
    assert [row["description"] for row in agenda["items"]] == ["Future subscription"]
    assert not any(row["description"] == "old duplicate forecast" for row in agenda["items"])

    accounts = client.get("/api/v1/accounts").json()
    assert {row["name"]: row["current_balance"] for row in accounts} == {
        "Carteira": "338.51",
        "Cofrinho Conjunto": "0.00",
        "Essenciais": "1142.06",
        "Não Essenciais": "356.16",
    }

    essential_id = next(row["account_id"] for row in accounts if row["name"] == "Essenciais")
    new_expense = client.post(
        "/api/v1/transactions",
        json={
            "description": "Actual post-cutoff purchase",
            "amount": "5.00",
            "type": "EXPENSE",
            "status": "POSTED",
            "account_id": essential_id,
            "transaction_date": "2026-11-12",
            "idempotency_key": "new-post-cutoff-purchase",
        },
    )
    assert new_expense.status_code == 201, new_expense.text
    updated = client.get("/api/v1/dashboard/monthly", params=params).json()
    assert updated["current_balance"] == "1831.73"
    assert updated["expense_month"] == "5.00"
    assert updated["pending_commitments"] == "9.90"
    agenda_after = client.get("/api/v1/dashboard/agenda", params=params).json()
    assert agenda_after["summary"]["paid"]["count"] == 1
    assert agenda_after["summary"]["paid"]["amount"] == "5.00"


def test_agenda_does_not_allow_archived_rule_to_suppress_new_forecast(
    client: TestClient,
) -> None:
    # Direct read paths should never surface archived records as operational bills.
    account = client.post(
        "/api/v1/accounts", json={"name": "Checking", "opening_balance": "100"}
    ).json()["id"]
    old = client.post(
        "/api/v1/transactions",
        json={
            "description": "Old subscription",
            "amount": "15.00",
            "type": "EXPENSE",
            "status": "PENDING",
            "account_id": account,
            "transaction_date": "2026-11-16",
            "idempotency_key": "old-sub",
        },
    )
    assert old.status_code == 201
    old_id = old.json()["id"]
    # Verify the pure transformation remains defensive when given archived rows.
    from datetime import date, datetime
    from decimal import Decimal

    from app.services.agenda import expense_agenda

    row = {
        "id": old_id,
        "type": "EXPENSE",
        "status": "PENDING",
        "description": "Archived recurrence",
        "transaction_date": date(2026, 11, 16),
        "amount": Decimal("15.00"),
        "archived_at": datetime(2026, 11, 12),
        "recurrence_id": "old-rule",
    }
    rule = {
        "id": "new-rule",
        "type": "EXPENSE",
        "description": "Active recurrence",
        "active": True,
        "frequency": "MONTHLY",
        "due_day": 16,
        "start_date": date(2026, 11, 1),
        "end_date": date(2026, 11, 30),
        "expected_amount": Decimal("15.00"),
    }
    result = expense_agenda(
        [row], [rule], [], [], date(2026, 11, 1), date(2026, 11, 30), date(2026, 11, 12)
    )
    assert len(result["items"]) == 1
    assert result["items"][0]["description"] == "Active recurrence"
    assert result["items"][0]["recorded"] is False
