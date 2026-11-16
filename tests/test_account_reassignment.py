"""Regression tests for auditable, zero-balance-impact envelope corrections."""

from typing import Any, cast

from fastapi.testclient import TestClient


def account(client: TestClient, name: str) -> str:
    response = client.post("/api/v1/accounts", json={"name": name})
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


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
    return str(response.json()["id"])


def get(client: TestClient, identity: str) -> dict[str, Any]:
    response = client.get(f"/api/v1/transactions/{identity}")
    assert response.status_code == 200
    return cast(dict[str, Any], response.json())


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


def test_patch_account_and_payment_replay_preserves_single_debit(client: TestClient) -> None:
    source = account(client, "Source")
    dest = account(client, "Destination")
    identity = expense(client, source)
    path = f"/api/v1/transactions/{identity}"
    payload = {"account_id": dest, "status": "POSTED"}
    first = client.patch(path, json=payload)
    assert first.status_code == 200, first.text
    balances = client.get("/api/v1/accounts").json()
    history = client.get("/api/v1/transactions/history", params={"identity": identity}).json()
    repeated = client.patch(path, json=payload)
    assert repeated.json() == first.json()
    assert client.get("/api/v1/accounts").json() == balances
    assert (
        client.get("/api/v1/transactions/history", params={"identity": identity}).json() == history
    )
    amounts = {row["account_id"]: row["current_balance"] for row in balances}
    assert amounts[source] == "0.00"
    assert amounts[dest] == "-38.45"
    assert len(client.get("/api/v1/transactions").json()) == 1


def test_posted_patch_moves_debit_without_creating_expense(client: TestClient) -> None:
    source = account(client, "Source")
    dest = account(client, "Destination")
    identity = expense(client, source, status="POSTED")
    response = client.patch(f"/api/v1/transactions/{identity}", json={"account_id": dest})
    assert response.status_code == 200, response.text
    amounts = {r["account_id"]: r["current_balance"] for r in client.get("/api/v1/accounts").json()}
    assert amounts[source] == "0.00"
    assert amounts[dest] == "-38.45"
    assert response.json()["id"] == identity


def test_invalid_account_rolls_back_payment(client: TestClient) -> None:
    source = account(client, "Source")
    identity = expense(client, source)
    before = get(client, identity)
    response = client.patch(
        f"/api/v1/transactions/{identity}",
        json={"account_id": "00000000-0000-0000-0000-000000000999", "status": "POSTED"},
    )
    assert response.status_code == 404
    assert get(client, identity) == before


def test_recurrence_account_patch_is_idempotent_and_preserves_occurrences(
    client: TestClient,
) -> None:
    source = account(client, "Source")
    dest = account(client, "Destination")
    rule = client.post(
        "/api/v1/recurrences",
        json={
            "description": "Meli+",
            "expected_amount": "9.90",
            "account_id": source,
            "due_day": 15,
            "start_date": "2026-11-01",
        },
    ).json()
    identity = expense(client, source, recurrence_id=rule["id"])
    path = f"/api/v1/recurrences/{rule['id']}"
    first = client.patch(path, json={"account_id": dest})
    assert first.status_code == 200, first.text
    assert first.json()["account_id"] == dest
    assert client.patch(path, json={"account_id": dest}).json() == first.json()
    assert get(client, identity)["account_id"] == source


def test_concurrent_account_payment_retries_debit_once(client: TestClient) -> None:
    from concurrent.futures import ThreadPoolExecutor

    source = account(client, "Source")
    dest = account(client, "Destination")
    identity = expense(client, source)

    def pay(_: int) -> Any:
        return client.patch(
            f"/api/v1/transactions/{identity}", json={"account_id": dest, "status": "POSTED"}
        ).status_code

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(pay, range(8))) == [200] * 8
    amounts = {r["account_id"]: r["current_balance"] for r in client.get("/api/v1/accounts").json()}
    assert amounts[source] == "0.00"
    assert amounts[dest] == "-38.45"
    assert len(client.get("/api/v1/transactions").json()) == 1
    history = client.get("/api/v1/transactions/history", params={"identity": identity}).json()
    assert len([r for r in history["items"] if r["action"] == "UPDATE"]) == 1


def test_account_change_cannot_redebit_historical_opening_balance(client: TestClient) -> None:
    source = client.post(
        "/api/v1/accounts",
        json={"name": "Source", "opening_balance": "100", "opening_balance_date": "2026-12-10"},
    ).json()["id"]
    dest = account(client, "Destination")
    identity = expense(client, source, status="POSTED")
    balances = client.get("/api/v1/accounts").json()
    response = client.patch(f"/api/v1/transactions/{identity}", json={"account_id": dest})
    assert response.status_code == 409
    assert client.get("/api/v1/accounts").json() == balances
