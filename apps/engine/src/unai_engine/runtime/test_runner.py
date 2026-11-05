"""Explicitly gated Python test execution on a disposable workspace copy."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
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
GENERATED_TEST_EVIDENCE_SOURCES = {
    "grounded-oracle-sandbox",
    "grounded-oracle-shrink-sandbox",
    "grounded-mutation-sandbox",
}
MUTATION_EVIDENCE_SOURCE = "grounded-mutation-sandbox"
MAX_SOURCE_OVERRIDES = 2
MAX_SOURCE_OVERRIDE_BYTES = 300_000
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
    "service-account.json", "service_account.json", "google-services.json",
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
        or any(marker in name for marker in ("secret", "credential", "password", "token", "private"))
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
                flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                source_fd = os.open(source_file, flags)
                with os.fdopen(source_fd, "rb") as reader, target.open("xb") as writer:
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


def _validate_source_overrides(
    source_overrides: dict[str, str] | None,
) -> dict[str, str]:
    """Validate mutant-source replacements before touching the staging copy."""
    if source_overrides is None:
        return {}
    if not isinstance(source_overrides, dict) or len(source_overrides) > MAX_SOURCE_OVERRIDES:
        raise WorkspaceStagingError("Source overrides exceed the allowed count.")
    normalized: dict[str, str] = {}
    for relative_path, content in source_overrides.items():
        if not isinstance(relative_path, str) or not isinstance(content, str):
            raise WorkspaceStagingError("Source overrides must map paths to text content.")
        relative = PurePosixPath(relative_path)
        if (
            relative.is_absolute()
            or "\\" in relative_path
            or relative.as_posix() != relative_path
            or any(part in {"", ".", ".."} for part in relative.parts)
            or not relative.parts
            or relative.parts[0] == "unai_generated_tests"
            or relative.suffix.casefold() != ".py"
        ):
            raise WorkspaceStagingError("Source override path is outside the allowed boundary.")
        encoded = content.encode("utf-8")
        if not encoded or len(encoded) > MAX_SOURCE_OVERRIDE_BYTES:
            raise WorkspaceStagingError("Source override content is empty or exceeds its size limit.")
        normalized[relative_path] = content
    return normalized


def _redact_preview(value: str, limit: int = 2_000) -> str:
    redacted = _REDACT_ASSIGNMENT.sub(r"\1\2[REDACTED]", value)
    redacted = _REDACT_BEARER.sub("Bearer [REDACTED]", redacted)
    return redacted[-limit:]


def _validated_replay_trace(
    workspace: Path,
    relative_path: str,
    expected_case_ids: list[str],
) -> tuple[list[dict[str, Any]], str]:
    """Load only the small, hash-only replay trace written by the generated harness."""
    relative = PurePosixPath(relative_path)
    if (
        relative.is_absolute()
        or relative.as_posix() != relative_path
        or len(relative.parts) != 2
        or relative.parts[0] != "unai_generated_tests"
        or relative.suffix != ".json"
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        return [], "invalid_path"
    expected = {
        item for item in expected_case_ids
        if isinstance(item, str) and item
    }
    if not expected or len(expected) > 20:
        return [], "invalid_case_ids"
    workspace_root = workspace.resolve()
    trace_directory = workspace / relative.parts[0]
    if trace_directory.is_symlink() or not trace_directory.is_dir():
        return [], "unsafe_directory"
    try:
        resolved_directory = trace_directory.resolve(strict=True)
        if not resolved_directory.is_relative_to(workspace_root):
            return [], "unsafe_directory"
        trace_path = trace_directory / relative.parts[1]
        if trace_path.is_symlink() or not trace_path.is_file():
            return [], "missing"
        resolved_trace_path = trace_path.resolve(strict=True)
        if not resolved_trace_path.is_relative_to(resolved_directory):
            return [], "unsafe_path"
        if resolved_trace_path.stat().st_size > 16_384:
            return [], "oversized"
        payload = json.loads(resolved_trace_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return [], "invalid"
    if (
        not isinstance(payload, dict)
        or payload.get("schema") != "unai.replay-trace.v1"
        or not isinstance(payload.get("records"), list)
        or len(payload["records"]) > 20
    ):
        return [], "invalid"

    seen: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for record in payload["records"]:
        if (
            not isinstance(record, dict)
            or not isinstance(record.get("case_id"), str)
            or record["case_id"] not in expected
            or record["case_id"] in seen
        ):
            return [], "invalid"
        seen.add(record["case_id"])
        kind = record.get("kind")
        if kind == "output_stable_across_replays":
            if (
                not isinstance(record.get("replay_count"), int)
                or isinstance(record.get("replay_count"), bool)
                or not 2 <= record["replay_count"] <= 4
                or not isinstance(record.get("stable"), bool)
                or not isinstance(record.get("results"), list)
                or len(record["results"]) != record["replay_count"]
            ):
                return [], "invalid"
            results: list[dict[str, Any]] = []
            result_hashes: list[str] = []
            all_returned = True
            for index, item in enumerate(record["results"], start=1):
                if (
                    not isinstance(item, dict)
                    or item.get("replay_index") != index
                    or item.get("status") not in {"returned", "raised", "non_json_return"}
                    or not isinstance(item.get("actual_type"), str)
                ):
                    return [], "invalid"
                normalized_item: dict[str, Any] = {
                    "replay_index": index,
                    "status": item["status"],
                    "actual_type": item["actual_type"][:120],
                }
                digest = item.get("actual_sha256")
                if digest is not None:
                    if not isinstance(digest, str) or re.fullmatch(r"[a-fA-F0-9]{64}", digest) is None:
                        return [], "invalid"
                    normalized_item["actual_sha256"] = digest.lower()
                if item["status"] == "returned":
                    if digest is None:
                        return [], "invalid"
                    result_hashes.append(digest.lower())
                else:
                    all_returned = False
                exception_digest = item.get("exception_message_sha256")
                if exception_digest is not None:
                    if not isinstance(exception_digest, str) or re.fullmatch(r"[a-fA-F0-9]{64}", exception_digest) is None:
                        return [], "invalid"
                    normalized_item["exception_message_sha256"] = exception_digest.lower()
                frame = item.get("target_frame")
                if frame is not None:
                    if (
                        not isinstance(frame, dict)
                        or not isinstance(frame.get("line"), int)
                        or isinstance(frame.get("line"), bool)
                        or frame["line"] < 1
                        or not isinstance(frame.get("function"), str)
                    ):
                        return [], "invalid"
                    normalized_item["target_frame"] = {
                        "line": frame["line"],
                        "function": frame["function"][:128],
                    }
                results.append(normalized_item)
            computed_stable = (
                all_returned
                and len(result_hashes) == record["replay_count"]
                and len(set(result_hashes)) == 1
            )
            if computed_stable != record["stable"]:
                return [], "invalid"
            normalized.append({
                "case_id": record["case_id"],
                "kind": "output_stable_across_replays",
                "replay_count": record["replay_count"],
                "stable": computed_stable,
                "results": results,
            })
        elif kind in {
            "workspace_filesystem_unchanged",
            "external_fixture_filesystem_unchanged",
        }:
            audit_status = record.get("status")
            if audit_status == "unavailable":
                reason_code = record.get("reason_code")
                allowed_reasons = {
                    "entry_limit_exceeded",
                    "file_size_limit_exceeded",
                    "request_byte_budget_exceeded",
                    "unsupported_entry_type",
                    "unsafe_file_type",
                    "filesystem_read_error",
                    "file_changed_during_snapshot",
                    "fixture_unavailable",
                    "fixture_root_unsafe",
                }
                if reason_code not in allowed_reasons:
                    return [], "invalid"
                normalized.append({
                    "case_id": record["case_id"],
                    "kind": "workspace_filesystem_unchanged",
                    "status": "unavailable",
                    "reason_code": reason_code,
                })
                continue
            if (
                audit_status != "complete"
                or not isinstance(record.get("unchanged"), bool)
                or not isinstance(record.get("before_sha256"), str)
                or re.fullmatch(r"[a-fA-F0-9]{64}", record["before_sha256"]) is None
                or not isinstance(record.get("after_sha256"), str)
                or re.fullmatch(r"[a-fA-F0-9]{64}", record["after_sha256"]) is None
                or not isinstance(record.get("changed_entry_count"), int)
                or isinstance(record.get("changed_entry_count"), bool)
                or not 0 <= record["changed_entry_count"] <= 1024
                or not isinstance(record.get("changed_paths_sha256"), str)
                or re.fullmatch(r"[a-fA-F0-9]{64}", record["changed_paths_sha256"]) is None
                or not isinstance(record.get("entry_count"), int)
                or isinstance(record.get("entry_count"), bool)
                or not 0 <= record["entry_count"] <= 1024
                or not isinstance(record.get("bytes_hashed"), int)
                or isinstance(record.get("bytes_hashed"), bool)
                or not 0 <= record["bytes_hashed"] <= 16 * 1024 * 1024
            ):
                return [], "invalid"
            unchanged = record["changed_entry_count"] == 0
            if (
                unchanged != record["unchanged"]
                or (unchanged and record["before_sha256"] != record["after_sha256"])
                or (not unchanged and record["before_sha256"] == record["after_sha256"])
            ):
                return [], "invalid"
            normalized.append({
                "case_id": record["case_id"],
                "kind": kind,
                "status": "complete",
                "unchanged": unchanged,
                "before_sha256": record["before_sha256"].lower(),
                "after_sha256": record["after_sha256"].lower(),
                "changed_entry_count": record["changed_entry_count"],
                "changed_paths_sha256": record["changed_paths_sha256"].lower(),
                "entry_count": record["entry_count"],
                "bytes_hashed": record["bytes_hashed"],
            })
        else:
            return [], "invalid"
    return normalized, "complete" if seen == expected else "partial"


def _validated_coverage_trace(
    workspace: Path,
    relative_path: str,
    expected_case_ids: list[str],
    expected_target_file: str,
    expected_target_sha256: str,
) -> tuple[dict[str, Any] | None, str]:
    """Load only the bounded, hash-only line trace written by the harness."""
    if not isinstance(relative_path, str):
        return None, "invalid_path"
    relative = PurePosixPath(relative_path)
    if (
        relative.is_absolute()
        or relative.as_posix() != relative_path
        or len(relative.parts) != 2
        or relative.parts[0] != "unai_generated_tests"
        or relative.suffix != ".json"
        or not relative.name.endswith(".coverage.json")
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        return None, "invalid_path"
    expected = {
        item for item in expected_case_ids
        if isinstance(item, str) and item
    }
    if not expected or len(expected) > 20:
        return None, "invalid_case_ids"
    workspace_root = workspace.resolve()
    trace_directory = workspace / relative.parts[0]
    if trace_directory.is_symlink() or not trace_directory.is_dir():
        return None, "unsafe_directory"
    try:
        resolved_directory = trace_directory.resolve(strict=True)
        if not resolved_directory.is_relative_to(workspace_root):
            return None, "unsafe_directory"
        trace_path = trace_directory / relative.parts[1]
        if trace_path.is_symlink() or not trace_path.is_file():
            return None, "missing"
        resolved_trace_path = trace_path.resolve(strict=True)
        if not resolved_trace_path.is_relative_to(resolved_directory):
            return None, "unsafe_path"
        if resolved_trace_path.stat().st_size > 262_144:
            return None, "oversized"
        raw = resolved_trace_path.read_text(encoding="utf-8")
        payload = json.loads(raw)
    except (OSError, UnicodeError, ValueError, RecursionError):
        return None, "invalid"
    if (
        not isinstance(payload, dict)
        or payload.get("schema") != "unai.coverage-trace.v1"
        or payload.get("target_file") != expected_target_file
        or not isinstance(payload.get("target_sha256"), str)
        or payload["target_sha256"].lower() != expected_target_sha256.lower()
    ):
        return None, "invalid"

    def normalize_lines(value: Any, limit: int) -> list[int] | None:
        if not isinstance(value, list) or len(value) > limit:
            return None
        normalized: set[int] = set()
        for item in value:
            if (
                not isinstance(item, int)
                or isinstance(item, bool)
                or not 1 <= item <= 1_000_000
            ):
                return None
            normalized.add(item)
        return sorted(normalized)

    executed_lines = normalize_lines(payload.get("executed_lines"), 5_000)
    if executed_lines is None:
        return None, "invalid"
    case_lines = payload.get("case_lines")
    if not isinstance(case_lines, dict) or len(case_lines) > len(expected):
        return None, "invalid"
    normalized_cases: dict[str, list[int]] = {}
    total_case_lines = 0
    for case_id, lines in case_lines.items():
        if case_id not in expected:
            return None, "invalid"
        normalized_lines = normalize_lines(lines, 5_000)
        if normalized_lines is None:
            return None, "invalid"
        total_case_lines += len(normalized_lines)
        if total_case_lines > 20_000:
            return None, "invalid"
        normalized_cases[case_id] = normalized_lines
    trace_sha256 = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "schema": "unai.coverage-trace.v1",
        "target_file": expected_target_file,
        "target_sha256": expected_target_sha256.lower(),
        "executed_lines": executed_lines,
        "case_lines": normalized_cases,
        "trace_sha256": trace_sha256,
    }, "complete"


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
        evidence_source: str = "docker-sandbox",
        evidence_metadata: dict[str, Any] | None = None,
        artifact_ref: str | None = None,
    ) -> TaskTestRunResponse:
        now = datetime.now(timezone.utc).isoformat()
        stdout_bytes = stdout.encode("utf-8", errors="replace")
        stderr_bytes = stderr.encode("utf-8", errors="replace")
        metadata: dict[str, Any] = {
            "command": COMMAND,
            "exit_code": exit_code,
            "timed_out": timed_out,
            "sandboxed": sandboxed,
            "staged_files": staged.files if staged else 0,
            "staged_bytes": staged.bytes_copied if staged else 0,
            "skipped_files": staged.skipped_files if staged else 0,
            "stdout_sha256": hashlib.sha256(stdout_bytes).hexdigest(),
            "stderr_sha256": hashlib.sha256(stderr_bytes).hexdigest(),
        }
        if error:
            metadata["error"] = error
        if evidence_metadata:
            metadata.update(evidence_metadata)
        if evidence_source in {"grounded-oracle-sandbox", "grounded-oracle-shrink-sandbox"}:
            markers: list[dict[str, Any]] = []
            captured_output = "\n".join([stdout, stderr])
            for match in re.findall(r"UNAI_COUNTEREXAMPLE:(\{[^\r\n]*\})", captured_output):
                try:
                    marker = json.loads(match)
                except json.JSONDecodeError:
                    continue
                case_id = marker.get("case_id")
                if isinstance(case_id, str):
                    normalized_marker: dict[str, Any] = {
                        "case_id": case_id[:80],
                        "actual_type": str(marker.get("actual_type", "unknown"))[:120],
                        "actual_summary": _redact_preview(
                            str(marker.get("actual_summary", "")), 500
                        ),
                    }
                    for digest_field in (
                        "actual_sha256",
                        "exception_message_sha256",
                        "replay_trace_sha256",
                        "filesystem_before_sha256",
                        "filesystem_after_sha256",
                        "filesystem_changed_paths_sha256",
                    ):
                        digest_value = marker.get(digest_field)
                        if isinstance(digest_value, str) and re.fullmatch(r"[a-fA-F0-9]{64}", digest_value):
                            normalized_marker[digest_field] = digest_value.lower()
                    for count_field, maximum in (
                        ("replay_count", 4),
                        ("filesystem_changed_entry_count", 1024),
                        ("filesystem_entry_count", 1024),
                        ("filesystem_bytes_hashed", 16 * 1024 * 1024),
                    ):
                        count_value = marker.get(count_field)
                        minimum = 2 if count_field == "replay_count" else 0
                        if (
                            isinstance(count_value, int)
                            and not isinstance(count_value, bool)
                            and minimum <= count_value <= maximum
                        ):
                            normalized_marker[count_field] = count_value
                    replay_count = marker.get("replay_count")
                    if isinstance(replay_count, int) and not isinstance(replay_count, bool) and 2 <= replay_count <= 4:
                        normalized_marker["replay_count"] = replay_count
                    target_frame = marker.get("target_frame")
                    if isinstance(target_frame, dict):
                        line_number = target_frame.get("line")
                        function_name = target_frame.get("function")
                        if isinstance(line_number, int) and not isinstance(line_number, bool) and line_number > 0:
                            normalized_marker["target_line"] = line_number
                        if isinstance(function_name, str):
                            normalized_marker["target_function"] = function_name[:128]
                    markers.append(normalized_marker)
            metadata["counterexample_markers"] = markers
        if evidence_source == MUTATION_EVIDENCE_SOURCE and status in {"passed", "failed"}:
            # For a mutant run, a failing pytest suite means the mutant was killed.
            killed = status == "failed"
            metadata["mutation_killed"] = killed
            mutant_id = str(metadata.get("mutant_id", "unknown"))[:32]
            claim = (
                f"Mutant {mutant_id} was killed by the grounded oracle suite."
                if killed
                else f"Mutant {mutant_id} survived the grounded oracle suite."
            )
            passed = killed
        evidence = EvidenceRecord(
            evidence_id=uuid4().hex,
            task_id=snapshot.task_id,
            kind="execution",
            source=evidence_source,
            claim=claim,
            passed=passed,
            artifact_ref=artifact_ref or f"task:{snapshot.task_id}/python-tests",
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
            stdout_preview=_redact_preview(
                stdout, limit=10_000 if evidence_source == "grounded-oracle-sandbox" else 2_000
            ),
            stderr_preview=_redact_preview(
                stderr, limit=10_000 if evidence_source == "grounded-oracle-sandbox" else 2_000
            ),
            evidence=evidence,
        )

    def run_generated_test(
        self,
        task_id: str,
        workspace_root: Path,
        generated_test_path: str,
        generated_test_content: str,
        evidence_metadata: dict[str, Any],
        *,
        evidence_source: str = "grounded-oracle-sandbox",
        source_overrides: dict[str, str] | None = None,
    ) -> TaskTestRunResponse:
        """Run one server-generated oracle test alongside the filtered workspace copy."""
        if evidence_source not in GENERATED_TEST_EVIDENCE_SOURCES:
            raise ValueError("Unsupported generated-test evidence source.")
        return self.run(
            task_id,
            workspace_root,
            generated_test=(generated_test_path, generated_test_content),
            evidence_source=evidence_source,
            evidence_metadata=evidence_metadata,
            source_overrides=source_overrides,
            artifact_ref=f"task:{task_id}/grounded-oracle-tests",
        )

    def run(
        self,
        task_id: str,
        workspace_root: Path,
        *,
        generated_test: tuple[str, str] | None = None,
        evidence_source: str = "docker-sandbox",
        evidence_metadata: dict[str, Any] | None = None,
        source_overrides: dict[str, str] | None = None,
        artifact_ref: str | None = None,
    ) -> TaskTestRunResponse:
        if source_overrides is not None and evidence_source != MUTATION_EVIDENCE_SOURCE:
            raise ValueError("Source overrides are allowed only for mutation evidence.")
        normalized_overrides = _validate_source_overrides(source_overrides)
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
                effective_evidence_metadata = dict(evidence_metadata or {})
                if normalized_overrides:
                    override_hashes: dict[str, str] = {}
                    for relative_path, override_content in normalized_overrides.items():
                        override_target = stage_path.joinpath(
                            *PurePosixPath(relative_path).parts
                        )
                        if override_target.is_symlink() or not override_target.is_file():
                            raise WorkspaceStagingError(
                                "A mutation source override target is missing or unsafe."
                            )
                        resolved_target = override_target.resolve(strict=True)
                        if not resolved_target.is_relative_to(stage_path.resolve()):
                            raise WorkspaceStagingError(
                                "A mutation source override escaped the staging workspace."
                            )
                        override_target.write_text(
                            override_content, encoding="utf-8", newline=""
                        )
                        override_hashes[relative_path] = hashlib.sha256(
                            override_content.encode("utf-8")
                        ).hexdigest()
                        staged = StagedWorkspace(
                            staged.files,
                            staged.bytes_copied
                            + len(override_content.encode("utf-8")),
                            staged.skipped_files,
                        )
                    effective_evidence_metadata["source_override_sha256"] = override_hashes
                if generated_test is not None:
                    relative_text, generated_content = generated_test
                    relative = PurePosixPath(relative_text)
                    if (
                        relative.is_absolute()
                        or len(relative.parts) != 2
                        or relative.parts[0] != "unai_generated_tests"
                        or relative.suffix != ".py"
                        or any(part in {"", ".", ".."} for part in relative.parts)
                        or len(generated_content.encode("utf-8")) > 300_000
                    ):
                        raise WorkspaceStagingError("Generated test path or size is outside the allowed boundary.")
                    generated_path = stage_path.joinpath(*relative.parts)
                    generated_path.parent.mkdir(parents=True, exist_ok=True)
                    with generated_path.open("x", encoding="utf-8", newline="") as handle:
                        handle.write(generated_content)
                    staged = StagedWorkspace(
                        staged.files + 1,
                        staged.bytes_copied + len(generated_content.encode("utf-8")),
                        staged.skipped_files,
                    )
                external_fixture_case_ids = effective_evidence_metadata.get(
                    "external_fixture_case_ids", []
                )
                if external_fixture_case_ids:
                    result = self.sandbox.run(
                        stage_path,
                        COMMAND,
                        external_side_effect_fixture=True,
                    )
                else:
                    result = self.sandbox.run(stage_path, COMMAND)
                stdout = result.stdout or ""
                stderr = result.stderr or ""
                external_fixture_case_ids = effective_evidence_metadata.get(
                    "external_fixture_case_ids", []
                )
                if external_fixture_case_ids:
                    # Missing or invalid trace plumbing must never downgrade a
                    # requested external audit into an ordinary passing test.
                    effective_evidence_metadata["external_fixture_audit_status"] = "unavailable"
                    effective_evidence_metadata["external_fixture_audit_unavailable_case_ids"] = sorted(
                        set(external_fixture_case_ids)
                    )
                replay_trace_relative_path = effective_evidence_metadata.get("replay_trace_relative_path")
                if replay_trace_relative_path is not None:
                    generated_relative = PurePosixPath(relative_text)
                    expected_trace_path = generated_relative.with_suffix(".replay.json").as_posix()
                    if replay_trace_relative_path != expected_trace_path:
                        effective_evidence_metadata["replay_trace_records"] = []
                        effective_evidence_metadata["replay_trace_status"] = "invalid_path"
                    else:
                        replay_records, replay_status = _validated_replay_trace(
                            stage_path,
                            replay_trace_relative_path,
                            effective_evidence_metadata.get("trace_case_ids", []),
                        )
                        effective_evidence_metadata["replay_trace_records"] = replay_records
                        effective_evidence_metadata["replay_trace_status"] = replay_status
                        filesystem_case_ids = effective_evidence_metadata.get("filesystem_case_ids", [])
                        if filesystem_case_ids:
                            filesystem_records = {
                                record["case_id"]: record
                                for record in replay_records
                                if record.get("kind") == "workspace_filesystem_unchanged"
                            }
                            effective_evidence_metadata["filesystem_audit_status"] = (
                                "complete"
                                if set(filesystem_records) == set(filesystem_case_ids)
                                and all(record.get("status") == "complete" for record in filesystem_records.values())
                                else "unavailable"
                            )
                            effective_evidence_metadata["filesystem_audit_unavailable_case_ids"] = sorted(
                                case_id
                                for case_id in filesystem_case_ids
                                if filesystem_records.get(case_id, {}).get("status") != "complete"
                            )
                        external_fixture_case_ids = effective_evidence_metadata.get(
                            "external_fixture_case_ids", []
                        )
                        if external_fixture_case_ids:
                            external_fixture_records = {
                                record["case_id"]: record
                                for record in replay_records
                                if record.get("kind") == "external_fixture_filesystem_unchanged"
                            }
                            effective_evidence_metadata["external_fixture_audit_status"] = (
                                "complete"
                                if set(external_fixture_records) == set(external_fixture_case_ids)
                                and all(record.get("status") == "complete" for record in external_fixture_records.values())
                                else "unavailable"
                            )
                            effective_evidence_metadata["external_fixture_audit_unavailable_case_ids"] = sorted(
                                case_id
                                for case_id in external_fixture_case_ids
                                if external_fixture_records.get(case_id, {}).get("status") != "complete"
                            )
                coverage_trace_relative_path = effective_evidence_metadata.get(
                    "coverage_trace_relative_path"
                )
                if coverage_trace_relative_path is not None and generated_test is not None:
                    generated_relative = PurePosixPath(relative_text)
                    expected_trace_path = generated_relative.with_suffix(
                        ".coverage.json"
                    ).as_posix()
                    if coverage_trace_relative_path != expected_trace_path:
                        effective_evidence_metadata["coverage_trace"] = None
                        effective_evidence_metadata["coverage_trace_status"] = "invalid_path"
                    else:
                        coverage_trace, coverage_status = _validated_coverage_trace(
                            stage_path,
                            coverage_trace_relative_path,
                            effective_evidence_metadata.get("coverage_case_ids", []),
                            effective_evidence_metadata.get("target_file", ""),
                            effective_evidence_metadata.get("target_sha256", ""),
                        )
                        effective_evidence_metadata["coverage_trace"] = coverage_trace
                        effective_evidence_metadata["coverage_trace_status"] = coverage_status
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
                        evidence_source=evidence_source,
                        evidence_metadata=effective_evidence_metadata,
                        artifact_ref=artifact_ref,
                    )
                if (
                    result.exit_code == 0
                    and not result.timed_out
                    and effective_evidence_metadata.get("filesystem_audit_status") == "unavailable"
                ):
                    return self._response(
                        snapshot,
                        "unavailable",
                        None,
                        "Workspace filesystem audit exceeded its safe snapshot budget or could not read the disposable workspace; no unchanged verdict is available.",
                        exit_code=result.exit_code,
                        timed_out=result.timed_out,
                        staged=staged,
                        stdout=stdout,
                        stderr=stderr,
                        error="workspace_filesystem_audit_unavailable",
                        sandboxed=True,
                        evidence_source=evidence_source,
                        evidence_metadata=effective_evidence_metadata,
                        artifact_ref=artifact_ref,
                    )
                if (
                    result.exit_code == 0
                    and not result.timed_out
                    and effective_evidence_metadata.get("external_fixture_audit_status") == "unavailable"
                ):
                    return self._response(
                        snapshot,
                        "unavailable",
                        None,
                        "External side-effect fixture audit could not be completed within its safe snapshot budget; no verdict is available.",
                        exit_code=result.exit_code,
                        timed_out=result.timed_out,
                        staged=staged,
                        stdout=stdout,
                        stderr=stderr,
                        error="external_fixture_audit_unavailable",
                        sandboxed=True,
                        evidence_source=evidence_source,
                        evidence_metadata=effective_evidence_metadata,
                        artifact_ref=artifact_ref,
                    )
                if result.exit_code == 125:
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
                        evidence_source=evidence_source,
                        evidence_metadata=effective_evidence_metadata,
                        artifact_ref=artifact_ref,
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
                    evidence_source=evidence_source,
                    evidence_metadata=effective_evidence_metadata,
                    artifact_ref=artifact_ref,
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
                evidence_source=evidence_source,
                evidence_metadata=evidence_metadata,
                artifact_ref=artifact_ref,
            )