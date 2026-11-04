from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


REPOSITORY = Path(__file__).resolve().parents[1]
SUPERVISOR = REPOSITORY / "scripts" / "termux-supervise.sh"


def run_supervisor(
    tmp_path: Path,
    command: list[str],
    *,
    max_restarts: int = 5,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(tmp_path / "home"),
            "UNAI_LOG_DIR": str(tmp_path / "logs"),
            "UNAI_MAX_RESTARTS": str(max_restarts),
            "UNAI_RESTART_DELAY_SECONDS": "1",
            "UNAI_MAX_RESTART_DELAY_SECONDS": "1",
            "UNAI_LOG_MAX_BYTES": "1048576",
        }
    )
    return subprocess.run(
        ["bash", str(SUPERVISOR), "fixture", "--", *command],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )


def test_supervisor_does_not_restart_a_clean_exit(tmp_path: Path) -> None:
    attempts = tmp_path / "attempts.txt"
    command = [
        "bash",
        "-c",
        'echo run >> "$1"; exit 0',
        "_",
        str(attempts),
    ]

    result = run_supervisor(tmp_path, command)

    assert result.returncode == 0, result.stdout + result.stderr
    assert attempts.read_text(encoding="utf-8").splitlines() == ["run"]
    assert "process exited cleanly; supervisor stopping" in result.stdout
    assert (tmp_path / "logs" / "fixture.log").exists()


def test_supervisor_recovers_after_transient_process_failures(tmp_path: Path) -> None:
    attempts = tmp_path / "attempts.txt"
    command = [
        "bash",
        "-c",
        (
            'count=0; [[ -f "$1" ]] && count="$(wc -l < "$1")"; '
            'echo attempt >> "$1"; '
            'if (( count < 2 )); then exit 9; fi; '
            'echo service-ready; exit 0'
        ),
        "_",
        str(attempts),
    ]

    result = run_supervisor(tmp_path, command, max_restarts=3)

    assert result.returncode == 0, result.stdout + result.stderr
    assert len(attempts.read_text(encoding="utf-8").splitlines()) == 3
    assert "restart count: 2" in result.stdout
    assert "service-ready" in result.stdout
    assert "process exited cleanly; supervisor stopping" in result.stdout


def test_supervisor_stops_after_the_configured_restart_limit(tmp_path: Path) -> None:
    attempts = tmp_path / "attempts.txt"
    command = [
        "bash",
        "-c",
        'echo attempt >> "$1"; exit 17',
        "_",
        str(attempts),
    ]

    result = run_supervisor(tmp_path, command, max_restarts=2)

    assert result.returncode == 17
    assert len(attempts.read_text(encoding="utf-8").splitlines()) == 3
    assert "restart limit reached after 2 retries" in result.stdout
    assert "last exit code: 17" in result.stdout


def test_supervisor_rotates_an_oversized_log_before_appending(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    old_log = log_dir / "fixture.log"
    old_log.write_text("old-log-" * 10, encoding="utf-8")
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(tmp_path / "home"),
            "UNAI_LOG_DIR": str(log_dir),
            "UNAI_MAX_RESTARTS": "0",
            "UNAI_RESTART_DELAY_SECONDS": "1",
            "UNAI_MAX_RESTART_DELAY_SECONDS": "1",
            "UNAI_LOG_MAX_BYTES": "16",
        }
    )

    result = subprocess.run(
        ["bash", str(SUPERVISOR), "fixture", "--", "true"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert (log_dir / "fixture.log.1").read_text(encoding="utf-8") == "old-log-" * 10
    assert "supervisor started" in (log_dir / "fixture.log").read_text(encoding="utf-8")


def test_supervisor_rejects_invalid_restart_configuration(tmp_path: Path) -> None:
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(tmp_path / "home"),
            "UNAI_LOG_DIR": str(tmp_path / "logs"),
            "UNAI_MAX_RESTARTS": "-1",
        }
    )

    result = subprocess.run(
        ["bash", str(SUPERVISOR), "fixture", "--", "true"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )

    assert result.returncode == 2
    assert "Invalid restart configuration" in result.stdout
