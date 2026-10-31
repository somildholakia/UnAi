"""Approval-gated single-file patch proposals with optimistic concurrency."""
from __future__ import annotations

import difflib
import hashlib
import os
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from uuid import uuid4

from unai_engine.models import (
    EvidenceRecord,
    PatchApplyResponse,
    PatchProposalRequest,
    PatchProposalResponse,
    TaskEvidenceSnapshot,
)
from unai_engine.runtime.workspace import WorkspaceError, resolve_workspace_path
from unai_engine.verification.ledger import EvidenceLedger, EvidenceLedgerError


MAX_SOURCE_BYTES = 1_000_000
PATCHABLE_SUFFIXES = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".css", ".html",
    ".json", ".toml", ".yaml", ".yml", ".md", ".txt", ".sh", ".go", ".rs",
    ".java", ".cpp", ".h", ".hpp", ".sql",
}
SENSITIVE_MARKERS = ("secret", "credential", "password", "token", "private")
SENSITIVE_NAMES = {
    ".env", ".netrc", "id_rsa", "id_ed25519", "credentials.json",
    "service-account.json", "service_account.json", "google-services.json",
}


class PatchConflict(RuntimeError):
    pass


class PatchRejected(PermissionError):
    pass


class PatchManager:
    def __init__(self, ledger: EvidenceLedger) -> None:
        self.ledger = ledger

    @staticmethod
    def _require_permissions(snapshot: TaskEvidenceSnapshot) -> None:
        if snapshot.status != "inspection_complete":
            raise PatchRejected("Task must complete inspection before patch operations.")
        permissions = set(snapshot.contract.permissions)
        required = {"workspace_read", "workspace_write"}
        if not required.issubset(permissions):
            raise TaskRunRejected("This task has not explicitly approved workspace_read and workspace_write.")
        if "destructive_changes" in snapshot.contract.required_permissions and "destructive_changes" not in permissions:
            raise TaskRunRejected("This task also requires explicit destructive_changes approval.")

    @staticmethod
    def _target(workspace_root: Path, workspace: str, file_path: str) -> Path:
        if not file_path or "\x00" in file_path or "\\" in file_path:
            raise PatchConflict("File path must be a clean workspace-relative path.")
        relative = PurePosixPath(file_path)
        if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
            raise PatchConflict("Absolute paths, dot segments, and parent traversal are not allowed.")
        candidate_text = relative.as_posix()
        if relative.name.casefold() in SENSITIVE_NAMES or any(
            marker in relative.name.casefold() for marker in SENSITIVE_MARKERS
        ):
            raise PatchConflict("Sensitive files cannot be patch targets.")
        if Path(candidate_text).suffix.casefold() not in PATCHABLE_SUFFIXES:
            raise PatchConflict("File type is not enabled for patch proposals.")

        try:
            root = resolve_workspace_path(workspace_root, workspace)
            cursor = root
            for part in relative.parts:
                cursor = cursor / part
                if cursor.is_symlink():
                    raise PatchConflict("Symlinked patch paths are not allowed.")
            target = resolve_workspace_path(root, candidate_text)
        except WorkspaceError as exc:
            raise PatchConflict("Patch path escapes the configured workspace.") from exc

        if not root.is_dir() or not target.is_file():
            raise PatchConflict("Patch target must be an existing file inside the workspace.")
        if target.stat().st_size > MAX_SOURCE_BYTES:
            raise PatchConflict("Patch target exceeds the 1 MB source limit.")
        return target

    def propose(
        self,
        task_id: str,
        request: PatchProposalRequest,
        workspace_root: Path,
    ) -> PatchProposalResponse:
        snapshot = self.ledger.get_snapshot(task_id)
        if snapshot is None:
            raise KeyError(task_id)
        self._require_permissions(snapshot)
        target = self._target(workspace_root, snapshot.workspace, request.file_path)

        try:
            original_bytes = target.read_bytes()
            original = original_bytes.decode("utf-8")
        except (OSError, UnicodeError) as exc:
            raise PatchConflict("Patch target must be readable UTF-8 text.") from exc
        actual_sha = hashlib.sha256(original_bytes).hexdigest()
        expected_sha = request.expected_sha256.casefold()
        if actual_sha != expected_sha:
            raise PatchConflict("Target file changed since the supplied base hash.")
        if "\x00" in request.proposed_content:
            raise PatchConflict("Patch content must not contain NUL bytes.")

        proposed_bytes = request.proposed_content.encode("utf-8")
        proposed_sha = hashlib.sha256(proposed_bytes).hexdigest()
        if proposed_sha == actual_sha:
            raise PatchConflict("Proposed content is identical to the current file.")
        proposal_id = uuid4().hex
        created_at = datetime.now(timezone.utc).isoformat()
        diff = "".join(
            difflib.unified_diff(
                original.splitlines(keepends=True),
                request.proposed_content.splitlines(keepends=True),
                fromfile=f"a/{request.file_path}",
                tofile=f"b/{request.file_path}",
            )
        )

        evidence = EvidenceRecord(
            evidence_id=uuid4().hex,
            task_id=task_id,
            kind="patch",
            source="patch-proposal",
            claim=f"Patch proposal for {request.file_path} matches the inspected file hash.",
            passed=True,
            artifact_ref=f"proposal:{proposal_id}",
            metadata={
                "file_path": request.file_path,
                "expected_sha256": actual_sha,
                "proposed_sha256": proposed_sha,
                "description": request.description,
                "diff_sha256": hashlib.sha256(diff.encode("utf-8")).hexdigest(),
            },
            created_at=created_at,
        )
        self.ledger.store_patch_proposal(
            {
                "proposal_id": proposal_id,
                "task_id": task_id,
                "workspace": snapshot.workspace,
                "file_path": request.file_path,
                "expected_sha256": actual_sha,
                "proposed_sha256": proposed_sha,
                "proposed_content": request.proposed_content,
                "description": request.description,
                "created_at": created_at,
            },
            evidence,
        )
        return PatchProposalResponse(
            proposal_id=proposal_id,
            task_id=task_id,
            file_path=request.file_path,
            expected_sha256=actual_sha,
            proposed_sha256=proposed_sha,
            status="pending",
            diff_preview=diff[:5_000],
            created_at=created_at,
        )

    def apply(
        self,
        task_id: str,
        proposal_id: str,
        workspace_root: Path,
    ) -> PatchApplyResponse:
        snapshot = self.ledger.get_snapshot(task_id)
        if snapshot is None:
            raise KeyError(task_id)
        self._require_permissions(snapshot)
        proposal = self.ledger.get_patch_proposal(task_id, proposal_id)
        if proposal is None:
            raise KeyError(proposal_id)
        if proposal["status"] != "pending":
            raise PatchConflict("Patch proposal is not pending.")
        if proposal["workspace"] != snapshot.workspace:
            raise PatchConflict("Proposal workspace no longer matches the task snapshot.")

        target = self._target(workspace_root, snapshot.workspace, proposal["file_path"])
        current_bytes = target.read_bytes()
        current_sha = hashlib.sha256(current_bytes).hexdigest()
        expected_sha = proposal["expected_sha256"]
        proposed_sha = proposal["proposed_sha256"]

        # Recover safely if the atomic file replacement completed but the evidence
        # transaction was interrupted before it could mark the proposal as applied.
        if current_sha != proposed_sha:
            if current_sha != expected_sha:
                raise PatchConflict("Target file changed after proposal creation; refusing to overwrite it.")
            proposed_bytes = proposal["proposed_content"].encode("utf-8")
            if hashlib.sha256(proposed_bytes).hexdigest() != proposed_sha:
                raise PatchConflict("Stored proposal content failed its integrity check.")

            parent = target.parent
            original_mode = stat.S_IMODE(target.stat().st_mode)
            temporary_path: str | None = None
            try:
                descriptor, temporary_path = tempfile.mkstemp(prefix=".unai-patch-", dir=parent)
                os.fchmod(descriptor, original_mode)
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(proposed_bytes)
                    handle.flush()
                    os.fsync(handle.fileno())

                # Re-check immediately before replacement to avoid silently
                # overwriting a concurrent user edit.
                before_replace = target.read_bytes()
                if hashlib.sha256(before_replace).hexdigest() != expected_sha:
                    raise PatchConflict("Target file changed while the patch was being prepared.")
                os.replace(temporary_path, target)
                temporary_path = None
                try:
                    directory_fd = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
                    try:
                        os.fsync(directory_fd)
                    finally:
                        os.close(directory_fd)
                except OSError:
                    # The file has already been atomically replaced; directory fsync
                    # is best-effort on filesystems that do not support it.
                    pass
            finally:
                if temporary_path is not None:
                    try:
                        os.unlink(temporary_path)
                    except OSError:
                        pass

            current_bytes = target.read_bytes()
            current_sha = hashlib.sha256(current_bytes).hexdigest()
            if current_sha != proposed_sha:
                raise PatchConflict("Applied file hash differs from the proposed content hash.")

        applied_at = datetime.now(timezone.utc).isoformat()
        evidence = EvidenceRecord(
            evidence_id=uuid4().hex,
            task_id=task_id,
            kind="patch",
            source="patch-applier",
            claim=f"Approved patch applied to {proposal['file_path']}.",
            passed=True,
            artifact_ref=f"proposal:{proposal_id}",
            metadata={
                "file_path": proposal["file_path"],
                "old_sha256": expected_sha,
                "new_sha256": current_sha,
                "description": proposal["description"],
                "approval": "apply-patch",
            },
            created_at=applied_at,
        )
        try:
            self.ledger.finalize_patch_proposal(
                task_id, proposal_id, current_sha, applied_at, evidence
            )
        except EvidenceLedgerError as exc:
            raise PatchConflict("File replacement finished, but proposal finalization needs recovery.") from exc

        return PatchApplyResponse(
            proposal_id=proposal_id,
            task_id=task_id,
            file_path=proposal["file_path"],
            status="applied",
            old_sha256=expected_sha,
            new_sha256=current_sha,
            applied_at=applied_at,
            evidence=evidence,
        )

