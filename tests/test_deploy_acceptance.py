"""Isolated contract tests for the opt-in acceptance harness."""

from copy import deepcopy
from typing import Any

from app.services.deploy_acceptance import acceptance_report, run_from_environment


class FakeReadOnlyAdapter:
    def __init__(self) -> None:
        self.service = {
            "id": "srv-test",
            "branch": "main",
            "autoDeploy": "no",
            "autoDeployTrigger": "off",
        }
        self.plugin = {"read_only": True, "version": "0.4.0"}
        self.status = "MATCH"
        self.calls: list[str] = []

    def render_service(self) -> dict[str, Any]:
        self.calls.append("render_service")
        return deepcopy(self.service)

    def main_commit(self) -> str:
        self.calls.append("main_commit")
        return "a" * 40

    def latest_deployment(self) -> dict[str, Any]:
        self.calls.append("latest_deployment")
        return {"status": "live", "commit": {"id": "a" * 40}}

    def plugin_info(self) -> dict[str, Any]:
        self.calls.append("plugin_info")
        return deepcopy(self.plugin)

    def financial_snapshot(self) -> dict[str, Any]:
        self.calls.append("financial_snapshot")
        return {
            "schema_version": 2,
            "read_only": True,
            "snapshot_hash": "b" * 64,
            "quality": {"status": "OK"},
        }

    def compare_financial_audit(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        self.calls.append("compare_financial_audit")
        return {
            "status": self.status,
            "matches": self.status == "MATCH",
            "read_only": True,
            "before_hash": snapshot["snapshot_hash"],
            "after_hash": snapshot["snapshot_hash"],
            "sections": [
                {"section": name, "matches": True, "changed_buckets": []}
                for name in (
                    "accounts",
                    "account_balances",
                    "transactions_active",
                    "transactions_archived",
                    "audit_history",
                )
            ],
        }


def test_acceptance_passes_only_read_only_contract() -> None:
    source = FakeReadOnlyAdapter()
    result = acceptance_report(source, expected_service="srv-test")  # type: ignore[arg-type]
    assert result["status"] == "PASS"
    assert result["read_only"] is True
    assert "snapshot_hash" not in str(result)
    assert source.calls == [
        "render_service",
        "main_commit",
        "latest_deployment",
        "plugin_info",
        "financial_snapshot",
        "compare_financial_audit",
    ]


def test_acceptance_blocks_autodeploy_and_drift() -> None:
    source = FakeReadOnlyAdapter()
    source.service["autoDeploy"] = "yes"
    assert acceptance_report(source, expected_service="srv-test")["status"] == "BLOCKED"  # type: ignore[arg-type]
    source.service["autoDeploy"] = "no"
    source.status = "REVIEW_REQUIRED"
    assert acceptance_report(source, expected_service="srv-test")["status"] == "BLOCKED"  # type: ignore[arg-type]


def test_acceptance_missing_environment_never_calls_network() -> None:
    assert run_from_environment({}) == {
        "status": "BLOCKED",
        "reason": "MISSING_ACCEPTANCE_CONFIGURATION",
    }
