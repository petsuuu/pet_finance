"""Fail-closed solo-operator GitHub Actions readiness attestation.

This verifies who explicitly dispatched a read-only workflow and exactly which
main commit was confirmed. It does not establish independent review, perform
deployment, or grant production authorization.
"""

import os
import re
import subprocess
import sys


def evaluate_solo_attestation(
    *,
    repository: str,
    actor: str,
    event: str,
    ref: str,
    github_sha: str,
    checked_out_sha: str,
    expected_sha: str,
    confirmation: str,
) -> dict[str, str]:
    """Return PASS only for an exact, explicit, single-operator attestation."""
    if repository != "petsuuu/pet_finance" or actor != "petsuuu":
        return {"status": "BLOCKED", "reason": "UNAUTHORIZED_GITHUB_CONTEXT"}
    if event != "workflow_dispatch" or ref != "refs/heads/main":
        return {"status": "BLOCKED", "reason": "MANUAL_MAIN_ONLY"}
    if not re.fullmatch(r"[0-9a-f]{40}", expected_sha):
        return {"status": "BLOCKED", "reason": "INVALID_EXPECTED_SHA"}
    if github_sha != expected_sha or checked_out_sha != expected_sha:
        return {"status": "BLOCKED", "reason": "COMMIT_MISMATCH"}
    if confirmation != f"VERIFY READINESS {expected_sha}":
        return {"status": "BLOCKED", "reason": "CONFIRMATION_MISMATCH"}
    return {"status": "PASS", "reason": "SOLO_READINESS_ATTESTED_NOT_DEPLOY_APPROVED"}


def main() -> int:
    try:
        checked_out_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        print("BLOCKED: CHECKOUT_UNAVAILABLE")
        return 1
    result = evaluate_solo_attestation(
        repository=os.getenv("GITHUB_REPOSITORY", ""),
        actor=os.getenv("GITHUB_ACTOR", ""),
        event=os.getenv("GITHUB_EVENT_NAME", ""),
        ref=os.getenv("GITHUB_REF", ""),
        github_sha=os.getenv("GITHUB_SHA", ""),
        checked_out_sha=checked_out_sha,
        expected_sha=os.getenv("EXPECTED_SHA", ""),
        confirmation=os.getenv("CONFIRMATION", ""),
    )
    print(f"{result['status']}: {result['reason']}")
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
