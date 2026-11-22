"""Fail-closed, read-only gates for an explicitly authorized manual deployment.

This module evaluates evidence supplied by the operator. It NEVER calls Render,
GitHub or financial write endpoints; external verification and an authenticated,
tamper-resistant baseline archive are operator responsibilities.
"""

from datetime import datetime
from typing import Any

from app.services.audit_snapshots import _validate_baseline

REQUIRED_SECTIONS = frozenset(
    {
        "accounts",
        "account_balances",
        "transactions_active",
        "transactions_archived",
        "audit_history",
    }
)


def _require(condition: bool, message: str, findings: list[str]) -> None:
    if not condition:
        findings.append(message)


def _time(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.tzinfo is not None and parsed.utcoffset() is not None
    except ValueError:
        return False


def _autodeploy_off(service: dict[str, Any]) -> bool:
    return service.get("autoDeploy") == "no" and service.get("autoDeployTrigger") == "off"


def _valid_baseline(snapshot: Any) -> bool:
    if not isinstance(snapshot, dict):
        return False
    try:
        _validate_baseline(snapshot)
    except Exception:
        return False
    return snapshot.get("read_only") is True and _time(snapshot.get("captured_at"))


def _result(issues: list[str], phase: str) -> dict[str, Any]:
    return {
        "phase": phase,
        "status": "PASS" if not issues else "BLOCKED",
        "approved": not issues,
        "issues": issues,
        "read_only": True,
        "manual_deploy_authorization_required": True,
    }


def check_predeploy(
    *,
    service: dict[str, Any],
    expected_service_id: str,
    expected_commit: str,
    main_commit: str,
    snapshot: dict[str, Any],
    plugin_info: dict[str, Any],
    expected_plugin_version: str,
) -> dict[str, Any]:
    """Review the immediately captured baseline before a manual deploy.

    PASS only permits asking for separate human deploy authorization.
    It does not authorize or perform deployment.
    """
    issues: list[str] = []
    _require(
        bool(expected_service_id) and service.get("id") == expected_service_id,
        "SERVICE_ID_MISMATCH",
        issues,
    )
    _require(_autodeploy_off(service), "AUTO_DEPLOY_NOT_OFF", issues)
    _require(service.get("branch") == "main", "UNEXPECTED_BRANCH", issues)
    _require(
        len(expected_commit) == 40
        and all(c in "0123456789abcdef" for c in expected_commit.lower())
        and main_commit == expected_commit,
        "COMMIT_MISMATCH",
        issues,
    )
    _require(_valid_baseline(snapshot), "INVALID_BASELINE", issues)
    _require(
        plugin_info.get("version") == expected_plugin_version
        and plugin_info.get("read_only") is True,
        "PLUGIN_VERSION_MISMATCH",
        issues,
    )
    _require(
        snapshot.get("quality", {}).get("status") == "OK"
        and snapshot.get("quality", {}).get("total_findings") == 0,
        "BASELINE_QUALITY_NOT_OK",
        issues,
    )
    return _result(issues, "predeploy")


def check_postdeploy(
    *,
    service: dict[str, Any],
    expected_service_id: str,
    expected_commit: str,
    expected_plugin_version: str,
    deployment: dict[str, Any],
    plugin_info: dict[str, Any],
    baseline: dict[str, Any],
    comparison: dict[str, Any],
) -> dict[str, Any]:
    """Validate a trusted baseline against read-only after-deploy evidence."""
    issues: list[str] = []
    _require(
        bool(expected_service_id) and service.get("id") == expected_service_id,
        "SERVICE_ID_MISMATCH",
        issues,
    )
    _require(_autodeploy_off(service), "AUTO_DEPLOY_NOT_OFF", issues)
    _require(service.get("branch") == "main", "UNEXPECTED_BRANCH", issues)
    _require(
        len(expected_commit) == 40
        and all(c in "0123456789abcdef" for c in expected_commit.lower())
        and deployment.get("status") == "live"
        and deployment.get("commit", {}).get("id") == expected_commit,
        "LIVE_COMMIT_MISMATCH",
        issues,
    )
    _require(
        plugin_info.get("version") == expected_plugin_version
        and plugin_info.get("read_only") is True,
        "PLUGIN_VERSION_MISMATCH",
        issues,
    )
    _require(_valid_baseline(baseline), "INVALID_BASELINE", issues)
    expected_hash = baseline.get("snapshot_hash")
    sections = comparison.get("sections")
    _require(
        comparison.get("status") == "MATCH"
        and comparison.get("matches") is True
        and comparison.get("requires_user_review") is False
        and comparison.get("read_only") is True
        and comparison.get("before_hash") == expected_hash
        and comparison.get("after_hash") == expected_hash
        and isinstance(sections, list)
        and {s.get("section") for s in sections if isinstance(s, dict)} == REQUIRED_SECTIONS
        and len(sections) == len(REQUIRED_SECTIONS)
        and all(
            s.get("matches") is True
            and s.get("changed_buckets") == []
            and s.get("before_count") == s.get("after_count")
            and s.get("before_sha256") == s.get("after_sha256")
            for s in sections
            if isinstance(s, dict)
        )
        and comparison.get("changed_account_balances") == []
        and comparison.get("quality_before", {}).get("status") == "OK"
        and comparison.get("quality_after", {}).get("status") == "OK"
        and comparison.get("quality_after", {}).get("total_findings") == 0,
        "FINANCIAL_COMPARISON_NOT_MATCH",
        issues,
    )
    return _result(issues, "postdeploy")
