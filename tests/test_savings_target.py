"""Savings goal changes are metadata only and never produce ledger movements."""

from fastapi.testclient import TestClient


def test_savings_target_is_independent_of_category_budgets_and_balances(
    client: TestClient,
) -> None:
    account = client.post(
        "/api/v1/accounts", json={"name": "Checking", "opening_balance": "1000.00"}
    ).json()["id"]
    category = client.post("/api/v1/categories", json={"name": "Living"}).json()["id"]
    expense = client.post(
        "/api/v1/transactions",
        json={
            "description": "Actual purchase",
            "type": "EXPENSE",
            "status": "POSTED",
            "amount": "50.00",
            "transaction_date": "2026-11-12",
            "account_id": account,
            "category_id": category,
            "idempotency_key": "goal-test-purchase",
        },
    )
    assert expense.status_code == 201
    budget = client.post(
        "/api/v1/budgets/set",
        json={
            "year": 2026,
            "month": 11,
            "category_id": category,
            "limit_amount": "200.00",
            "repeat_monthly": True,
        },
    )
    assert budget.status_code == 200
    before = client.get("/api/v1/transactions").json()
    balances = client.get("/api/v1/accounts").json()
    first = client.post("/api/v1/budgets/savings-target", json={"savings_target": "4250.00"})
    assert first.status_code == 200, first.text
    assert first.json() == {"savings_target": "4250.00", "enabled": True}
    repeated = client.post("/api/v1/budgets/savings-target", json={"savings_target": "4250.00"})
    assert repeated.status_code == 200
    assert repeated.json() == first.json()
    current = client.get("/api/v1/budgets", params={"year": 2026, "month": 11}).json()
    assert current["savings_target"] == "4250.00"
    assert current["total_limit"] == "200.00"
    assert len(current["items"]) == 1
    assert client.get("/api/v1/transactions").json() == before
    assert client.get("/api/v1/accounts").json() == balances
    assert (
        client.post("/api/v1/budgets/savings-target", json={"savings_target": "-1"}).status_code
        == 422
    )


def test_rebase_archived_zero_evidence_budget_does_not_block_savings_goal(
    client: TestClient,
) -> None:
    assert client.post(
        "/api/v1/accounts", json={"name": "Legacy", "opening_balance": "100.00"}
    ).status_code == 201
    category = client.post(
        "/api/v1/categories", json={"name": "Unused category", "expense_class": "ESSENTIAL"}
    ).json()["id"]
    old_budget = client.post(
        "/api/v1/budgets/set",
        json={"year": 2026, "month": 11, "category_id": category, "limit_amount": "75.00"},
    )
    assert old_budget.status_code == 200
    rebase = client.post(
        "/api/v1/finance/rebase/commit",
        json={
            "batch_key": "regression-archived-budgets-and-goal",
            "as_of_date": "2026-11-12",
            "backup_confirmed": True,
            "expected_active_transactions": 0,
            "accounts": [{"name": "Checking", "opening_balance": "100.00"}],
            "transactions": [
                {
                    "source_id": "future-salary",
                    "transaction_date": "2026-11-27",
                    "description": "Expected salary",
                    "amount": "1200.00",
                    "type": "INCOME",
                    "status": "PENDING",
                    "account_name": "Checking",
                }
            ],
        },
    )
    assert rebase.status_code == 200, rebase.text
    before = client.get("/api/v1/transactions").json()
    balances = client.get("/api/v1/accounts").json()
    applied = client.post(
        "/api/v1/budgets/generate",
        json={"year": 2026, "month": 11, "savings_target": "4250.00", "preview": False},
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["items"] == []
    report = client.get("/api/v1/budgets", params={"year": 2026, "month": 11}).json()
    assert report["savings_target"] == "4250.00"
    assert report["items"] == []
    assert client.get("/api/v1/transactions").json() == before
    assert client.get("/api/v1/accounts").json() == balances
