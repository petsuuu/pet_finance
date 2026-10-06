from fastapi.testclient import TestClient


def test_import_installment_preview_retry_preserves_paid_date_and_amount(
    client: TestClient,
) -> None:
    account = client.post("/api/v1/accounts", json={"name": "Imported wallet"}).json()["id"]
    ids = []
    for n, amount, day, status in [
        (2, "108.75", "2026-09-05", "POSTED"),
        (3, "108.74", "2026-10-06", "PENDING"),
    ]:
        row = client.post(
            "/api/v1/transactions",
            json={
                "description": f"Monitor {n}/3",
                "type": "EXPENSE",
                "status": status,
                "amount": amount,
                "account_id": account,
                "transaction_date": day,
                "idempotency_key": f"import-monitor-{n}",
            },
        ).json()
        ids.append(row["id"])
    body = {
        "description": "Monitor",
        "installment_amount": "108.75",
        "total_installments": 3,
        "first_installment_date": "2026-08-06",
        "first_tracked_number": 2,
        "account_id": account,
        "links": [{"transaction_id": ids[0], "number": 2}, {"transaction_id": ids[1], "number": 3}],
    }
    assert client.post("/api/v1/installments/setup", json=body).json()["preview"] is True
    assert client.get("/api/v1/installments").json() == []
    applied = client.post("/api/v1/installments/setup", json={**body, "preview": False})
    assert applied.status_code == 200
    identity = applied.json()["plan"]["id"]
    assert (
        client.post("/api/v1/installments/setup", json={**body, "preview": False}).json()["plan"][
            "id"
        ]
        == identity
    )
    assert client.post(f"/api/v1/installments/{identity}/generate").json()["created_count"] == 0
    rows = client.get("/api/v1/transactions").json()
    assert len(rows) == 2
    paid = client.get(f"/api/v1/transactions/{ids[0]}").json()
    assert paid["transaction_date"] == "2026-09-05" and paid["status"] == "POSTED"
    assert client.get(f"/api/v1/transactions/{ids[1]}").json()["amount"] == "108.74"
    assert client.get("/api/v1/installments").json()[0]["total_amount"] is None
    bad = {**body, "links": [body["links"][0], body["links"][0]], "preview": False}
    assert client.post("/api/v1/installments/setup", json=bad).status_code == 422


def test_cashflow_rest_preserves_ledger(client: TestClient) -> None:
    client.post("/api/v1/accounts", json={"name": "Cashflow wallet", "opening_balance": "100"})
    result = client.get("/api/v1/dashboard/cashflow", params={"as_of": "2026-10-06"})
    assert result.status_code == 200
    assert result.json()["forecast_closing_balance"] == "100.00"
    assert len(result.json()["days"]) == 26
    assert client.get("/api/v1/transactions").json() == []


def test_agenda_rest_read_only_and_date_validation(client: TestClient) -> None:
    account = client.post("/api/v1/accounts", json={"name": "Agenda wallet"}).json()["id"]
    client.post(
        "/api/v1/recurrences",
        json={
            "description": "Agenda subscription",
            "account_id": account,
            "expected_amount": "20",
            "due_day": 9,
            "start_date": "2026-10-01",
        },
    )
    params = {"year": 2026, "month": 10, "as_of": "2026-10-05"}
    result = client.get("/api/v1/dashboard/agenda", params=params)
    assert result.status_code == 200
    assert result.json()["summary"]["pending"]["amount"] == "20.00"
    assert result.json()["missing_forecasts"][0]["recorded"] is False
    assert client.get("/api/v1/transactions").json() == []
    assert (
        client.get(
            "/api/v1/dashboard/agenda",
            params={
                **params,
                "as_of": "2026-11-01",
            },
        ).status_code
        == 422
    )


def test_cancel_rest_preserves_paid_history_and_blocks_generation(client: TestClient) -> None:
    account = client.post("/api/v1/accounts", json={"name": "Test wallet"}).json()["id"]
    rule = client.post(
        "/api/v1/recurrences",
        json={
            "description": "Subscription",
            "account_id": account,
            "expected_amount": "50",
            "due_day": 9,
            "start_date": "2026-09-01",
            "end_date": "2028-12-31",
        },
    ).json()["id"]
    generated = client.post(
        f"/api/v1/recurrences/{rule}/generate",
        json={
            "start_date": "2026-09-01",
            "end_date": "2026-12-31",
        },
    ).json()["transactions"]
    september = next(t for t in generated if t["transaction_date"] == "2026-09-09")
    october = next(t for t in generated if t["transaction_date"] == "2026-10-09")
    client.patch(f"/api/v1/transactions/{october['id']}", json={"status": "POSTED"})
    url = f"/api/v1/recurrences/{rule}/cancel"
    body = {"effective_date": "2026-10-01"}
    assert client.post(url, json=body).json()["affected_count"] == 2
    applied = client.post(url, json={**body, "preview": False})
    assert applied.status_code == 200
    assert applied.json()["cancelled_count"] == 2
    assert client.get(f"/api/v1/transactions/{october['id']}").json()["status"] == "POSTED"
    assert client.get(f"/api/v1/transactions/{september['id']}").json()["status"] == "PENDING"
    assert client.post(url, json={**body, "preview": False}).json()["cancelled_count"] == 0
    regenerated = client.post(
        f"/api/v1/recurrences/{rule}/generate",
        json={
            "start_date": "2026-10-01",
            "end_date": "2026-12-31",
        },
    )
    assert regenerated.json()["created_count"] == 0


def test_merchant_rest_rollback_and_default_category(client: TestClient) -> None:
    account = client.post("/api/v1/accounts", json={"name": "Test wallet"}).json()["id"]
    category = client.post("/api/v1/categories", json={"name": "Food"}).json()["id"]
    body = {"name": "Sample Cafe", "aliases": ["SAMPLE*CAFE LTDA"], "default_category_id": category}
    merchant = client.post("/api/v1/merchants/setup", json=body).json()["merchant"]["id"]
    assert client.post("/api/v1/merchants/setup", json=body).json()["merchant"]["id"] == merchant
    collision = client.post(
        "/api/v1/merchants/setup",
        json={
            "name": "Another shop",
            "aliases": ["SAMPLE*CAFE LTDA"],
        },
    )
    assert collision.status_code == 409
    assert len(client.get("/api/v1/merchants").json()) == 1
    resolved = client.get("/api/v1/merchants/resolve", params={"raw_name": "sample cafe ltda"})
    assert resolved.json()["suggested_category_id"] == category
    result = client.post(
        "/api/v1/transactions",
        json={
            "account_id": account,
            "merchant_id": merchant,
            "type": "EXPENSE",
            "status": "POSTED",
            "description": "Lunch",
            "amount": "33.25",
            "transaction_date": "2026-10-05",
            "idempotency_key": "merchant-lunch",
        },
    )
    assert result.status_code == 201
    assert result.json()["category_id"] == category
    client.patch(f"/api/v1/merchants/{merchant}", json={"default_category_id": None})
    assert (
        client.get(f"/api/v1/transactions/{result.json()['id']}").json()["category_id"] == category
    )
