"""Manual, local macOS Keychain-backed Ed25519 approval receipts.

Run ONLY on the operator's Mac. No private key is written to disk, CI or Render.
Generic-password Keychain items are exportable by authorized local processes:
this is NOT equivalent to a Secure Enclave non-exportable key or remote authority.
"""

import argparse
import base64
import ctypes
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.services.deploy_approval import (
    RECEIPT_FORMAT,
    SignedApprovalVerifier,
    canonical_claims,
)

SERVICE = b"pet-finance-deploy-approval-v1"
ACCOUNT = b"local-operator-ed25519-v1"
IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{7,127}$")
SHA40 = re.compile(r"^[a-f0-9]{40}$")


class KeyStore(Protocol):
    def store(self, seed: bytes) -> None: ...

    def load(self) -> bytes: ...


class MacKeychain:
    """Use Security.framework directly so the seed never appears in argv or stdout."""

    def __init__(self) -> None:
        if sys.platform != "darwin":
            raise RuntimeError("MACOS_REQUIRED")
        self._api = ctypes.CDLL("/System/Library/Frameworks/Security.framework/Security")
        self._add: Any = self._api.SecKeychainAddGenericPassword
        self._add.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_char_p,
            ctypes.c_uint32,
            ctypes.c_char_p,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        self._add.restype = ctypes.c_int32
        self._find: Any = self._api.SecKeychainFindGenericPassword
        self._find.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_char_p,
            ctypes.c_uint32,
            ctypes.c_char_p,
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.c_void_p,
        ]
        self._find.restype = ctypes.c_int32
        self._free: Any = self._api.SecKeychainItemFreeContent
        self._free.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self._free.restype = ctypes.c_int32

    def store(self, seed: bytes) -> None:
        if len(seed) != 32:
            raise ValueError("INVALID_SEED_LENGTH")
        buf = ctypes.create_string_buffer(seed)
        status = self._add(
            None,
            len(SERVICE),
            SERVICE,
            len(ACCOUNT),
            ACCOUNT,
            len(seed),
            ctypes.cast(buf, ctypes.c_void_p),
            None,
        )
        if status != 0:
            # Duplicate item (-25299) deliberately fails: never overwrite a key.
            raise RuntimeError("KEYCHAIN_INITIALIZATION_FAILED")

    def load(self) -> bytes:
        length = ctypes.c_uint32()
        pointer = ctypes.c_void_p()
        status = self._find(
            None,
            len(SERVICE),
            SERVICE,
            len(ACCOUNT),
            ACCOUNT,
            ctypes.byref(length),
            ctypes.byref(pointer),
            None,
        )
        if status != 0:
            raise RuntimeError("KEYCHAIN_ITEM_UNAVAILABLE")
        try:
            seed = ctypes.string_at(pointer, length.value)
        finally:
            self._free(None, pointer)
        if len(seed) != 32:
            raise RuntimeError("INVALID_KEYCHAIN_ITEM")
        return seed


def public_key_bytes(seed: bytes) -> bytes:
    return (
        Ed25519PrivateKey.from_private_bytes(seed)
        .public_key()
        .public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    )


def initialize(store: KeyStore) -> str:
    """Generate a new identity only on this machine. Never overwrite an existing key."""
    private_key = Ed25519PrivateKey.generate()
    seed = private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    store.store(seed)
    return base64.b64encode(public_key_bytes(seed)).decode("ascii")


def sign_approval(
    store: KeyStore,
    *,
    authorization_id: str,
    execution_id: str,
    service_id: str,
    commit: str,
    plugin_version: str,
    now: datetime,
    ttl_minutes: int = 30,
) -> dict[str, object]:
    """Sign an explicitly confirmed single operation, never issue approval automatically."""
    if (
        not all(IDENTIFIER.fullmatch(x) for x in (authorization_id, execution_id))
        or not service_id.startswith("srv-")
        or not SHA40.fullmatch(commit)
        or plugin_version != "0.4.0"
        or not 1 <= ttl_minutes <= 120
        or now.tzinfo is None
    ):
        raise ValueError("INVALID_APPROVAL_REQUEST")
    now = now.astimezone(timezone.utc)
    seed = store.load()
    claims: dict[str, object] = {
        "format": RECEIPT_FORMAT,
        "authorization_id": authorization_id,
        "execution_id": execution_id,
        "service_id": service_id,
        "commit": commit,
        "plugin_version": plugin_version,
        "issued_at": now.isoformat(),
        "expires_at": (now + timedelta(minutes=ttl_minutes)).isoformat(),
        "approved": True,
    }
    signature = Ed25519PrivateKey.from_private_bytes(seed).sign(canonical_claims(claims))
    return {
        "claims": claims,
        "signature": base64.b64encode(signature).decode("ascii"),
    }


def save_receipt(path: Path, receipt: dict[str, object]) -> None:
    """Exclusive, owner-only output; do not store receipts under the repository."""
    import os

    data = json.dumps(receipt, ensure_ascii=False, sort_keys=True).encode("utf-8") + b"\n"
    fd = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local Ed25519 approval via macOS Keychain")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="Create the key locally and print PUBLIC key only")
    sub.add_parser("public", help="Print PUBLIC key from existing Keychain item")
    sign = sub.add_parser("sign", help="Manually approve one precise release")
    sign.add_argument("--authorization-id", required=True)
    sign.add_argument("--execution-id", required=True)
    sign.add_argument("--service-id", required=True)
    sign.add_argument("--commit", required=True)
    sign.add_argument("--plugin-version", default="0.4.0")
    sign.add_argument("--output", type=Path, required=True)
    sign.add_argument("--ttl-minutes", type=int, default=30)
    args = parser.parse_args(argv)
    try:
        store = MacKeychain()
        if args.command == "init":
            print("PUBLIC_KEY_BASE64=" + initialize(store))
        elif args.command == "public":
            print("PUBLIC_KEY_BASE64=" + base64.b64encode(public_key_bytes(store.load())).decode())
        else:
            # Explicit interactive approval. Non-interactive invocation always refuses to sign.
            if not sys.stdin.isatty():
                raise RuntimeError("INTERACTIVE_APPROVAL_REQUIRED")
            print("Release:", args.service_id, args.commit, args.execution_id)
            print("This signs approval ONLY; it never merges or deploys anything.")
            if input("Digite APROVAR para confirmar: ").strip() != "APROVAR":
                raise RuntimeError("APPROVAL_DENIED")
            now = datetime.now(timezone.utc)
            receipt = sign_approval(
                store,
                authorization_id=args.authorization_id,
                execution_id=args.execution_id,
                service_id=args.service_id,
                commit=args.commit,
                plugin_version=args.plugin_version,
                now=now,
                ttl_minutes=args.ttl_minutes,
            )
            # Verify before writing evidence; a successful signature is not a deploy permission.
            verifier = SignedApprovalVerifier(public_key_bytes(store.load()))
            if not verifier.verify(
                receipt,
                service_id=args.service_id,
                commit=args.commit,
                plugin_version=args.plugin_version,
                authorization_id=args.authorization_id,
                execution_id=args.execution_id,
                at=now,
            ):
                raise RuntimeError("SIGNATURE_SELF_CHECK_FAILED")
            save_receipt(args.output, receipt)
            print("SIGNED_RECEIPT_CREATED")
        return 0
    except (OSError, RuntimeError, ValueError):
        print("LOCAL_SIGNER_BLOCKED", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
