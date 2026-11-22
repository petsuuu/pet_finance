"""PR #54: encrypted baseline lifecycle and read-only release orchestration."""

import os
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.services.audit_snapshots import SECTION_NAMES, _digest
from app.services.deploy_approval import RECEIPT_FORMAT, SignedApprovalVerifier, canonical_claims
from app.services.deploy_orchestrator import prepare_predeploy, verify_postdeploy

SHA = "a" * 40
NOW = datetime(2026, 11, 22, 19, 0, tzinfo=timezone.utc)
AUTHORIZATION = "approval-20261122-1234"
EXECUTION = "release-20261122-1234"
SERVICE = {"id": "srv-test", "branch": "main", "autoDeploy": "no", "autoDeployTrigger": "off"}
PLUGIN = {"version": "0.4.0", "read_only": True}
EMPTY_HASH = _digest([])


ISSUER_KEY = Ed25519PrivateKey.generate()
PUBLIC_KEY = ISSUER_KEY.public_key().public_bytes(
    encoding=serialization.Encoding.Raw,
    format=serialization.PublicFormat.Raw,
)
VERIFIER = SignedApprovalVerifier(PUBLIC_KEY)


def approval_receipt() -> dict[str, Any]:
    import base64

    claims = {
        "format": RECEIPT_FORMAT,
        "authorization_id": AUTHORIZATION,
        "execution_id": EXECUTION,
        "service_id": "srv-test",
        "commit": SHA,
        "plugin_version": "0.4.0",
        "issued_at": (NOW - timedelta(minutes=1)).isoformat(),
        "expires_at": (NOW + timedelta(hours=1)).isoformat(),
        "approved": True,
    }
    return {
        "claims": claims,
        "signature": base64.b64encode(ISSUER_KEY.sign(canonical_claims(claims))).decode(),
    }


def baseline() -> dict[str, Any]:
    sections = {
        name: {
            "count": 0,
            "sha256": EMPTY_HASH,
            "buckets": {str(i): {"count": 0, "sha256": EMPTY_HASH} for i in range(16)},
        }
        for name in SECTION_NAMES
    }
    core: dict[str, Any] = {
        "schema_version": 2,
        "hash_algorithm": "sha256",
        "sections": sections,
        "balances": [],
        "quality": {"status": "OK", "total_findings": 0, "balance_mismatches": 0},
    }
    return {
        **core,
        "snapshot_hash": _digest(core),
        "captured_at": NOW.isoformat(),
        "read_only": True,
    }


class FakeSources:
    """Only the six read operations; no merge/deploy/write operation exists."""

    def __init__(self) -> None:
        self.service = deepcopy(SERVICE)
        self.main = SHA
        self.plugin = deepcopy(PLUGIN)
        self.snapshot = baseline()
        self.deployment = {"status": "live", "commit": {"id": SHA}}
        self.drift = False
        self.calls: list[str] = []

    def render_service(self) -> dict[str, Any]:
        self.calls.append("render_service")
        return deepcopy(self.service)

    def main_commit(self) -> str:
        self.calls.append("main_commit")
        return self.main

    def plugin_info(self) -> dict[str, Any]:
        self.calls.append("plugin_info")
        return deepcopy(self.plugin)

    def financial_snapshot(self) -> dict[str, Any]:
        self.calls.append("financial_snapshot")
        return deepcopy(self.snapshot)

    def latest_deployment(self) -> dict[str, Any]:
        self.calls.append("latest_deployment")
        return deepcopy(self.deployment)

    def compare_financial_audit(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        self.calls.append("compare_financial_audit")
        mismatch = self.drift
        return {
            "status": "REVIEW_REQUIRED" if mismatch else "MATCH",
            "matches": not mismatch,
            "requires_user_review": mismatch,
            "read_only": True,
            "before_hash": snapshot["snapshot_hash"],
            "after_captured_at": NOW.isoformat(),
            "after_hash": "b" * 64 if mismatch else snapshot["snapshot_hash"],
            "sections": [
                {
                    "section": name,
                    "matches": not (mismatch and name == "transactions_archived"),
                    "changed_buckets": (
                        ["3"] if mismatch and name == "transactions_archived" else []
                    ),
                    "before_count": 0,
                    "after_count": 1 if mismatch and name == "transactions_archived" else 0,
                    "before_sha256": EMPTY_HASH,
                    "after_sha256": "b" * 64
                    if mismatch and name == "transactions_archived"
                    else EMPTY_HASH,
                }
                for name in SECTION_NAMES
            ],
            "changed_account_balances": [],
            "quality_before": {"status": "OK", "total_findings": 0},
            "quality_after": {"status": "OK", "total_findings": 0},
        }


def preflight(
    source: FakeSources, directory: Path, key: bytes, *, commit: str = SHA
) -> dict[str, Any]:
    return prepare_predeploy(
        source,
        service_id="srv-test",
        commit=commit,
        plugin_version="0.4.0",
        vault_dir=directory,
        encryption_key=key,
        authorization_id=AUTHORIZATION,
        execution_id=EXECUTION,
        approval_receipt=approval_receipt(),
        approval_verifier=VERIFIER,
        now=NOW,
    )


def postflight(source: FakeSources, file: Path, key: bytes, *, commit: str = SHA) -> dict[str, Any]:
    return verify_postdeploy(
        source,
        evidence_file=file,
        encryption_key=key,
        service_id="srv-test",
        commit=commit,
        plugin_version="0.4.0",
        authorization_id=AUTHORIZATION,
        execution_id=EXECUTION,
        approval_verifier=VERIFIER,
        now=NOW + timedelta(minutes=2),
    )


def test_sealed_evidence_success_and_report(tmp_path: Path) -> None:
    key = Fernet.generate_key()
    source = FakeSources()
    report = preflight(source, tmp_path / "private", key)
    assert report["status"] == "PASS"
    assert report["manual_deploy_authorization_required"] is True
    file = Path(report["evidence_file"])
    assert file.stat().st_mode & 0o077 == 0
    assert file.parent.stat().st_mode & 0o077 == 0
    assert b"current_balance" not in file.read_bytes()
    assert b"snapshot_hash" not in file.read_bytes()
    after = postflight(source, file, key)
    assert after["status"] == "PASS"
    assert after["baseline_hash"] == after["current_hash"]
    assert after["changed_sections"] == []
    assert source.calls == [
        "render_service",
        "main_commit",
        "plugin_info",
        "financial_snapshot",
        "render_service",
        "latest_deployment",
        "plugin_info",
        "compare_financial_audit",
    ]


@pytest.mark.parametrize(
    "field,new_value",
    [
        ("main", "b" * 40),
        ("plugin", {"version": "0.3.0", "read_only": True}),
        (
            "service",
            {"id": "srv-test", "branch": "main", "autoDeploy": "yes", "autoDeployTrigger": "off"},
        ),
    ],
)
def test_preflight_blocks_mismatched_evidence(tmp_path: Path, field: str, new_value: Any) -> None:
    source = FakeSources()
    setattr(source, field, new_value)
    report = preflight(source, tmp_path / "private", Fernet.generate_key())
    assert report["status"] == "BLOCKED"
    assert not (tmp_path / "private").exists()


def test_preflight_refuses_to_overwrite_existing_baseline(tmp_path: Path) -> None:
    source = FakeSources()
    key = Fernet.generate_key()
    first = preflight(source, tmp_path / "private", key)
    original = Path(first["evidence_file"]).read_bytes()
    second = preflight(source, tmp_path / "private", key)
    assert second["status"] == "BLOCKED"
    assert Path(first["evidence_file"]).read_bytes() == original


def test_postflight_rejects_wrong_key_tamper_and_commit(tmp_path: Path) -> None:
    source = FakeSources()
    key = Fernet.generate_key()
    file = Path(preflight(source, tmp_path / "private", key)["evidence_file"])
    assert postflight(source, file, Fernet.generate_key())["status"] == "BLOCKED"
    assert postflight(source, file, key, commit="b" * 40)["status"] == "BLOCKED"
    file.write_bytes(file.read_bytes()[:-3] + b"bad")
    assert postflight(source, file, key)["status"] == "BLOCKED"


def test_postflight_stops_on_archive_drift_and_wrong_live_commit(tmp_path: Path) -> None:
    source = FakeSources()
    key = Fernet.generate_key()
    file = Path(preflight(source, tmp_path / "private", key)["evidence_file"])
    source.drift = True
    report = postflight(source, file, key)
    assert report["status"] == "BLOCKED"
    assert report["changed_sections"] == ["transactions_archived"]
    source.drift = False
    source.deployment["commit"] = {"id": "b" * 40}
    assert "LIVE_COMMIT_MISMATCH" in postflight(source, file, key)["issues"]


def test_postflight_refuses_world_readable_evidence(tmp_path: Path) -> None:
    source = FakeSources()
    key = Fernet.generate_key()
    file = Path(preflight(source, tmp_path / "private", key)["evidence_file"])
    file.chmod(0o644)
    assert postflight(source, file, key)["status"] == "BLOCKED"


def test_read_only_source_failure_blocks_without_secret_leak(tmp_path: Path) -> None:
    source = FakeSources()

    def broken() -> dict[str, Any]:
        raise RuntimeError("sensitive secret should not be leaked")

    source.financial_snapshot = broken  # type: ignore[method-assign]
    result = preflight(source, tmp_path / "private", Fernet.generate_key())
    assert result["status"] == "BLOCKED"
    assert "secret" not in str(result)


def test_bound_authorization_execution_and_baseline_freshness(tmp_path: Path) -> None:
    source = FakeSources()
    key = Fernet.generate_key()
    file = Path(preflight(source, tmp_path / "private", key)["evidence_file"])
    invalid_auth = verify_postdeploy(
        source,
        evidence_file=file,
        encryption_key=key,
        service_id="srv-test",
        commit=SHA,
        plugin_version="0.4.0",
        authorization_id="another-approval-reference",
        execution_id=EXECUTION,
        approval_verifier=VERIFIER,
        now=NOW + timedelta(minutes=1),
    )
    assert invalid_auth["status"] == "BLOCKED"
    assert invalid_auth["issues"] == ["EVIDENCE_BINDING_MISMATCH"]
    expired = verify_postdeploy(
        source,
        evidence_file=file,
        encryption_key=key,
        service_id="srv-test",
        commit=SHA,
        plugin_version="0.4.0",
        authorization_id=AUTHORIZATION,
        execution_id=EXECUTION,
        approval_verifier=VERIFIER,
        now=NOW + timedelta(hours=3),
    )
    assert expired["issues"] == ["AUTHORIZATION_NOT_VERIFIED"]
    source.snapshot["captured_at"] = (NOW - timedelta(minutes=6)).isoformat()
    assert preflight(source, tmp_path / "fresh", key)["issues"] == ["STALE_BASELINE"]


def test_future_dated_evidence_cannot_pass(tmp_path: Path) -> None:
    source = FakeSources()
    source.snapshot["captured_at"] = (NOW + timedelta(minutes=1)).isoformat()
    result = preflight(source, tmp_path / "future", Fernet.generate_key())
    assert result["issues"] == ["STALE_BASELINE"]


def test_postflight_rejects_stale_comparison(tmp_path: Path) -> None:
    source = FakeSources()
    key = Fernet.generate_key()
    file = Path(preflight(source, tmp_path / "private", key)["evidence_file"])
    original = source.compare_financial_audit

    def stale(baseline: dict[str, Any]) -> dict[str, Any]:
        result = original(baseline)
        result["after_captured_at"] = (NOW - timedelta(minutes=6)).isoformat()
        return result

    source.compare_financial_audit = stale  # type: ignore[assignment]
    assert postflight(source, file, key)["issues"] == ["STALE_POSTDEPLOY_EVIDENCE"]


def test_vault_rejects_symlink_and_non_private_directory(tmp_path: Path) -> None:
    source = FakeSources()
    key = Fernet.generate_key()
    trusted = tmp_path / "trusted"
    trusted.mkdir(mode=0o700)
    shortcut = tmp_path / "shortcut"
    shortcut.symlink_to(trusted, target_is_directory=True)
    assert preflight(source, shortcut, key)["status"] == "BLOCKED"
    public = tmp_path / "public"
    public.mkdir()
    public.chmod(0o755)
    assert preflight(source, public, key)["status"] == "BLOCKED"


def test_vault_rejects_symlink_evidence_file(tmp_path: Path) -> None:
    source = FakeSources()
    key = Fernet.generate_key()
    file = Path(preflight(source, tmp_path / "private", key)["evidence_file"])
    link = file.parent / "linked.sealed"
    link.symlink_to(file)
    assert postflight(source, link, key)["status"] == "BLOCKED"


def test_no_partial_file_on_fsync_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = FakeSources()
    key = Fernet.generate_key()
    original = os.fsync
    calls = 0

    def broken(fd: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("filesystem failure")
        original(fd)

    monkeypatch.setattr(os, "fsync", broken)
    result = preflight(source, tmp_path / "private", key)
    assert result["status"] == "BLOCKED"
    assert list((tmp_path / "private").iterdir()) == []


def test_predeploy_rejects_forged_or_missing_approval(tmp_path: Path) -> None:
    source = FakeSources()
    key = Fernet.generate_key()
    forged = approval_receipt()
    forged["claims"]["approved"] = False
    from app.services.deploy_orchestrator import prepare_predeploy

    data = {
        "service_id": "srv-test",
        "commit": SHA,
        "plugin_version": "0.4.0",
        "authorization_id": AUTHORIZATION,
        "execution_id": EXECUTION,
        "approval_verifier": VERIFIER,
        "vault_dir": tmp_path / "private",
        "encryption_key": key,
        "now": NOW,
    }
    result = prepare_predeploy(source, approval_receipt=forged, **data)
    assert result["issues"] == ["AUTHORIZATION_NOT_VERIFIED"]
    assert source.calls == []
    assert not (tmp_path / "private").exists()


def test_postdeploy_rejects_expired_external_approval(tmp_path: Path) -> None:
    source = FakeSources()
    key = Fernet.generate_key()
    file = Path(preflight(source, tmp_path / "private", key)["evidence_file"])
    result = verify_postdeploy(
        source,
        evidence_file=file,
        encryption_key=key,
        service_id="srv-test",
        commit=SHA,
        plugin_version="0.4.0",
        authorization_id=AUTHORIZATION,
        execution_id=EXECUTION,
        approval_verifier=VERIFIER,
        now=NOW + timedelta(hours=1, minutes=1),
    )
    assert result["issues"] == ["AUTHORIZATION_NOT_VERIFIED"]
