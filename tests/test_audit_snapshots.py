"""Audit v2: consistent snapshots, deterministic SHA-256 and read-only comparison."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

import app.services.audit_snapshots as snapshots
from app.core.config import Settings
from app.db.database import build_engine


def add_account(client: TestClient, name: str) -> str:
    response = client.post("/api/v1/accounts", json={"name": name, "opening_balance": "100.00"})
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def add_expense(client: TestClient, account_id: str, *, status: str, key: str, amount: str) -> str:
    response = client.post(
        "/api/v1/transactions",
        json={
            "description": "Despesa para auditoria",
            "type": "EXPENSE",
            "status": status,
            "amount": amount,
            "account_id": account_id,
            "transaction_date": "2026-11-22",
            "idempotency_key": key,
        },
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def test_snapshot_is_deterministic_complete_and_never_mutates(client: TestClient) -> None:
    identity = add_account(client, "Carteira")
    add_expense(client, identity, status="POSTED", key="audit-51-posted", amount="11.00")
    archived = add_expense(
        client, identity, status="PENDING", key="audit-51-archived", amount="19.00"
    )
    engine = build_engine(Settings().database_url)
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE transactions SET archived_at = now() WHERE id = CAST(:id AS uuid)"),
            {"id": archived},
        )
    engine.dispose()

    before_accounts = client.get("/api/v1/accounts").json()
    before_transactions = client.get("/api/v1/transactions").json()
    before_history = client.get("/api/v1/audit/transactions").json()

    response = client.get("/api/v1/audit/financial-snapshot")
    assert response.status_code == 200, response.text
    before = response.json()
    again = client.get("/api/v1/audit/financial-snapshot").json()
    assert before["read_only"] is True
    assert before["schema_version"] == 2
    assert before["hash_algorithm"] == "sha256"
    assert len(before["snapshot_hash"]) == 64
    assert again["snapshot_hash"] == before["snapshot_hash"]
    assert before["sections"] == again["sections"]
    assert before["sections"]["accounts"]["count"] == 1
    assert before["sections"]["account_balances"]["count"] == 1
    assert before["sections"]["transactions_active"]["count"] == 1
    assert before["sections"]["transactions_archived"]["count"] == 1
    assert before["sections"]["audit_history"]["count"] >= 3
    for section in before["sections"].values():
        assert len(section["buckets"]) == 16
        assert sum(item["count"] for item in section["buckets"].values()) == section["count"]
        assert all(len(item["sha256"]) == 64 for item in section["buckets"].values())
    assert before["balances"][0]["current_balance"] == "89.00"
    assert before["quality"]["status"] in {"OK", "REVIEW"}
    assert before["quality"]["balance_mismatches"] == 0

    comparison = client.post("/api/v1/audit/financial-snapshot/compare", json={"baseline": before})
    assert comparison.status_code == 200, comparison.text
    result = comparison.json()
    assert result["status"] == "MATCH"
    assert result["matches"] is True
    assert result["requires_user_review"] is False
    assert result["changed_account_balances"] == []
    assert all(section["matches"] for section in result["sections"])
    assert all(not section["changed_buckets"] for section in result["sections"])

    assert client.get("/api/v1/accounts").json() == before_accounts
    assert client.get("/api/v1/transactions").json() == before_transactions
    assert client.get("/api/v1/audit/transactions").json() == before_history


def test_compare_detects_payment_changes_and_blocks_modified_baseline(
    client: TestClient,
) -> None:
    identity = add_account(client, "Essenciais")
    movement = add_expense(
        client, identity, status="PENDING", key="audit-51-changing", amount="25.00"
    )
    before = client.get("/api/v1/audit/financial-snapshot").json()

    tampered = {**before, "snapshot_hash": "0" * 64}
    invalid = client.post("/api/v1/audit/financial-snapshot/compare", json={"baseline": tampered})
    assert invalid.status_code == 422
    assert (
        client.post(
            "/api/v1/audit/financial-snapshot/compare",
            json={"baseline": {"schema_version": 999}},
        ).status_code
        == 422
    )

    changed = client.patch(f"/api/v1/transactions/{movement}", json={"status": "POSTED"})
    assert changed.status_code == 200, changed.text

    result = client.post("/api/v1/audit/financial-snapshot/compare", json={"baseline": before})
    assert result.status_code == 200, result.text
    payload = result.json()
    assert payload["status"] == "REVIEW_REQUIRED"
    assert payload["matches"] is False
    assert payload["requires_user_review"] is True
    differences = {row["section"] for row in payload["sections"] if not row["matches"]}
    assert {"account_balances", "transactions_active", "audit_history"} <= differences
    assert "transactions_archived" not in differences
    for section in payload["sections"]:
        if section["section"] in differences:
            assert section["changed_buckets"]
        else:
            assert section["changed_buckets"] == []
    assert len(payload["changed_account_balances"]) == 1
    item = payload["changed_account_balances"][0]
    assert item["before"]["current_balance"] == "100.00"
    assert item["after"]["current_balance"] == "75.00"


def test_read_only_mvcc_snapshot_remains_coherent_during_concurrent_update(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    identity = add_account(client, "Original")
    original = snapshots._read_rows
    injected = False

    def read_with_concurrent_change(
        service: Any, name: str, *, archived: bool | None = None
    ) -> list[dict[str, Any]]:
        nonlocal injected
        rows = original(service, name, archived=archived)
        if name == "accounts" and not injected:
            injected = True
            engine = build_engine(Settings().database_url)
            with engine.begin() as connection:
                connection.execute(
                    text("UPDATE accounts SET name = 'Alterada' WHERE id = CAST(:id AS uuid)"),
                    {"id": identity},
                )
            engine.dispose()
        return rows

    monkeypatch.setattr(snapshots, "_read_rows", read_with_concurrent_change)
    old = client.get("/api/v1/audit/financial-snapshot")
    assert old.status_code == 200, old.text
    first = old.json()
    # A READ COMMITTED set of queries would show the updated account name here.
    assert first["balances"][0]["name"] == "Original"
    assert first["sections"]["accounts"]["count"] == 1
    assert injected is True

    new = client.get("/api/v1/audit/financial-snapshot")
    assert new.status_code == 200
    assert new.json()["balances"][0]["name"] == "Alterada"
    assert new.json()["snapshot_hash"] != first["snapshot_hash"]


def test_snapshot_endpoints_require_authentication(client: TestClient) -> None:
    assert (
        client.get(
            "/api/v1/audit/financial-snapshot",
            headers={"Authorization": "Bearer invalid"},
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/api/v1/audit/financial-snapshot/compare",
            json={"baseline": {}},
            headers={"Authorization": "Bearer invalid"},
        ).status_code
        == 401
    )


def test_buckets_are_stable_for_row_order_and_reveal_targeted_drift() -> None:
    left = [
        {"id": "acaa", "amount": "10.00"},
        {"id": "bcbb", "amount": "20.00"},
        {"id": "ccdd", "amount": "30.00"},
    ]
    reversed_rows = list(reversed(left))
    # Snapshot reader sorts by stable PK before calling the manifest.
    first = snapshots._bucket_manifest(sorted(left, key=lambda row: row["id"]), key="id")
    second = snapshots._bucket_manifest(sorted(reversed_rows, key=lambda row: row["id"]), key="id")
    assert first == second
    changed = [row.copy() for row in left]
    changed[1]["amount"] = "21.00"
    third = snapshots._bucket_manifest(sorted(changed, key=lambda row: row["id"]), key="id")
    altered = [key for key in first if first[key] != third[key]]
    assert len(altered) == 1
    assert sum(value["count"] for value in first.values()) == 3


def test_compare_rejects_invalid_bucket_counts_and_digest(
    client: TestClient,
) -> None:
    add_account(client, "Auditoria")
    baseline = client.get("/api/v1/audit/financial-snapshot").json()
    bad = __import__("copy").deepcopy(baseline)
    bad["sections"]["accounts"]["buckets"]["0"]["count"] += 1
    invalid = client.post("/api/v1/audit/financial-snapshot/compare", json={"baseline": bad})
    assert invalid.status_code == 422

    bad_hash = __import__("copy").deepcopy(baseline)
    bad_hash["sections"]["accounts"]["buckets"]["0"]["sha256"] = "bad"
    invalid_hash = client.post(
        "/api/v1/audit/financial-snapshot/compare", json={"baseline": bad_hash}
    )
    assert invalid_hash.status_code == 422


def test_bucket_manifest_includes_historic_and_excluded_account_rows(
    client: TestClient,
) -> None:
    included = add_account(client, "Incluida")
    excluded = add_account(client, "Excluida")
    # The snapshot operates on all user accounts, including those not
    # displayed in the main financial summary.
    patch = client.patch(f"/api/v1/accounts/{excluded}", json={"include_in_summary": False})
    assert patch.status_code == 200, patch.text
    add_expense(client, included, status="PENDING", key="audit-52-a", amount="11")
    add_expense(client, excluded, status="PENDING", key="audit-52-b", amount="12")
    snapshot = client.get("/api/v1/audit/financial-snapshot")
    assert snapshot.status_code == 200, snapshot.text
    payload = snapshot.json()
    assert payload["sections"]["accounts"]["count"] == 2
    assert payload["sections"]["account_balances"]["count"] == 2
    assert payload["sections"]["transactions_active"]["count"] == 2
    assert len(payload["balances"]) == 2
    assert any(not row["include_in_summary"] for row in payload["balances"])
