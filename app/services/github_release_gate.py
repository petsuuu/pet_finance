"""Fail-closed GitHub Environment approval gate for manual release readiness.

This module never deploys or accesses financial accounts. A successful check only
attests to GitHub environment policy, not to human approval or a safe release.
"""
import json
import os
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def evaluate_environment(
    environment: dict[str, Any],
    *,
    ref: str,
    event: str,
    expected_environment: str = "production",
) -> dict[str, str]:
    """Reject an unprotected or incorrectly scoped release environment."""
    rules = environment.get("protection_rules")
    reviewers = []
    if isinstance(rules, list):
        for rule in rules:
            if isinstance(rule, dict) and rule.get("type") == "required_reviewers":
                reviewers = rule.get("reviewers", [])
                break
    approved_policy = (
        environment.get("name") == expected_environment
        and isinstance(reviewers, list)
        and bool(reviewers)
        and environment.get("prevent_self_review") is True
    )
    if not approved_policy:
        return {"status": "BLOCKED", "reason": "ENVIRONMENT_APPROVAL_NOT_ENFORCED"}
    if event != "workflow_dispatch" or ref != "refs/heads/main":
        return {"status": "BLOCKED", "reason": "MANUAL_MAIN_ONLY"}
    return {"status": "PASS", "reason": "ENVIRONMENT_POLICY_VERIFIED_ONLY"}


def main() -> int:
    token = os.getenv("GITHUB_TOKEN", "")
    repository = os.getenv("GITHUB_REPOSITORY", "")
    if not token or repository != "petsuuu/pet_finance":
        print("BLOCKED: MISSING_TRUSTED_GITHUB_CONTEXT")
        return 1
    url = f"https://api.github.com/repos/{repository}/environments/production"
    request = Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        method="GET",
    )
    try:
        with urlopen(request, timeout=15) as response:
            data = json.load(response)
    except (HTTPError, URLError, ValueError, TimeoutError, OSError):
        print("BLOCKED: ENVIRONMENT_POLICY_UNAVAILABLE")
        return 1
    if not isinstance(data, dict):
        print("BLOCKED: INVALID_ENVIRONMENT_RESPONSE")
        return 1
    result = evaluate_environment(
        data,
        ref=os.getenv("GITHUB_REF", ""),
        event=os.getenv("GITHUB_EVENT_NAME", ""),
    )
    print(f'{result["status"]}: {result["reason"]}')
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
