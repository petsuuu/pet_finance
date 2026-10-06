from fastapi.testclient import TestClient


def test_radar_embedded_without_financial_mutations(client: TestClient) -> None:
    account = client.post("/api/v1/accounts", json={"name": "Bank"}).json()["id"]
    category = client.post(
        "/api/v1/categories", json={"name": "Streaming e Mídia", "expense_class": "SUPERFLUOUS"}
    ).json()["id"]
    assert (
        client.post(
            "/api/v1/recurrences",
            json={
                "account_id": account,
                "category_id": category,
                "description": "Prime Video",
                "expected_amount": "120",
                "frequency": "YEARLY",
                "month_of_year": 7,
                "due_day": 9,
                "start_date": "2026-01-01",
            },
        ).status_code
        == 201
    )
    before = client.get("/api/v1/audit/transactions").json()
    r = client.get(
        "/api/v1/dashboard/monthly", params={"year": 2026, "month": 10, "as_of": "2026-10-06"}
    )
    assert r.status_code == 200
    radar = r.json()["recurring_radar"]
    assert radar["recurring_costs"][0]["annual_run_rate"] == "120.00"
    assert radar["recurring_costs"][0]["review_candidate"] is True
    assert client.get("/api/v1/audit/transactions").json() == before
    assert client.get("/api/v1/transactions").json() == []
