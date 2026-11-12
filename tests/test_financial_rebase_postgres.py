"""Financial rebase: archived history, checkpoint cash and reproducible import."""

from fastapi.testclient import TestClient


def _body() -> dict[str, object]:
    return {
        "batch_key": "test-baseline-fictitious",
        "as_of_date": "2026-06-12",
        "backup_confirmed": True,
        "expected_active_transactions": 2,
        "accounts": [
            {"name": "Conta Principal", "opening_balance": "1000.00"},
            {"name": "Não Conta Principal", "opening_balance": "250.00"},
            {"name": "Dinheiro", "opening_balance": "75.00"},
            {
                "name": "Reserva Isolada",
                "opening_balance": "0",
                "include_in_summary": False,
            },
        ],
        "transactions": [
            {
                "source_id": "fake-row-a",
                "transaction_date": "2026-06-01",
                "type": "EXPENSE",
                "status": "POSTED",
                "description": "Compra exemplo",
                "amount": "30",
                "account_name": "Não Conta Principal",
                "category": "Categoria Exemplo",
                "subcategory": "Subcategoria Exemplo",
                "tags": ["teste"],
            },
            {
                "source_id": "fake-row-b",
                "transaction_date": "2026-06-01",
                "type": "EXPENSE",
                "status": "POSTED",
                "description": "Compra exemplo",
                "amount": "30",
                "account_name": "Não Conta Principal",
                "category": "Categoria Exemplo",
                "subcategory": "Subcategoria Exemplo",
                "tags": ["teste"],
            },
            {
                "source_id": "fake-row-c",
                "transaction_date": "2026-06-05",
                "type": "EXPENSE",
                "status": "POSTED",
                "description": "Despesa paga",
                "amount": "100",
                "account_name": "Conta Principal",
            },
            {
                "source_id": "fake-row-d",
                "transaction_date": "2026-06-17",
                "type": "EXPENSE",
                "status": "PENDING",
                "description": "Assinatura futura",
                "amount": "125.00",
                "account_name": "Conta Principal",
            },
            {
                "source_id": "fake-row-e",
                "transaction_date": "2026-06-27",
                "type": "INCOME",
                "status": "PENDING",
                "description": "Salário previsto",
                "amount": "2500.00",
                "account_name": "Conta Principal",
            },
        ],
    }


def test_rebase_is_atomic_idempotent_and_preserves_archives(client: TestClient) -> None:
    old = client.post(
        "/api/v1/accounts", json={"name": "Dinheiro", "opening_balance": "500"}
    ).json()
    assert (
        client.post(
            "/api/v1/transactions",
            json={
                "description": "Old paid purchase",
                "transaction_date": "2026-05-01",
                "amount": "50",
                "type": "EXPENSE",
                "status": "POSTED",
                "account_id": old["id"],
                "idempotency_key": "legacy-1",
            },
        ).status_code
        == 201
    )
    assert (
        client.post(
            "/api/v1/transactions",
            json={
                "description": "Old pending charge",
                "transaction_date": "2026-06-17",
                "amount": "20",
                "type": "EXPENSE",
                "status": "PENDING",
                "account_id": old["id"],
                "idempotency_key": "legacy-2",
            },
        ).status_code
        == 201
    )
    body = _body()
    preview = client.post("/api/v1/finance/rebase/preview", json=body)
    assert preview.status_code == 200
    assert preview.json()["safe_to_commit"] is True
    assert preview.json()["existing_live_transactions"] == 2
    assert len(client.get("/api/v1/transactions").json()) == 2

    result = client.post("/api/v1/finance/rebase/commit", json=body)
    assert result.status_code == 200, result.text
    assert result.json()["archived"]["transactions"] == 2
    assert result.json()["imported_transactions"] == 5
    assert result.json()["starting_summary_balance"] == "1325.00"

    accounts = client.get("/api/v1/accounts").json()
    assert len(accounts) == 4
    assert {a["name"] for a in accounts} == {
        "Conta Principal",
        "Não Conta Principal",
        "Dinheiro",
        "Reserva Isolada",
    }
    assert (
        next(a for a in accounts if a["name"] == "Reserva Isolada")["include_in_summary"] is False
    )

    active = client.get("/api/v1/transactions").json()
    archived = client.get("/api/v1/transactions?archive_mode=archived").json()
    assert len(active) == 5
    assert len(archived) == 2
    assert len([row for row in active if row["description"] == "Compra exemplo"]) == 2
    dashboard = client.get(
        "/api/v1/dashboard/monthly",
        params={"year": 2026, "month": 6, "as_of": "2026-06-12"},
    ).json()
    assert dashboard["current_balance"] == "1325.00"
    assert dashboard["expense_month"] == "0"
    assert dashboard["pending_commitments"] == "125.00"
    assert dashboard["expected_income"] == "2500.00"

    repeat = client.post("/api/v1/finance/rebase/commit", json=body)
    assert repeat.status_code == 200
    assert repeat.json() == result.json()
    assert len(client.get("/api/v1/transactions").json()) == 5

    savings = next(a for a in accounts if a["name"] == "Reserva Isolada")
    assert (
        client.post(
            "/api/v1/transactions",
            json={
                "description": "Savings activity",
                "transaction_date": "2026-06-12",
                "amount": "10",
                "type": "EXPENSE",
                "status": "POSTED",
                "account_id": savings["account_id"],
                "idempotency_key": "savings-excluded-1",
            },
        ).status_code
        == 201
    )
    again = client.get(
        "/api/v1/dashboard/monthly",
        params={"year": 2026, "month": 6, "as_of": "2026-06-12"},
    ).json()
    assert again["current_balance"] == "1325.00"
    assert again["expense_month"] == "0"

    everyday = next(a for a in accounts if a["name"] == "Conta Principal")
    response = client.post(
        "/api/v1/transactions",
        json={
            "description": "New expense after cash checkpoint",
            "transaction_date": "2026-06-12",
            "amount": "5",
            "type": "EXPENSE",
            "status": "POSTED",
            "account_id": everyday["account_id"],
            "idempotency_key": "today-after-rebase",
        },
    )
    assert response.status_code == 201
    after = client.get(
        "/api/v1/dashboard/monthly",
        params={"year": 2026, "month": 6, "as_of": "2026-06-12"},
    ).json()
    assert after["current_balance"] == "1320.00"
    assert after["expense_month"] == "5.00"


def test_rebase_rejects_bad_backups_and_mismatched_counts(client: TestClient) -> None:
    body = _body()
    body["expected_active_transactions"] = 9
    mismatch = client.post("/api/v1/finance/rebase/commit", json=body)
    assert mismatch.status_code == 409
    assert client.get("/api/v1/accounts").json() == []
    body["expected_active_transactions"] = 0
    body["backup_confirmed"] = False
    assert client.post("/api/v1/finance/rebase/commit", json=body).status_code == 422

    body["backup_confirmed"] = True
    movements = body["transactions"]
    assert isinstance(movements, list)
    movements[1]["source_id"] = movements[0]["source_id"]
    assert client.post("/api/v1/finance/rebase/preview", json=body).status_code == 422
