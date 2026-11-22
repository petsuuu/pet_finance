"""Read-only integrity audit regressions; uses disposable PostgreSQL fixture."""

from typing import Any

from fastapi.testclient import TestClient


def create_account(client: TestClient, name: str, opening: str = "0") -> str:
    response = client.post(
        "/api/v1/accounts",
        json={"name": name, "opening_balance": opening},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def expense(
    client: TestClient,
    account_id: str,
    key: str,
    *,
    description: str = "Coffee",
    status: str = "POSTED",
    recurrence_id: str | None = None,
    force: bool = False,
) -> str:
    body: dict[str, Any] = {
        "type": "EXPENSE",
        "status": status,
        "account_id": account_id,
        "idempotency_key": key,
        "amount": "10.00",
        "transaction_date": "2026-11-22",
        "description": description,
        "force": force,
    }
    if recurrence_id:
        body["recurrence_id"] = recurrence_id
    response = client.post("/api/v1/transactions", json=body)
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def test_integrity_audit_reconciles_posted_and_ignores_pending(client: TestClient) -> None:
    account_id = create_account(client, "Carteira", "100.00")
    expense(client, account_id, "paid-one")
    expense(client, account_id, "pending-one", description="Pending", status="PENDING")
    response = client.get("/api/v1/audit/integrity")
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["read_only"] is True
    assert report["summary"]["accounts_checked"] == 1
    assert report["summary"]["balance_mismatches"] == 0
    assert report["balances"][0]["recorded"] == "90.00"
    assert report["balances"][0]["calculated"] == "90.00"
    assert report["summary"]["potential_duplicate_groups"] == 0


def test_duplicate_audit_flags_candidates_but_never_mutates(client: TestClient) -> None:
    account_id = create_account(client, "Carteira")
    first = expense(client, account_id, "first")
    second = expense(client, account_id, "second", force=True)
    before = client.get("/api/v1/transactions").json()
    history_before = client.get("/api/v1/audit/transactions").json()
    report = client.get("/api/v1/audit/integrity").json()
    assert report["summary"]["potential_duplicate_groups"] == 1
    assert set(report["potential_duplicates"][0]["transaction_ids"]) == {first, second}
    assert report["potential_duplicates"][0]["requires_review"] is True
    assert client.get("/api/v1/transactions").json() == before
    assert client.get("/api/v1/audit/transactions").json() == history_before


def test_recurrence_account_mismatch_is_reported_without_changes(
    client: TestClient,
) -> None:
    source = create_account(client, "Origem")
    destination = create_account(client, "Destino")
    rule_response = client.post(
        "/api/v1/recurrences",
        json={
            "description": "Monthly service",
            "expected_amount": "10.00",
            "account_id": source,
            "due_day": 22,
            "start_date": "2026-11-01",
        },
    )
    assert rule_response.status_code == 201, rule_response.text
    rule_id = str(rule_response.json()["id"])
    transaction_id = expense(
        client, source, "recurring-expense", recurrence_id=rule_id, status="PENDING"
    )
    patch = client.patch(f"/api/v1/recurrences/{rule_id}", json={"account_id": destination})
    assert patch.status_code == 200, patch.text
    report = client.get("/api/v1/audit/integrity").json()
    assert report["summary"]["recurrence_account_mismatches"] == 1
    mismatch = report["recurrence_account_mismatches"][0]
    assert mismatch["transaction_id"] == transaction_id
    assert mismatch["transaction_account_id"] == source
    assert mismatch["rule_account_id"] == destination
    assert client.get(f"/api/v1/transactions/{transaction_id}").json()["account_id"] == source


def test_integrity_audit_limit_and_no_data_leak(client: TestClient) -> None:
    response = client.get("/api/v1/audit/integrity", params={"limit": 0})
    assert response.status_code == 422
    assert client.get("/api/v1/audit/integrity").json()["balances"] == []
    assert client.get("/api/v1/audit/integrity").json()["potential_duplicates"] == []


def test_inactive_legacy_accounts_are_excluded_from_operational_audit(
    client: TestClient,
) -> None:
    legacy = create_account(client, "Legacy", "100.00")
    current = create_account(client, "Current", "50.00")
    old = expense(client, legacy, "legacy-paid")
    deactivated = client.patch(f"/api/v1/accounts/{legacy}", json={"active": False})
    assert deactivated.status_code == 200, deactivated.text
    report = client.get("/api/v1/audit/integrity").json()
    assert report["summary"]["accounts_checked"] == 1
    assert [row["account_id"] for row in report["balances"]] == [current]
    assert report["summary"]["balance_mismatches"] == 0
    assert client.get(f"/api/v1/transactions/{old}").status_code == 200


def test_paid_recurrence_history_is_not_flagged_after_rule_change(
    client: TestClient,
) -> None:
    source = create_account(client, "Former")
    destination = create_account(client, "Future")
    response = client.post(
        "/api/v1/recurrences",
        json={
            "description": "Subscription",
            "expected_amount": "10.00",
            "account_id": source,
            "due_day": 22,
            "start_date": "2026-11-01",
        },
    )
    assert response.status_code == 201, response.text
    rule_id = str(response.json()["id"])
    paid = expense(client, source, "paid-subscription", recurrence_id=rule_id)
    changed = client.patch(f"/api/v1/recurrences/{rule_id}", json={"account_id": destination})
    assert changed.status_code == 200, changed.text
    report = client.get("/api/v1/audit/integrity").json()
    assert report["summary"]["recurrence_account_mismatches"] == 0
    assert client.get(f"/api/v1/transactions/{paid}").json()["account_id"] == source
