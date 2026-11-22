"""Unit tests for solo-operator readiness; never an independent approval."""

import pytest

from app.services.github_release_gate import evaluate_solo_attestation

SHA = "a" * 40


def valid() -> dict[str, str]:
    return {
        "repository": "petsuuu/pet_finance",
        "actor": "petsuuu",
        "event": "workflow_dispatch",
        "ref": "refs/heads/main",
        "github_sha": SHA,
        "checked_out_sha": SHA,
        "expected_sha": SHA,
        "confirmation": f"VERIFY READINESS {SHA}",
    }


def test_explicit_solo_readiness_is_not_deploy_approval() -> None:
    assert evaluate_solo_attestation(**valid()) == {
        "status": "PASS",
        "reason": "SOLO_READINESS_ATTESTED_NOT_DEPLOY_APPROVED",
    }


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("repository", "someone/else"),
        ("actor", "other"),
        ("event", "push"),
        ("ref", "refs/heads/feature"),
        ("github_sha", "b" * 40),
        ("checked_out_sha", "b" * 40),
        ("expected_sha", "invalid"),
        ("expected_sha", "b" * 40),
        ("confirmation", "VERIFY READINESS"),
        ("confirmation", "DEPLOY NOW"),
    ],
)
def test_mismatch_fails_closed(key: str, value: str) -> None:
    options = valid()
    options[key] = value
    assert evaluate_solo_attestation(**options)["status"] == "BLOCKED"
