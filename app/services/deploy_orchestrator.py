"""Fail-closed read-only pre/post deployment audit with encrypted local evidence.

No deployment, merge, financial write or Render settings method exists here.
The caller must independently verify the human authorization and trust the adapter.
"""

import json
import os
import re
import stat
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Protocol

from cryptography.fernet import Fernet, InvalidToken

from app.services.deploy_safety import check_postdeploy, check_predeploy

FORMAT = "pet-finance-manual-deploy-v2"
MAX_PREDEPLOY_AGE = timedelta(minutes=5)
MAX_AUDIT_WINDOW = timedelta(hours=2)
IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{7,127}$")


class ReadOnlySources(Protocol):
    """Only read operations; implementations must never expose write credentials."""

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


def _timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("INVALID_EVIDENCE_TIMESTAMP")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("NAIVE_EVIDENCE_TIMESTAMP")
    return parsed.astimezone(timezone.utc)


def _fresh(value: Any, *, now: datetime, max_age: timedelta) -> bool:
    try:
        age = now - _timestamp(value)
        return timedelta(0) <= age <= max_age
    except (ValueError, TypeError, OverflowError):
        return False


def _valid_reference(value: str) -> bool:
    return isinstance(value, str) and IDENTIFIER.fullmatch(value) is not None


@contextmanager
def _private_directory(path: Path, *, create: bool) -> Iterator[int]:
    """Open a private owned directory without following its final symlink."""
    if create:
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            pass
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        meta = os.fstat(fd)
        if not stat.S_ISDIR(meta.st_mode) or meta.st_uid != os.getuid() or meta.st_mode & 0o077:
            raise ValueError("INSECURE_EVIDENCE_DIRECTORY")
        yield fd
    finally:
        os.close(fd)


def _save_private(directory: Path, name: str, value: bytes) -> Path:
    if not re.fullmatch(r"baseline-[a-f0-9]{40}-[A-Za-z0-9_-]{8,128}\.sealed", name):
        raise ValueError("INVALID_EVIDENCE_FILENAME")
    with _private_directory(directory, create=True) as directory_fd:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(name, flags, 0o600, dir_fd=directory_fd)
        try:
            with os.fdopen(fd, "wb") as output:
                output.write(value)
                output.flush()
                os.fsync(output.fileno())
            os.fsync(directory_fd)
        except BaseException:
            os.unlink(name, dir_fd=directory_fd)
            os.fsync(directory_fd)
            raise
    return directory / name


def _read_private(path: Path) -> bytes:
    with _private_directory(path.parent, create=False) as directory_fd:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        fd = os.open(path.name, flags, dir_fd=directory_fd)
        with os.fdopen(fd, "rb") as stream:
            meta = os.fstat(stream.fileno())
            if not stat.S_ISREG(meta.st_mode) or meta.st_uid != os.getuid() or meta.st_mode & 0o077:
                raise ValueError("INSECURE_EVIDENCE_FILE")
            return stream.read()


def prepare_predeploy(
    source: ReadOnlySources,
    *,
    service_id: str,
    commit: str,
    plugin_version: str,
    authorization_id: str,
    execution_id: str,
    vault_dir: Path,
    encryption_key: bytes,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Create immutable encrypted evidence, but never authorize a deployment."""
    try:
        current = now or datetime.now(timezone.utc)
        if not _valid_reference(authorization_id) or not _valid_reference(execution_id):
            return _blocked("INVALID_APPROVAL_REFERENCE", "predeploy")
        if current.tzinfo is None:
            return _blocked("INVALID_CURRENT_TIME", "predeploy")
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
        if not _fresh(baseline.get("captured_at"), now=current, max_age=MAX_PREDEPLOY_AGE):
            return _blocked("STALE_BASELINE", "predeploy")
        record = {
            "format": FORMAT,
            "service_id": service_id,
            "expected_commit": commit,
            "expected_plugin_version": plugin_version,
            "authorization_id": authorization_id,
            "execution_id": execution_id,
            "captured_at": baseline["captured_at"],
            "baseline": baseline,
        }
        sealed = Fernet(encryption_key).encrypt(
            json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
        )
        path = _save_private(vault_dir, f"baseline-{commit}-{execution_id}.sealed", sealed)
        return {
            **report,
            "evidence_file": str(path),
            "baseline_hash": baseline["snapshot_hash"],
            "authorization_id": authorization_id,
            "execution_id": execution_id,
        }
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
    authorization_id: str,
    execution_id: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Revalidate authorization binding, evidence age, then compare read-only data."""
    try:
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            return _blocked("INVALID_CURRENT_TIME", "postdeploy")
        encrypted = _read_private(evidence_file)
        record = json.loads(Fernet(encryption_key).decrypt(encrypted))
        if (
            not isinstance(record, dict)
            or record.get("format") != FORMAT
            or record.get("service_id") != service_id
            or record.get("expected_commit") != commit
            or record.get("expected_plugin_version") != plugin_version
            or not _valid_reference(authorization_id)
            or not _valid_reference(execution_id)
            or record.get("authorization_id") != authorization_id
            or record.get("execution_id") != execution_id
            or not isinstance(record.get("baseline"), dict)
        ):
            return _blocked("EVIDENCE_BINDING_MISMATCH", "postdeploy")
        baseline = record["baseline"]
        if record.get("captured_at") != baseline.get("captured_at") or not _fresh(
            record.get("captured_at"), now=current, max_age=MAX_AUDIT_WINDOW
        ):
            return _blocked("EXPIRED_BASELINE", "postdeploy")
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
        if not _fresh(comparison.get("after_captured_at"), now=current, max_age=MAX_PREDEPLOY_AGE):
            return _blocked("STALE_POSTDEPLOY_EVIDENCE", "postdeploy")
        return {
            **report,
            "baseline_hash": baseline["snapshot_hash"],
            "current_hash": comparison.get("after_hash"),
            "authorization_id": authorization_id,
            "execution_id": execution_id,
            "changed_sections": [
                section.get("section")
                for section in comparison.get("sections", [])
                if isinstance(section, dict) and section.get("matches") is not True
            ],
        }
    except (ValueError, OSError, KeyError, TypeError, InvalidToken, json.JSONDecodeError):
        return _blocked("ENCRYPTED_EVIDENCE_INVALID", "postdeploy")
    except Exception:
        return _blocked("READ_ONLY_SOURCE_FAILURE", "postdeploy")
