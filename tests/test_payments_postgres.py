from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.services.ledger import Ledger


def obligation(client: TestClient, **changes: Any) -> str:
    account = client.post("/api/v1/accounts", json={"name": "Payment wallet"}).json()["id"]
    body = {
        "type": "EXPENSE",
        "status": "PENDING",
        "description": "Fictional allowance",
        "amount": "50",
        "transaction_date": "2026-10-27",
        "account_id": account,
        "idempotency_key": "fixture",
        **changes,
    }
    return str(client.post("/api/v1/transactions", json=body).json()["id"])


def test_atomic_partial_retry_and_final_payment(client: TestClient) -> None:
    identity = obligation(client)
    body = {"amount": "44", "payment_date": "2026-10-07", "idempotency_key": "partial"}
    url = f"/api/v1/transactions/{identity}/settle"
    response = client.post(url, json=body)
    assert response.status_code == 200
    data = response.json()
    assert data["remaining"] == "6.00"
    assert data["pending"]["transaction_date"] == "2026-10-27"
    assert data["payment"]["status"] == "POSTED"
    assert client.post(url, json=body).json() == data
    assert len(client.get("/api/v1/transactions").json()) == 2
    assert client.get("/api/v1/accounts").json()[0]["current_balance"] == "-44.00"
    assert client.post(url, json={**body, "amount": "40"}).status_code == 409
    final = client.post(url, json={**body, "amount": "6", "idempotency_key": "final"})
    assert final.json()["status"] == "SETTLED"
    assert final.json()["pending"] is None
    assert client.get("/api/v1/accounts").json()[0]["current_balance"] == "-50.00"


def test_failed_payment_rolls_back_both_parts(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    identity = obligation(client)
    original = Ledger.create

    def fail(self: Ledger, name: str, data: dict[str, Any]) -> dict[str, Any]:
        if name == "payment_operations":
            raise RuntimeError("Simulated failure after both writes")
        return original(self, name, data)

    monkeypatch.setattr(Ledger, "create", fail)
    with pytest.raises(RuntimeError, match="Simulated failure"):
        client.post(
            f"/api/v1/transactions/{identity}/settle",
            json={"amount": "44", "payment_date": "2026-10-07", "idempotency_key": "fail"},
        )
    rows = client.get("/api/v1/transactions").json()
    assert len(rows) == 1
    assert rows[0]["status"] == "PENDING"
    assert rows[0]["amount"] == "50.00"
    assert client.get("/api/v1/accounts").json()[0]["current_balance"] == "0.00"


@pytest.mark.parametrize("amount,day", [("51", "2026-10-07"), ("1", "2099-01-01")])
def test_invalid_payment_does_not_mutate(client: TestClient, amount: str, day: str) -> None:
    identity = obligation(client)
    assert (
        client.post(
            f"/api/v1/transactions/{identity}/settle",
            json={"amount": amount, "payment_date": day, "idempotency_key": "invalid"},
        ).status_code
        == 422
    )
    assert len(client.get("/api/v1/transactions").json()) == 1


def test_history_has_before_after_pagination_and_owner_boundary(client: TestClient) -> None:
    identity = obligation(client)
    client.patch(f"/api/v1/transactions/{identity}", json={"amount": "48"})
    first = client.get(
        "/api/v1/transactions/history", params={"identity": identity, "limit": 1}
    ).json()
    assert first["items"][0]["before_data"]["amount"] == "50.00"
    assert first["items"][0]["after_data"]["amount"] == "48.00"
    next_page = client.get(
        "/api/v1/transactions/history", params={"identity": identity, "limit": 1, "offset": 1}
    ).json()
    assert next_page["items"][0]["action"] == "CREATE"
    assert (
        client.get(
            "/api/v1/transactions/history",
            params={"identity": "11111111-1111-1111-1111-111111111111"},
        ).status_code
        == 404
    )
    assert (
        client.get(
            "/api/v1/transactions/history", params={"since": "2026-10-07", "until": "2026-10-06"}
        ).status_code
        == 422
    )


def test_merchant_learning_is_persisted_without_recategorizing_history(client: TestClient) -> None:
    category = client.post("/api/v1/categories", json={"name": "Fictional meals"}).json()["id"]
    identity = obligation(client, category_id=category)
    before = client.get(f"/api/v1/transactions/{identity}").json()
    body = {"name": "Fictional Restaurant", "aliases": ["CARD FICTIONAL REST"]}
    url = f"/api/v1/merchants/learn/{identity}"
    first = client.post(url, json=body)
    assert first.status_code == 200
    assert client.post(url, json=body).json() == first.json()
    match = client.get(
        "/api/v1/merchants/resolve", params={"raw_name": "card fictional rest"}
    ).json()
    assert match["suggested_category_id"] == category
    assert client.get(f"/api/v1/transactions/{identity}").json() == before


def test_concurrent_payment_retry_has_one_paid_fragment(client: TestClient) -> None:
    from concurrent.futures import ThreadPoolExecutor

    identity = obligation(client)
    url = f"/api/v1/transactions/{identity}/settle"
    body = {"amount": "10", "payment_date": "2026-10-07", "idempotency_key": "simultaneous"}
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: client.post(url, json=body), range(2)))
    assert all(r.status_code == 200 for r in responses)
    assert responses[0].json() == responses[1].json()
    assert len(client.get("/api/v1/transactions").json()) == 2
    assert client.get("/api/v1/accounts").json()[0]["current_balance"] == "-10.00"


def test_partial_installment_dashboard_preserves_total_and_ending_radar(client: TestClient) -> None:
    identity = obligation(
        client,
        description="Fictional course — parcela 3/3",
        notes="PAR-FICTIONAL-COURSE; parcela 3/3; confirmado.",
    )
    client.post(
        f"/api/v1/transactions/{identity}/settle",
        json={"amount": "44", "payment_date": "2026-10-07", "idempotency_key": "radar"},
    )
    d = client.get(
        "/api/v1/dashboard/monthly", params={"year": 2026, "month": 10, "as_of": "2026-10-07"}
    ).json()
    group = d["partial_payments"][0]
    assert (group["total"], group["paid"], group["remaining"]) == ("50.00", "44.00", "6.00")
    assert d["recurring_radar"]["installment_evidence_to_review"] == []
    ending = d["recurring_radar"]["installments_ending_next_six_months"][0]
    assert ending["potential_monthly_release"] == "50.00"
    assert ending["payoff_confirmed"] is False
