"""Operator-invoked read-only acceptance check, never a deploy command.

Secrets come exclusively from environment; output contains no financial values.
This module is intentionally NOT a CI job and never runs automatically.
"""

import json
import os
import sys
from typing import Any, Mapping

from app.services.deploy_readonly_adapter import ReadOnlyHttpAdapter

_REQUIRED = (
    "PF_ACCEPT_REPO",
    "PF_ACCEPT_SERVICE_ID",
    "PF_ACCEPT_MCP_URL",
    "PF_ACCEPT_TRUSTED_MCP_ORIGIN",
    "PF_ACCEPT_RENDER_TOKEN",
    "PF_ACCEPT_MCP_TOKEN",
)


def acceptance_report(source: ReadOnlyHttpAdapter, *, expected_service: str) -> dict[str, Any]:
    """Only GET metadata and the three explicitly allowlisted MCP read operations."""
    service = source.render_service()
    main_sha = source.main_commit()
    deploy = source.latest_deployment()
    plugin = source.plugin_info()
    snapshot = source.financial_snapshot()
    comparison = source.compare_financial_audit(snapshot)
    sections = comparison.get("sections")
    expected = {
        "accounts",
        "account_balances",
        "transactions_active",
        "transactions_archived",
        "audit_history",
    }
    contract_ok = (
        service.get("id") == expected_service
        and service.get("autoDeploy") == "no"
        and service.get("autoDeployTrigger") == "off"
        and service.get("branch") == "main"
        and isinstance(main_sha, str)
        and len(main_sha) == 40
        and deploy.get("status") == "live"
        and plugin.get("read_only") is True
        and plugin.get("version") == "0.4.0"
        and snapshot.get("schema_version") == 2
        and snapshot.get("read_only") is True
        and snapshot.get("quality", {}).get("status") == "OK"
        and comparison.get("status") == "MATCH"
        and comparison.get("matches") is True
        and comparison.get("read_only") is True
        and comparison.get("before_hash") == snapshot.get("snapshot_hash")
        and comparison.get("after_hash") == snapshot.get("snapshot_hash")
        and isinstance(sections, list)
        and {item.get("section") for item in sections if isinstance(item, dict)} == expected
        and len(sections) == len(expected)
        and all(
            item.get("matches") is True and item.get("changed_buckets") == []
            for item in sections
            if isinstance(item, dict)
        )
    )
    return {
        "status": "PASS" if contract_ok else "BLOCKED",
        "read_only": True,
        "service_valid": service.get("id") == expected_service,
        "auto_deploy_off": (
            service.get("autoDeploy") == "no" and service.get("autoDeployTrigger") == "off"
        ),
        "main_matches_live": main_sha == deploy.get("commit", {}).get("id"),
        "mcp_version_ok": plugin.get("version") == "0.4.0",
        "financial_comparison_matches": comparison.get("status") == "MATCH",
        "scope": "adapter-acceptance-only-not-pre-post-deployment",
    }


def run_from_environment(environment: Mapping[str, str]) -> dict[str, Any]:
    if any(not environment.get(key) for key in _REQUIRED):
        return {"status": "BLOCKED", "reason": "MISSING_ACCEPTANCE_CONFIGURATION"}
    source = ReadOnlyHttpAdapter(
        repo=environment["PF_ACCEPT_REPO"],
        service_id=environment["PF_ACCEPT_SERVICE_ID"],
        mcp_url=environment["PF_ACCEPT_MCP_URL"],
        trusted_mcp_origin=environment["PF_ACCEPT_TRUSTED_MCP_ORIGIN"],
        render_token=environment["PF_ACCEPT_RENDER_TOKEN"],
        mcp_oauth_token=environment["PF_ACCEPT_MCP_TOKEN"],
        github_token=environment.get("PF_ACCEPT_GITHUB_TOKEN"),
    )
    return acceptance_report(source, expected_service=environment["PF_ACCEPT_SERVICE_ID"])


def main() -> int:
    try:
        report = run_from_environment(os.environ)
    except Exception:
        report = {"status": "BLOCKED", "reason": "ACCEPTANCE_READ_FAILURE"}
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
