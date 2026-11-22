"""PR #54: encrypted baseline lifecycle and read-only release orchestration."""

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from cryptography.fernet import Fernet

from app.services.audit_snapshots import SECTION_NAMES, _digest
from app.services.deploy_orchestrator import prepare_predeploy, verify_postdeploy

SHA = "a" * 40
SERVICE = {"id": "srv-test", "branch": "main", "autoDeploy": "no", "autoDeployTrigger": "off"}
PLUGIN = {"version": "0.4.0", "read_only": True}
EMPTY_HASH = _digest([])


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
        "captured_at": "2026-11-22T19:00:00+00:00",
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
            "after_hash": "b" * 64 if mismatch else snapshot["snapshot_hash"],
            "sections": [
                {
                    "section": name,
                    "matches": not (mismatch and name == "transactions_archived"),
                    "changed_buckets": ["3"] if mismatch and name == "transactions_archived" else [],
                    "before_count": 0,
                    "after_count": 1 if mismatch and name == "transactions_archived" else 0,
                    "before_sha256": EMPTY_HASH,
                    "after_sha256": "b" * 64 if mismatch and name == "transactions_archived"
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
    )


def postflight(
    source: FakeSources, file: Path, key: bytes, *, commit: str = SHA
) -> dict[str, Any]:
    return verify_postdeploy(
        source,
        evidence_file=file,
        encryption_key=key,
        service_id="srv-test",
        commit=commit,
        plugin_version="0.4.0",
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
        "render_service", "main_commit", "plugin_info", "financial_snapshot",
        "render_service", "latest_deployment", "plugin_info", "compare_financial_audit",
    ]


@pytest.mark.parametrize(
    "field,new_value",
    [
        ("main", "b" * 40),
        ("plugin", {"version": "0.3.0", "read_only": True}),
        ("service", {"id": "srv-test", "branch": "main", "autoDeploy": "yes",
                     "autoDeployTrigger": "off"}),
    ],
)
def test_preflight_blocks_mismatched_evidence(
    tmp_path: Path, field: str, new_value: Any
) -> None:
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
    source.deployment["commit"]["id"] = "b" * 40
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
