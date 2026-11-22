"""PR #53: fail-closed deploy approval tests, without external writes."""

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

import pytest

from app.services.audit_snapshots import SECTION_NAMES, _digest
from app.services.deploy_safety import check_postdeploy, check_predeploy

COMMIT = "a" * 40
SERVICE = {"id": "srv-test", "branch": "main", "autoDeploy": "no", "autoDeployTrigger": "off"}
PLUGIN = {"version": "0.4.0", "read_only": True}
EMPTY_HASH = _digest([])


def snapshot() -> dict[str, Any]:
    sections = {
        name: {
            "count": 0,
            "sha256": EMPTY_HASH,
            "buckets": {str(i): {"count": 0, "sha256": EMPTY_HASH} for i in range(16)},
        }
        for name in SECTION_NAMES
    }
    core: dict[str, Any] = {
        "schema_version": 2,
        "hash_algorithm": "sha256",
        "sections": sections,
        "balances": [],
        "quality": {"status": "OK", "total_findings": 0, "balance_mismatches": 0},
    }
    return {
        **core,
        "snapshot_hash": _digest(core),
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "read_only": True,
    }


def before_args() -> dict[str, Any]:
    return {
        "service": deepcopy(SERVICE),
        "expected_service_id": "srv-test",
        "expected_commit": COMMIT,
        "main_commit": COMMIT,
        "snapshot": snapshot(),
        "plugin_info": deepcopy(PLUGIN),
        "expected_plugin_version": "0.4.0",
    }


def after_args() -> dict[str, Any]:
    base = snapshot()
    return {
        "service": deepcopy(SERVICE),
        "expected_service_id": "srv-test",
        "expected_commit": COMMIT,
        "expected_plugin_version": "0.4.0",
        "deployment": {"status": "live", "commit": {"id": COMMIT}},
        "plugin_info": deepcopy(PLUGIN),
        "baseline": base,
        "comparison": {
            "status": "MATCH",
            "matches": True,
            "requires_user_review": False,
            "read_only": True,
            "before_hash": base["snapshot_hash"],
            "after_hash": base["snapshot_hash"],
            "sections": [
                {
                    "section": name,
                    "matches": True,
                    "changed_buckets": [],
                    "before_count": 0,
                    "after_count": 0,
                    "before_sha256": EMPTY_HASH,
                    "after_sha256": EMPTY_HASH,
                }
                for name in SECTION_NAMES
            ],
            "changed_account_balances": [],
            "quality_before": {"status": "OK", "total_findings": 0},
            "quality_after": {"status": "OK", "total_findings": 0},
        },
    }


def test_predeploy_pass_still_requires_human_authorization() -> None:
    result = check_predeploy(**before_args())
    assert result["status"] == "PASS"
    assert result["manual_deploy_authorization_required"] is True
    assert result["read_only"] is True


@pytest.mark.parametrize(
    "field,mutation,reason",
    [
        ("service", {"autoDeploy": "yes"}, "AUTO_DEPLOY_NOT_OFF"),
        ("service", {"autoDeployTrigger": "on"}, "AUTO_DEPLOY_NOT_OFF"),
        ("service", {"id": "wrong"}, "SERVICE_ID_MISMATCH"),
        ("service", {"branch": "dev"}, "UNEXPECTED_BRANCH"),
        ("plugin_info", {"version": "0.3.0"}, "PLUGIN_VERSION_MISMATCH"),
    ],
)
def test_predeploy_fails_closed(field: str, mutation: dict[str, Any], reason: str) -> None:
    args = before_args()
    args[field].update(mutation)
    assert reason in check_predeploy(**args)["issues"]


def test_predeploy_rejects_missing_evidence_and_tampering() -> None:
    args = before_args()
    args["main_commit"] = "b" * 40
    args["snapshot"]["snapshot_hash"] = "0" * 64
    result = check_predeploy(**args)
    assert {"COMMIT_MISMATCH", "INVALID_BASELINE"} <= set(result["issues"])
    assert result["approved"] is False


def test_postdeploy_full_match() -> None:
    result = check_postdeploy(**after_args())
    assert result["status"] == "PASS"
    assert result["manual_deploy_authorization_required"] is True


@pytest.mark.parametrize(
    "mutation,reason",
    [
        (lambda a: a["service"].update({"autoDeploy": "yes"}), "AUTO_DEPLOY_NOT_OFF"),
        (lambda a: a["deployment"].update({"status": "building"}), "LIVE_COMMIT_MISMATCH"),
        (lambda a: a["deployment"]["commit"].update({"id": "b" * 40}), "LIVE_COMMIT_MISMATCH"),
        (lambda a: a["plugin_info"].update({"version": "0.3.0"}), "PLUGIN_VERSION_MISMATCH"),
        (
            lambda a: a["comparison"].update({"status": "REVIEW_REQUIRED", "matches": False}),
            "FINANCIAL_COMPARISON_NOT_MATCH",
        ),
        (
            lambda a: a["comparison"].update({"after_hash": "b" * 64}),
            "FINANCIAL_COMPARISON_NOT_MATCH",
        ),
        (
            lambda a: a["comparison"]["sections"][0].update({"changed_buckets": ["2"]}),
            "FINANCIAL_COMPARISON_NOT_MATCH",
        ),
        (
            lambda a: a["comparison"]["sections"][1].update({"after_count": 1}),
            "FINANCIAL_COMPARISON_NOT_MATCH",
        ),
        (
            lambda a: a["comparison"].update({"changed_account_balances": [{"account_id": "x"}]}),
            "FINANCIAL_COMPARISON_NOT_MATCH",
        ),
        (
            lambda a: a["comparison"]["quality_after"].update({"status": "REVIEW"}),
            "FINANCIAL_COMPARISON_NOT_MATCH",
        ),
    ],
)
def test_postdeploy_blocks_any_mismatch(mutation: Any, reason: str) -> None:
    args = after_args()
    mutation(args)
    result = check_postdeploy(**args)
    assert result["approved"] is False
    assert reason in result["issues"]


def test_rejects_missing_baseline_and_missing_comparison_sections() -> None:
    args = after_args()
    args["baseline"] = {}
    args["comparison"]["sections"] = []
    result = check_postdeploy(**args)
    assert "INVALID_BASELINE" in result["issues"]
    assert "FINANCIAL_COMPARISON_NOT_MATCH" in result["issues"]


def test_input_evidence_not_mutated() -> None:
    args = after_args()
    original = deepcopy(args)
    check_postdeploy(**args)
    assert args == original
