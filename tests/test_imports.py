from uuid import uuid4

from fastapi.testclient import TestClient


def snapshot() -> dict[str, object]:
    account, category, transaction = str(uuid4()), str(uuid4()), str(uuid4())
    return {
        "accounts": [
            {
                "id": account,
                "name": "Import wallet",
                "currencyCode": "BRL",
                "balances": {"opening": 0},
            }
        ],
        "categories": [{"id": category, "name": "Imported food", "subtype": "ESSENTIAL_EXPENSE"}],
        "transactions": [
            {
                "id": transaction,
                "intent": "EXPENSE",
                "amount": 12.34,
                "status": "POSTED",
                "date": "2026-10-04",
                "currencyCode": "BRL",
                "description": "Fictional expense",
                "account": {"id": account},
                "category": {"id": category},
            }
        ],
    }


def test_import_preview_commit_reimport_and_reconciliation(client: TestClient) -> None:
    source = snapshot()
    assert client.post("/api/v1/imports/clofin/preview", json=source).json()["count"] == 1
    assert client.get("/api/v1/transactions").json() == []
    response = client.post("/api/v1/imports/clofin/commit", json=source)
    assert response.status_code == 200
    assert response.json()["imported"] == 1
    batch = response.json()["batch_id"]
    report = client.get(f"/api/v1/imports/{batch}/reconciliation").json()
    assert report["reconciled"] is True
    assert report["different_rows"] == 0
    assert report["source_totals"] == report["destination_totals"]
    assert client.post("/api/v1/imports/clofin/commit", json=source).json()["skipped"] == 1
    assert len(client.get("/api/v1/transactions").json()) == 1
    invalid = {**source, "transactions": [{"id": "bad"}]}
    assert client.post("/api/v1/imports/clofin/preview", json=invalid).status_code == 422
