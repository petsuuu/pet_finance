"""Isolated CI approval checks. Ephemeral keys never authorize production."""
import base64
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

from app.services.deploy_approval import SignedApprovalVerifier, canonical_claims

NOW = datetime(2026, 11, 22, 20, 0, tzinfo=timezone.utc)


def test_ephemeral_authority_accepts_only_matching_claims() -> None:
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    claims = {
        "format": "pet-finance-deploy-approval-v1",
        "authorization_id": "approval-ci-0001",
        "execution_id": "execution-ci-0001",
        "service_id": "srv-ci-example",
        "commit": "a" * 40,
        "plugin_version": "0.4.0",
        "issued_at": NOW.isoformat(),
        "expires_at": (NOW + timedelta(minutes=30)).isoformat(),
        "approved": True,
    }
    receipt: dict[str, Any] = {
        "claims": claims,
        "signature": base64.b64encode(key.sign(canonical_claims(claims))).decode(),
    }
    verifier = SignedApprovalVerifier(public)
    args = {
        "service_id": "srv-ci-example",
        "commit": "a" * 40,
        "plugin_version": "0.4.0",
        "authorization_id": "approval-ci-0001",
        "execution_id": "execution-ci-0001",
        "at": NOW,
    }
    assert verifier.verify(receipt, **args)
    for field, value in (
        ("commit", "b" * 40),
        ("service_id", "srv-another"),
        ("approved", False),
        ("plugin_version", "0.3.0"),
        ("authorization_id", "approval-ci-0002"),
    ):
        altered = deepcopy(receipt)
        altered["claims"][field] = value
        assert not verifier.verify(altered, **args)
    assert not verifier.verify(receipt, **{**args, "at": NOW + timedelta(minutes=31)})
    assert not verifier.verify(receipt, **{**args, "execution_id": "execution-ci-0002"})
    wrong_key = Ed25519PrivateKey.generate()
    forged = deepcopy(receipt)
    forged["signature"] = base64.b64encode(
        wrong_key.sign(canonical_claims(claims))
    ).decode()
    assert not verifier.verify(forged, **args)
    assert not verifier.verify({"claims": claims, "signature": "invalid"}, **args)
