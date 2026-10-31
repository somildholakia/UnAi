from pathlib import Path

import pytest

from unai_engine.runtime.workspace import WorkspaceError, resolve_workspace_path


def test_resolves_file_inside_workspace(tmp_path: Path) -> None:
    target = resolve_workspace_path(tmp_path, "src/main.py")
    assert target == (tmp_path / "src/main.py").resolve()


def test_rejects_parent_traversal(tmp_path: Path) -> None:
    with pytest.raises(WorkspaceError, match="escapes"):
        resolve_workspace_path(tmp_path, "../../etc/passwd")


def test_rejects_absolute_path_outside_workspace(tmp_path: Path) -> None:
    with pytest.raises(WorkspaceError, match="escapes"):
        resolve_workspace_path(tmp_path, "/etc/passwd")


def test_rejects_empty_path(tmp_path: Path) -> None:
    with pytest.raises(WorkspaceError, match="relative path"):
        resolve_workspace_path(tmp_path, "")


def test_rejects_symlink_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    link = tmp_path / "escape"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Symlink creation is not available on this platform")
    with pytest.raises(WorkspaceError, match="escapes"):
        resolve_workspace_path(tmp_path, "escape/file.txt")
