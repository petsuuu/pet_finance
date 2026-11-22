"""GitHub-hosted release readiness checks: no deploy or production access."""
from typing import Any

import pytest

from app.services.github_release_gate import evaluate_environment


def protected() -> dict[str, Any]:
    return {
        "name": "production",
        "prevent_self_review": True,
        "protection_rules": [
            {"type": "required_reviewers", "reviewers": [{"type": "User", "id": 123}]}
        ],
    }


def test_protected_manual_main_is_policy_ready_only() -> None:
    result = evaluate_environment(
        protected(), ref="refs/heads/main", event="workflow_dispatch"
    )
    assert result == {"status": "PASS", "reason": "ENVIRONMENT_POLICY_VERIFIED_ONLY"}


@pytest.mark.parametrize(
    "change",
    [
        {"prevent_self_review": False},
        {"protection_rules": []},
        {"protection_rules": [{"type": "required_reviewers", "reviewers": []}]},
        {"name": "staging"},
    ],
)
def test_missing_protection_fails_closed(change: dict[str, Any]) -> None:
    data = protected()
    data.update(change)
    assert evaluate_environment(
        data, ref="refs/heads/main", event="workflow_dispatch"
    )["status"] == "BLOCKED"


@pytest.mark.parametrize(
    ("ref", "event"),
    [
        ("refs/heads/feature", "workflow_dispatch"),
        ("refs/heads/main", "push"),
        ("refs/pull/55/merge", "pull_request"),
    ],
)
def test_manual_main_only(ref: str, event: str) -> None:
    assert evaluate_environment(protected(), ref=ref, event=event)["status"] == "BLOCKED"
