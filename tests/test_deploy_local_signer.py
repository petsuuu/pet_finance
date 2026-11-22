"""Local approval authority tests never touch the real macOS Keychain."""

import base64
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from app.services.deploy_approval import SignedApprovalVerifier
from app.services.deploy_local_signer import (
    initialize,
    public_key_bytes,
    save_receipt,
    sign_approval,
)

NOW = datetime(2026, 11, 22, 20, 0, tzinfo=timezone.utc)
SHA = "a" * 40


class MemoryKeychain:
    def __init__(self) -> None:
        self.seed: bytes | None = None

    def store(self, seed: bytes) -> None:
        if self.seed is not None:
            raise RuntimeError("DUPLICATE_IDENTITY")
        self.seed = seed

    def load(self) -> bytes:
        if self.seed is None:
            raise RuntimeError("NOT_INITIALIZED")
        return self.seed


def signed_receipt(store: MemoryKeychain, **overrides: Any) -> dict[str, object]:
    arguments: dict[str, Any] = {
        "authorization_id": "approval-20261122-01",
        "execution_id": "execution-20261122-01",
        "service_id": "srv-example123",
        "commit": SHA,
        "plugin_version": "0.4.0",
        "now": NOW,
        "ttl_minutes": 30,
    }
    arguments.update(overrides)
    return sign_approval(store, **arguments)


def test_local_keychain_approval_and_verification() -> None:
    keychain = MemoryKeychain()
    public = initialize(keychain)
    assert public == base64.b64encode(public_key_bytes(keychain.load())).decode()
    assert len(base64.b64decode(public)) == 32
    receipt = signed_receipt(keychain)
    verifier = SignedApprovalVerifier(base64.b64decode(public))
    assert verifier.verify(
        receipt,
        service_id="srv-example123",
        commit=SHA,
        plugin_version="0.4.0",
        authorization_id="approval-20261122-01",
        execution_id="execution-20261122-01",
        at=NOW,
    )
    assert not verifier.verify(
        receipt,
        service_id="srv-other999", commit=SHA, plugin_version="0.4.0",
        authorization_id="approval-20261122-01",
        execution_id="execution-20261122-01", at=NOW,
    )
    assert not verifier.verify(
        receipt,
        service_id="srv-example123", commit=SHA, plugin_version="0.4.0",
        authorization_id="approval-20261122-01",
        execution_id="execution-20261122-01", at=NOW + timedelta(minutes=31),
    )


def test_keychain_never_rotates_identity_implicitly() -> None:
    store = MemoryKeychain()
    public = initialize(store)
    with pytest.raises(RuntimeError, match="DUPLICATE_IDENTITY"):
        initialize(store)
    assert public_key_bytes(store.load()) == base64.b64decode(public)


@pytest.mark.parametrize(
    "override",
    [
        {"commit": "bad"},
        {"plugin_version": "0.3.0"},
        {"authorization_id": "invalid"},
        {"execution_id": "invalid"},
        {"service_id": "not-render"},
        {"ttl_minutes": 121},
        {"now": datetime(2026, 11, 22)},
    ],
)
def test_invalid_signing_requests_never_succeed(override: dict[str, Any]) -> None:
    store = MemoryKeychain()
    initialize(store)
    with pytest.raises(ValueError, match="INVALID_APPROVAL_REQUEST"):
        signed_receipt(store, **override)


def test_receipt_creation_is_exclusive_and_owner_private(tmp_path: Path) -> None:
    store = MemoryKeychain()
    initialize(store)
    file = tmp_path / "signed.json"
    receipt = signed_receipt(store)
    save_receipt(file, receipt)
    assert file.stat().st_mode & 0o077 == 0
    assert b"private" not in file.read_bytes()
    original = file.read_bytes()
    with pytest.raises(FileExistsError):
        save_receipt(file, receipt)
    assert file.read_bytes() == original


def test_signer_blocks_missing_macos_keychain() -> None:
    import sys

    if sys.platform != "darwin":
        from app.services.deploy_local_signer import MacKeychain

        with pytest.raises(RuntimeError, match="MACOS_REQUIRED"):
            MacKeychain()
