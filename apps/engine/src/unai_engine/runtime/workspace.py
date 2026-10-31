from __future__ import annotations

from pathlib import Path


class WorkspaceError(ValueError):
    pass


def resolve_workspace_path(root: Path, relative_path: str) -> Path:
    """Resolve a user/model-supplied relative path without allowing root escape."""
    if not relative_path or "\x00" in relative_path:
        raise WorkspaceError("A non-empty, valid relative path is required.")
    candidate = (root.resolve() / relative_path).resolve()
    base = root.resolve()
    try:
        candidate.relative_to(base)
    except ValueError as exc:
        raise WorkspaceError("Path escapes the selected workspace.") from exc
    return candidate
