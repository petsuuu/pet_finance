from fastapi.testclient import TestClient


def test_embedded_category_comparison_is_readonly_and_owner_scoped(client: TestClient) -> None:
    account = client.post("/api/v1/accounts", json={"name": "Cash"}).json()["id"]
    category = client.post(
        "/api/v1/categories", json={"name": "Food", "expense_class": "ESSENTIAL"}
    ).json()["id"]
    for day, amount in [("2026-08-01", "10"), ("2026-09-03", "100"), ("2026-10-03", "50")]:
        assert (
            client.post(
                "/api/v1/transactions",
                json={
                    "account_id": account,
                    "category_id": category,
                    "description": "Meal " + day,
                    "amount": amount,
                    "transaction_date": day,
                    "status": "POSTED",
                    "type": "EXPENSE",
                    "idempotency_key": day,
                },
            ).status_code
            == 201
        )
    before = client.get("/api/v1/audit/transactions").json()
    r = client.get(
        "/api/v1/dashboard/monthly", params={"year": 2026, "month": 10, "as_of": "2026-10-06"}
    )
    assert r.status_code == 200
    comparison = r.json()["category_comparison"]
    assert comparison["items"][0]["matched_change_percent"] == "-50.00"
    assert comparison["items"][0]["months"][2]["paid_net"] == "50.00"
    assert client.get("/api/v1/audit/transactions").json() == before
