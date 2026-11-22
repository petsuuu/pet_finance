"""Consistent, read-only, SHA-256 financial snapshots for deployment audits.

The caller retains the trusted before snapshot. The compare endpoint re-reads
live data, never stores or modifies a financial record, and flags *any* drift.
"""

import hashlib
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import select, text

from app.services.data_quality import data_quality_dashboard
from app.services.ledger import Ledger

SCHEMA_VERSION = 1
SECTION_NAMES = (
    "accounts",
    "account_balances",
    "transactions_active",
    "transactions_archived",
    "audit_history",
)


def _normalize(value: Any) -> Any:
    """Explicit type-stable JSON representation for deterministic comparisons."""
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, Enum):
        return _normalize(value.value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _normalize(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    return value


def _digest(value: Any) -> str:
    payload = json.dumps(_normalize(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _read_rows(service: Ledger, name: str, *, archived: bool | None = None) -> list[dict[str, Any]]:
    table = service.table(name)
    query = select(table).where(table.c.user_id == service.user_id)
    if archived is not None:
        query = query.where(
            table.c.archived_at.is_not(None) if archived else table.c.archived_at.is_(None)
        )
    rows = [
        _normalize(dict(row))
        for row in service.connection.execute(query).mappings()
    ]
    primary_key = "account_id" if name == "account_balances" else "id"
    rows.sort(key=lambda row: str(row[primary_key]))
    return rows


def financial_snapshot(service: Ledger) -> dict[str, Any]:
    """A single PostgreSQL MVCC snapshot for balances, transactions and audit logs.

    This must be the first SQL statement in the request-scoped Ledger transaction.
    A repeatable-read read-only transaction refuses writes and prevents
    cross-query snapshot skew from concurrent financial activity.
    """
    service.connection.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
    sections: dict[str, dict[str, Any]] = {}
    data = {
        "accounts": _read_rows(service, "accounts"),
        "account_balances": _read_rows(service, "account_balances"),
        "transactions_active": _read_rows(service, "transactions", archived=False),
        "transactions_archived": _read_rows(service, "transactions", archived=True),
        "audit_history": _read_rows(service, "audit_log"),
    }
    for section in SECTION_NAMES:
        rows = data[section]
        sections[section] = {"count": len(rows), "sha256": _digest(rows)}
    balances = [
        {
            "account_id": row["account_id"],
            "name": row["name"],
            "current_balance": row["current_balance"],
            "active": row["active"],
            "include_in_summary": row["include_in_summary"],
        }
        for row in data["account_balances"]
    ]
    quality = data_quality_dashboard(service, limit=1)
    summary = {
        "schema_version": SCHEMA_VERSION,
        "hash_algorithm": "sha256",
        "sections": sections,
        "balances": balances,
        "quality": {
            "status": quality["status"],
            "total_findings": quality["summary"]["total_findings"],
            "balance_mismatches": quality["summary"]["balance_mismatches"],
        },
    }
    return {
        **summary,
        "snapshot_hash": _digest(summary),
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "read_only": True,
        "basis": (
            "Repeatable-read, read-only PostgreSQL transaction. Full rows are "
            "hashed locally, including inactive accounts, excluded accounts, "
            "archived transactions and full audit history. Hashes are not proof "
            "of legitimate changes or independent bank settlement."
        ),
    }


def _validate_baseline(baseline: dict[str, Any]) -> None:
    if baseline.get("schema_version") != SCHEMA_VERSION or baseline.get("hash_algorithm") != "sha256":
        raise HTTPException(422, "Unsupported audit snapshot schema or hash algorithm")
    sections = baseline.get("sections")
    if not isinstance(sections, dict) or set(sections) != set(SECTION_NAMES):
        raise HTTPException(422, "Invalid audit snapshot sections")
    for value in sections.values():
        if (
            not isinstance(value, dict)
            or not isinstance(value.get("count"), int)
            or value["count"] < 0
            or not isinstance(value.get("sha256"), str)
            or len(value["sha256"]) != 64
        ):
            raise HTTPException(422, "Invalid audit section digest")
    core = {
        "schema_version": baseline["schema_version"],
        "hash_algorithm": baseline["hash_algorithm"],
        "sections": sections,
        "balances": baseline.get("balances"),
        "quality": baseline.get("quality"),
    }
    if not isinstance(core["balances"], list) or not isinstance(core["quality"], dict):
        raise HTTPException(422, "Invalid baseline balances or quality")
    if _digest(core) != baseline.get("snapshot_hash"):
        raise HTTPException(422, "Baseline fingerprint does not match provided contents")


def compare_financial_snapshots(baseline: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    """Compare a caller-retained baseline with one fresh, consistent snapshot."""
    _validate_baseline(baseline)
    section_results = [
        {
            "section": name,
            "before_count": baseline["sections"][name]["count"],
            "after_count": current["sections"][name]["count"],
            "before_sha256": baseline["sections"][name]["sha256"],
            "after_sha256": current["sections"][name]["sha256"],
            "matches": baseline["sections"][name] == current["sections"][name],
        }
        for name in SECTION_NAMES
    ]
    previous = {row["account_id"]: row for row in baseline["balances"]}
    latest = {row["account_id"]: row for row in current["balances"]}
    changes = [
        {"account_id": account_id, "before": previous.get(account_id), "after": latest.get(account_id)}
        for account_id in sorted(set(previous) | set(latest))
        if previous.get(account_id) != latest.get(account_id)
    ]
    matches = (
        baseline["snapshot_hash"] == current["snapshot_hash"]
        and all(section["matches"] for section in section_results)
    )
    return {
        "status": "MATCH" if matches else "REVIEW_REQUIRED",
        "matches": matches,
        "read_only": True,
        "before_hash": baseline["snapshot_hash"],
        "after_hash": current["snapshot_hash"],
        "before_captured_at": baseline.get("captured_at"),
        "after_captured_at": current["captured_at"],
        "sections": section_results,
        "changed_account_balances": changes,
        "quality_before": baseline["quality"],
        "quality_after": current["quality"],
        "current_snapshot": current,
        "requires_user_review": not matches,
        "basis": (
            "Any record change requires review, even if it was legitimate activity "
            "during deploy. A caller-provided baseline must be retained from the "
            "authorized pre-deploy snapshot; no financial records are modified."
        ),
    }


def audit_snapshot_router(dependency: Any) -> APIRouter:
    router = APIRouter(prefix="/api/v1")
    Service = Annotated[Ledger, Depends(dependency)]

    @router.get("/audit/financial-snapshot")
    def snapshot(service: Service) -> dict[str, Any]:
        return financial_snapshot(service)

    @router.post("/audit/financial-snapshot/compare")
    def compare(
        service: Service, baseline: Annotated[dict[str, Any], Body(embed=True)]
    ) -> dict[str, Any]:
        _validate_baseline(baseline)
        return compare_financial_snapshots(baseline, financial_snapshot(service))

    return router
