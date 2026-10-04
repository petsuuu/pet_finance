from fastapi.testclient import TestClient


def test_dashboard_distinguishes_balance_result_and_commitments(client: TestClient) -> None:
    account = client.post(
        "/api/v1/accounts", json={"name": "Dashboard", "opening_balance": "1000"}
    ).json()["id"]
    category = client.post(
        "/api/v1/categories", json={"name": "Food", "expense_class": "ESSENTIAL"}
    ).json()["id"]

    def transaction(
        amount: str, kind: str, status: str, key: str, day: str = "2026-10-04", **extra: object
    ) -> None:
        response = client.post(
            "/api/v1/transactions",
            json={
                "description": key,
                "amount": amount,
                "type": kind,
                "status": status,
                "transaction_date": day,
                "account_id": account,
                "category_id": category,
                "idempotency_key": key,
                **extra,
            },
        )
        assert response.status_code == 201

    transaction("100", "INCOME", "POSTED", "salary")
    transaction("50", "EXPENSE", "POSTED", "food")
    transaction("20", "EXPENSE", "PENDING", "bill", "2026-10-20")
    transaction("500", "EXPENSE", "PENDING", "next-month", "2026-11-01")
    rule = client.post(
        "/api/v1/recurrences",
        json={
            "description": "Subscription",
            "account_id": account,
            "expected_amount": "30",
            "due_day": 22,
            "start_date": "2026-10-01",
        },
    ).json()["id"]
    params = {"year": 2026, "month": 10, "as_of": "2026-10-04", "safety_margin": "100"}
    data = client.get("/api/v1/dashboard/monthly", params=params).json()
    assert data["current_balance"] == "1050.00"
    assert data["month_result"] == "50.00"
    assert data["pending_commitments"] == "50.00"
    assert data["free_after_commitments"] == "900.00"
    assert data["forecast_closing_balance"] == "1000.00"
    assert data["effective_savings"] is None
    assert data["essential_expense"] == "50.00"
    transaction("30", "EXPENSE", "PENDING", "subscription", "2026-10-22", recurrence_id=rule)
    data = client.get("/api/v1/dashboard/monthly", params=params).json()
    assert data["pending_commitments"] == "50.00"
    assert data["unrecorded_recurring_expenses"] == "0"
    assert data["top_categories"][0]["name"] == "Food"
    assert (
        client.get("/api/v1/dashboard/monthly", params={**params, "month": 13}).status_code == 422
    )
    assert (
        client.get(
            "/api/v1/dashboard/monthly", params={**params, "safety_margin": "-1"}
        ).status_code
        == 422
    )
    assert (
        client.get(
            "/api/v1/dashboard/monthly", params={**params, "as_of": "2026-11-01"}
        ).status_code
        == 422
    )
