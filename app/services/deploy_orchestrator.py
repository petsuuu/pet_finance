"""Read-only orchestration of separately authorized manual deployment audits.

The source adapter deliberately exposes *no* mutation or deployment methods.
Encrypted evidence is local; the key must be supplied through a separate secret channel.
"""

import json
import os
import stat
from pathlib import Path
from typing import Any, Protocol

from cryptography.fernet import Fernet, InvalidToken

from app.services.deploy_safety import check_postdeploy, check_predeploy

FORMAT = "pet-finance-manual-deploy-v1"


class ReadOnlySources(Protocol):
    """Adapter implemented by the operator; every method must be read-only."""

    def render_service(self) -> dict[str, Any]: ...

    def main_commit(self) -> str: ...

    def plugin_info(self) -> dict[str, Any]: ...

    def financial_snapshot(self) -> dict[str, Any]: ...

    def latest_deployment(self) -> dict[str, Any]: ...

    def compare_financial_audit(self, baseline: dict[str, Any]) -> dict[str, Any]: ...


def _blocked(reason: str, phase: str) -> dict[str, Any]:
    return {
        "phase": phase,
        "status": "BLOCKED",
        "approved": False,
        "issues": [reason],
        "read_only": True,
        "manual_deploy_authorization_required": True,
    }


def _vault_dir(directory: Path) -> bool:
    """Use an existing private directory or create one without following a symlink."""
    try:
        if not directory.exists():
            directory.mkdir(mode=0o700)
        mode = directory.lstat().st_mode
        return stat.S_ISDIR(mode) and not mode & 0o077
    except OSError:
        return False


def _save_private(directory: Path, name: str, value: bytes) -> Path:
    if not _vault_dir(directory):
        raise ValueError("INSECURE_EVIDENCE_DIRECTORY")
    path = directory / name
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(value)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path


def _read_private(path: Path) -> bytes:
    mode = path.lstat().st_mode
    if not stat.S_ISREG(mode) or mode & 0o077 or not _vault_dir(path.parent):
        raise ValueError("INSECURE_EVIDENCE_FILE")
    return path.read_bytes()


def prepare_predeploy(
    source: ReadOnlySources,
    *,
    service_id: str,
    commit: str,
    plugin_version: str,
    vault_dir: Path,
    encryption_key: bytes,
) -> dict[str, Any]:
    """Capture a *fresh* baseline and encrypt it. Does not authorize deployment."""
    try:
        service = source.render_service()
        main_commit = source.main_commit()
        plugin = source.plugin_info()
        baseline = source.financial_snapshot()
        report = check_predeploy(
            service=service,
            expected_service_id=service_id,
            expected_commit=commit,
            main_commit=main_commit,
            snapshot=baseline,
            plugin_info=plugin,
            expected_plugin_version=plugin_version,
        )
        if not report["approved"]:
            return report
        record = {
            "format": FORMAT,
            "service_id": service_id,
            "expected_commit": commit,
            "expected_plugin_version": plugin_version,
            "baseline": baseline,
        }
        payload = json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
        sealed = Fernet(encryption_key).encrypt(payload)
        destination = _save_private(vault_dir, "baseline-" + commit + ".sealed", sealed)
        return {**report, "evidence_file": str(destination),
                "baseline_hash": baseline["snapshot_hash"]}
    except (ValueError, OSError, KeyError, TypeError, InvalidToken):
        return _blocked("PREDEPLOY_EVIDENCE_UNAVAILABLE", "predeploy")
    except Exception:
        return _blocked("READ_ONLY_SOURCE_FAILURE", "predeploy")


def verify_postdeploy(
    source: ReadOnlySources,
    *,
    evidence_file: Path,
    encryption_key: bytes,
    service_id: str,
    commit: str,
    plugin_version: str,
) -> dict[str, Any]:
    """Decrypt the pre-deploy evidence, then compare current read-only observations."""
    try:
        encrypted = _read_private(evidence_file)
        record = json.loads(Fernet(encryption_key).decrypt(encrypted))
        if (
            not isinstance(record, dict)
            or record.get("format") != FORMAT
            or record.get("service_id") != service_id
            or record.get("expected_commit") != commit
            or record.get("expected_plugin_version") != plugin_version
            or not isinstance(record.get("baseline"), dict)
        ):
            return _blocked("EVIDENCE_BINDING_MISMATCH", "postdeploy")
        baseline = record["baseline"]
        service = source.render_service()
        deployment = source.latest_deployment()
        plugin = source.plugin_info()
        comparison = source.compare_financial_audit(baseline)
        report = check_postdeploy(
            service=service,
            expected_service_id=service_id,
            expected_commit=commit,
            expected_plugin_version=plugin_version,
            deployment=deployment,
            plugin_info=plugin,
            baseline=baseline,
            comparison=comparison,
        )
        return {**report, "baseline_hash": baseline["snapshot_hash"],
                "current_hash": comparison.get("after_hash"),
                "changed_sections": [
                    section.get("section")
                    for section in comparison.get("sections", [])
                    if isinstance(section, dict) and section.get("matches") is not True
                ]}
    except (ValueError, OSError, KeyError, TypeError, InvalidToken, json.JSONDecodeError):
        return _blocked("ENCRYPTED_EVIDENCE_INVALID", "postdeploy")
    except Exception:
        return _blocked("READ_ONLY_SOURCE_FAILURE", "postdeploy")
