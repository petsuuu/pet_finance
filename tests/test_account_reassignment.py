"""Regression tests for auditable, zero-balance-impact envelope corrections."""

from fastapi.testclient import TestClient


def account(client: TestClient, name: str) -> str:
    response = client.post("/api/v1/accounts", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def expense(
    client: TestClient,
    account_id: str,
    *,
    description: str = "Claro TV",
    due: str = "2026-12-06",
    key: str = "claro-2026-12",
    recurrence_id: str | None = None,
    status: str = "PENDING",
) -> str:
    payload = {
        "type": "EXPENSE",
        "status": status,
        "description": description,
        "amount": "38.45",
        "transaction_date": due,
        "account_id": account_id,
        "idempotency_key": key,
    }
    if recurrence_id:
        payload["recurrence_id"] = recurrence_id
    response = client.post("/api/v1/transactions", json=payload)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def get(client: TestClient, identity: str) -> dict:
    response = client.get(f"/api/v1/transactions/{identity}")
    assert response.status_code == 200
    return response.json()


def test_one_off_preview_and_apply_does_not_post_or_change_balances(client: TestClient) -> None:
    source = account(client, "Não Essenciais")
    dest = account(client, "Essenciais")
    identity = expense(client, source)
    before = client.get("/api/v1/accounts").json()
    path = f"/api/v1/transactions/{identity}/reassign-account"
    preview = client.post(path, json={"target_account_id": dest}).json()
    assert preview["preview"] is True
    assert preview["affected_count"] == 1
    assert get(client, identity)["account_id"] == source
    applied = client.post(path, json={"target_account_id": dest, "preview": False})
    assert applied.status_code == 200, applied.text
    assert applied.json()["balances_changed"] is False
    after = get(client, identity)
    assert after["account_id"] == dest
    assert after["status"] == "PENDING"
    assert after["amount"] == "38.45"
    assert before == client.get("/api/v1/accounts").json()
    repeated = client.post(path, json={"target_account_id": dest, "preview": False})
    assert repeated.json()["affected_count"] == 0


def test_recurring_reassignment_updates_future_pending_only(client: TestClient) -> None:
    source = account(client, "Não Essenciais")
    dest = account(client, "Essenciais")
    rule_response = client.post(
        "/api/v1/recurrences",
        json={
            "description": "Meli+ — recorrência",
            "expected_amount": "9.90",
            "account_id": source,
            "due_day": 16,
            "start_date": "2026-11-01",
        },
    )
    assert rule_response.status_code == 201, rule_response.text
    rule_id = rule_response.json()["id"]
    old = expense(
        client, source, description="Meli+", due="2026-11-16", key="meli-nov", recurrence_id=rule_id
    )
    dec = expense(
        client, source, description="Meli+", due="2026-12-16", key="meli-dec", recurrence_id=rule_id
    )
    jan = expense(
        client, source, description="Meli+", due="2027-01-16", key="meli-jan", recurrence_id=rule_id
    )
    path = f"/api/v1/transactions/{dec}/reassign-account"
    assert client.post(path, json={"target_account_id": dest}).status_code == 422
    preview = client.post(
        path,
        json={
            "target_account_id": dest,
            "apply_to_recurrence": True,
        },
    ).json()
    assert preview["affected_count"] == 2
    assert get(client, dec)["account_id"] == source
    changed = client.post(
        path,
        json={
            "target_account_id": dest,
            "apply_to_recurrence": True,
            "preview": False,
        },
    )
    assert changed.status_code == 200, changed.text
    assert get(client, old)["account_id"] == source
    assert get(client, dec)["account_id"] == dest
    assert get(client, jan)["account_id"] == dest
    rules = client.get("/api/v1/recurrences").json()
    assert next(x for x in rules if x["id"] == rule_id)["account_id"] == dest
    assert all(get(client, x)["status"] == "PENDING" for x in (old, dec, jan))


def test_posted_expense_cannot_move(client: TestClient) -> None:
    source = account(client, "Não Essenciais")
    dest = account(client, "Essenciais")
    paid = expense(client, source, status="POSTED")
    response = client.post(
        f"/api/v1/transactions/{paid}/reassign-account",
        json={"target_account_id": dest, "preview": False},
    )
    assert response.status_code == 409
    assert get(client, paid)["account_id"] == source


def test_invalid_target_does_not_change_source(client: TestClient) -> None:
    source = account(client, "Não Essenciais")
    identity = expense(client, source)
    response = client.post(
        f"/api/v1/transactions/{identity}/reassign-account",
        json={"target_account_id": "00000000-0000-0000-0000-000000000999", "preview": False},
    )
    assert response.status_code == 404
    assert get(client, identity)["account_id"] == source
