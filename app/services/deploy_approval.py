"""Verify operator authorization receipts signed by an independent authority.

The authorization private key must NOT be available to this application.
The external authority signs canonical JSON claims; only its trusted public key is used.
"""

import base64
import binascii
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

RECEIPT_FORMAT = "pet-finance-deploy-approval-v1"
MAX_APPROVAL_DURATION = timedelta(hours=2)
CLAIM_KEYS = frozenset(
    {
        "format",
        "authorization_id",
        "execution_id",
        "service_id",
        "commit",
        "plugin_version",
        "issued_at",
        "expires_at",
        "approved",
    }
)


def canonical_claims(claims: dict[str, Any]) -> bytes:
    """Stable serialization for detached Ed25519 signature verification."""
    return json.dumps(claims, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


class SignedApprovalVerifier:
    """Read-only proof validation. Signature cannot be forged without issuer's key."""

    def __init__(self, trusted_public_key: bytes) -> None:
        self._key = Ed25519PublicKey.from_public_bytes(trusted_public_key)

    def verify(
        self,
        receipt: dict[str, Any],
        *,
        service_id: str,
        commit: str,
        plugin_version: str,
        authorization_id: str,
        execution_id: str,
        at: datetime,
    ) -> bool:
        try:
            if not isinstance(receipt, dict) or set(receipt) != {"claims", "signature"}:
                return False
            claims = receipt["claims"]
            if not isinstance(claims, dict) or set(claims) != CLAIM_KEYS:
                return False
            if (
                claims["format"] != RECEIPT_FORMAT
                or claims["approved"] is not True
                or claims["service_id"] != service_id
                or claims["commit"] != commit
                or claims["plugin_version"] != plugin_version
                or claims["authorization_id"] != authorization_id
                or claims["execution_id"] != execution_id
            ):
                return False
            if at.tzinfo is None:
                return False
            issued = datetime.fromisoformat(claims["issued_at"].replace("Z", "+00:00"))
            expires = datetime.fromisoformat(claims["expires_at"].replace("Z", "+00:00"))
            if issued.tzinfo is None or expires.tzinfo is None:
                return False
            current = at.astimezone(timezone.utc)
            issued = issued.astimezone(timezone.utc)
            expires = expires.astimezone(timezone.utc)
            if not (
                issued <= current <= expires
                and timedelta(0) < expires - issued <= MAX_APPROVAL_DURATION
            ):
                return False
            signature = base64.b64decode(receipt["signature"], validate=True)
            self._key.verify(signature, canonical_claims(claims))
            return True
        except (
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            InvalidSignature,
            binascii.Error,
            OverflowError,
        ):
            return False
