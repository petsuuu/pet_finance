from decimal import Decimal

import pytest
from fastapi.testclient import TestClient


@pytest.mark.parametrize("observed", ["80.00", "120.00", "-20.00"])
def test_signed_reconciliation(client: TestClient, observed: str) -> None:
    account = client.post(
        "/api/v1/accounts", json={"name": "Test", "opening_balance": "100"}
    ).json()["id"]
    body = {"account_id": account, "observed_balance": observed, "reason": "Bank statement"}
    response = client.post("/api/v1/reconciliation/preview", json=body)
    assert response.status_code == 200, response.text
    preview = response.json()
    assert Decimal(str(preview["difference"])) == Decimal(observed) - Decimal("100")
    assert Decimal(str(client.get("/api/v1/accounts").json()[0]["current_balance"])) == 100
    request = {
        **body,
        "expected_balance": "100",
        "preview_token": preview["preview_token"],
        "idempotency_key": "reconcile-test",
    }
    first = client.post("/api/v1/reconciliation/commit", json=request)
    assert first.status_code == 201, first.text
    replay = client.post("/api/v1/reconciliation/commit", json=request)
    assert replay.status_code == 201
    assert replay.json()["id"] == first.json()["id"]
    stale = client.post(
        "/api/v1/reconciliation/commit",
        json={**request, "idempotency_key": "another-key"},
    )
    assert stale.status_code == 409
    rows = client.get("/api/v1/transactions", params={"type": "ADJUSTMENT"}).json()
    assert len(rows) == 1
    assert Decimal(str(rows[0]["amount"])) == Decimal(observed) - Decimal("100")
    accounts = client.get("/api/v1/accounts").json()
    assert Decimal(str(accounts[0]["current_balance"])) == Decimal(observed)
    audit = client.get("/api/v1/audit/transactions").json()
    assert any(row["action"] == "RECONCILE" for row in audit)
