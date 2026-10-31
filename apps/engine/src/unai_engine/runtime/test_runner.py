"""Explicitly gated Python test execution on a disposable workspace copy."""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from unai_engine.models import (
    EvidenceRecord,
    TaskEvidenceSnapshot,
    TaskTestRunResponse,
)
from unai_engine.runtime.sandbox import DockerSandbox, SandboxUnavailable
from unai_engine.runtime.workspace import WorkspaceError, resolve_workspace_path
from unai_engine.verification.ledger import EvidenceLedger


COMMAND = "python -m pytest -q"
SKIP_DIRS = {
    ".git", ".venv", "venv", "env", "node_modules", "dist", "build",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".next", ".data", ".brain-vault", "coverage", "target",
}
SAFE_SUFFIXES = {
    ".py", ".toml", ".ini", ".cfg", ".txt", ".md", ".json",
    ".yaml", ".yml", ".rst", ".sh",
}
SAFE_NAMES = {
    "pytest.ini", "tox.ini", "setup.cfg", "setup.py", "pyproject.toml",
    "requirements.txt", "requirements-dev.txt", "conftest.py", "Makefile",
    "Pipfile", "Pipfile.lock",
}
SECRET_NAMES = {
    "credentials", "credentials.json", "secrets", "secrets.json",
    "id_rsa", "id_ed25519", "known_hosts", ".netrc",
}
SECRET_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".sqlite", ".db"}
_REDACT_ASSIGNMENT = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret|authorization)\b(\s*[:=]\s*|\s+)([^\s,;]+)"
)
_REDACT_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")


class TaskRunRejected(PermissionError):
    pass


class WorkspaceStagingError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class StagedWorkspace:
    files: int
    bytes_copied: int
    skipped_files: int


def _is_secret_file(path: Path) -> bool:
    name = path.name.casefold()
    return (
        name in SECRET_NAMES
        or name.startswith(".env")
        or path.suffix.casefold() in SECRET_SUFFIXES
        or "secret" in name
        or "credential" in name
    )


def stage_python_workspace(
    source: Path,
    destination: Path,
    *,
    max_files: int = 5_000,
    max_bytes: int = 50 * 1024 * 1024,
    max_file_bytes: int = 2 * 1024 * 1024,
) -> StagedWorkspace:
    """Copy eligible source/config files; never follow or copy symlinks/secrets."""
    source_root = source.resolve(strict=True)
    if not source_root.is_dir():
        raise WorkspaceStagingError("Source workspace is not a directory.")
    destination.mkdir(parents=True, exist_ok=False)

    file_count = 0
    total_bytes = 0
    skipped = 0

    for current, dirs, names in os.walk(source_root, topdown=True, followlinks=False):
        current_path = Path(current)
        dirs[:] = [
            name for name in dirs
            if name not in SKIP_DIRS
            and not name.startswith(".")
            and not (current_path / name).is_symlink()
        ]

        for name in names:
            source_file = current_path / name
            if source_file.is_symlink() or _is_secret_file(source_file):
                skipped += 1
                continue
            if source_file.suffix.casefold() not in SAFE_SUFFIXES and name not in SAFE_NAMES:
                skipped += 1
                continue

            try:
                size = source_file.stat().st_size
            except OSError:
                skipped += 1
                continue
            if size > max_file_bytes:
                skipped += 1
                continue
            if file_count >= max_files or total_bytes + size > max_bytes:
                raise WorkspaceStagingError("Workspace staging budget exceeded; no tests were run.")

            relative = source_file.relative_to(source_root)
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)

            try:
                with source_file.open("rb") as reader, target.open("xb") as writer:
                    copied = 0
                    while True:
                        chunk = reader.read(64 * 1024)
                        if not chunk:
                            break
                        copied += len(chunk)
                        if copied > max_file_bytes or total_bytes + copied > max_bytes:
                            raise WorkspaceStagingError("Workspace changed or exceeded the staging byte limit.")
                        writer.write(chunk)
            except OSError:
                skipped += 1
                try:
                    target.unlink(missing_ok=True)
                except OSError:
                    pass
                continue

            file_count += 1
            total_bytes += copied

    return StagedWorkspace(file_count, total_bytes, skipped)


def _redact_preview(value: str, limit: int = 2_000) -> str:
    redacted = _REDACT_ASSIGNMENT.sub(r"\1\2[REDACTED]", value)
    redacted = _REDACT_BEARER.sub("Bearer [REDACTED]", redacted)
    return redacted[-limit:]


class IsolatedPythonTestRunner:
    """Runs only pytest in Docker, with no network and a filtered temporary copy."""

    def __init__(
        self,
        ledger: EvidenceLedger,
        sandbox: DockerSandbox,
        *,
        max_files: int = 5_000,
        max_bytes: int = 50 * 1024 * 1024,
    ) -> None:
        self.ledger = ledger
        self.sandbox = sandbox
        self.max_files = max_files
        self.max_bytes = max_bytes

    def _response(
        self,
        snapshot: TaskEvidenceSnapshot,
        status: str,
        passed: bool | None,
        claim: str,
        *,
        exit_code: int | None = None,
        timed_out: bool = False,
        staged: StagedWorkspace | None = None,
        stdout: str = "",
        stderr: str = "",
        error: str | None = None,
        sandboxed: bool = False,
    ) -> TaskTestRunResponse:
        now = datetime.now(timezone.utc).isoformat()
        stdout_bytes = stdout.encode("utf-8", errors="replace")
        stderr_bytes = stderr.encode("utf-8", errors="replace")
        metadata: dict[str, Any] = {
            "command": COMMAND,
            "exit_code": exit_code,
            "timed_out": timed_out,
            "staged_files": staged.files if staged else 0,
            "staged_bytes": staged.bytes_copied if staged else 0,
            "skipped_files": staged.skipped_files if staged else 0,
            "stdout_sha256": hashlib.sha256(stdout_bytes).hexdigest(),
            "stderr_sha256": hashlib.sha256(stderr_bytes).hexdigest(),
        }
        if error:
            metadata["error"] = error
        evidence = EvidenceRecord(
            evidence_id=uuid4().hex,
            task_id=snapshot.task_id,
            kind="execution",
            source="docker-sandbox",
            claim=claim,
            passed=passed,
            artifact_ref=f"task:{snapshot.task_id}/python-tests",
            metadata=metadata,
            created_at=now,
        )
        self.ledger.append_evidence(evidence)
        return TaskTestRunResponse(
            task_id=snapshot.task_id,
            status=status,
            command=COMMAND,
            exit_code=exit_code,
            timed_out=timed_out,
            sandboxed=sandboxed,
            staged_files=staged.files if staged else 0,
            staged_bytes=staged.bytes_copied if staged else 0,
            skipped_files=staged.skipped_files if staged else 0,
            stdout_preview=_redact_preview(stdout),
            stderr_preview=_redact_preview(stderr),
            evidence=evidence,
        )

    def run(self, task_id: str, workspace_root: Path) -> TaskTestRunResponse:
        snapshot = self.ledger.get_snapshot(task_id)
        if snapshot is None:
            raise KeyError(task_id)
        if snapshot.status != "inspection_complete":
            raise TaskRunRejected("Task must complete read-only inspection before tests can run.")
        permissions = set(snapshot.contract.permissions)
        if "workspace_read" not in permissions or "run_tests" not in permissions:
            raise TaskRunRejected("This task has not explicitly approved workspace_read and run_tests.")

        try:
            workspace = resolve_workspace_path(workspace_root, snapshot.workspace)
        except WorkspaceError as exc:
            raise TaskRunRejected("The inspected workspace is no longer inside the configured root.") from exc
        if not workspace.is_dir():
            raise TaskRunRejected("The inspected workspace no longer exists.")

        staged: StagedWorkspace | None = None
        stdout = ""
        stderr = ""
        try:
            with tempfile.TemporaryDirectory(prefix="unai-test-") as temporary:
                stage_path = Path(temporary) / "workspace"
                staged = stage_python_workspace(
                    workspace,
                    stage_path,
                    max_files=self.max_files,
                    max_bytes=self.max_bytes,
                )
                result = self.sandbox.run(stage_path, COMMAND)
                stdout = result.stdout or ""
                stderr = result.stderr or ""
                if result.exit_code != 0 and (
                    "No module named pytest" in stderr
                    or "No module named pytest" in stdout
                ):
                    return self._response(
                        snapshot,
                        "unavailable",
                        None,
                        "Sandbox image does not include pytest; no test verdict is available.",
                        exit_code=result.exit_code,
                        timed_out=result.timed_out,
                        staged=staged,
                        stdout=stdout,
                        stderr=stderr,
                        error="pytest_missing_from_sandbox_image",
                        sandboxed=True,
                    )
                if result.exit_code == 125 and any(
                    marker in stderr.casefold()
                    for marker in ("cannot connect to the docker daemon", "unable to find image", "error during connect")
                ):
                    return self._response(
                        snapshot,
                        "unavailable",
                        None,
                        "Docker could not start the requested sandbox container.",
                        exit_code=result.exit_code,
                        timed_out=result.timed_out,
                        staged=staged,
                        stdout=stdout,
                        stderr=stderr,
                        error="docker_container_unavailable",
                        sandboxed=False,
                    )
                status = "passed" if result.exit_code == 0 and not result.timed_out else "failed"
                return self._response(
                    snapshot,
                    status,
                    status == "passed",
                    f"Sandboxed pytest exited with code {result.exit_code}.",
                    exit_code=result.exit_code,
                    timed_out=result.timed_out,
                    staged=staged,
                    stdout=stdout,
                    stderr=stderr,
                    sandboxed=True,
                )
        except (SandboxUnavailable, WorkspaceStagingError, OSError) as exc:
            return self._response(
                snapshot,
                "unavailable",
                None,
                "Isolated test execution could not be completed.",
                staged=staged,
                stdout=stdout,
                stderr=stderr,
                error=type(exc).__name__,
            )
