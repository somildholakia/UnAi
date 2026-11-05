from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


REPOSITORY = Path(__file__).resolve().parents[1]


@pytest.fixture
def termux_control(tmp_path: Path) -> tuple[Path, dict[str, str], Path]:
    root = tmp_path / "unai"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    for name in (
        "termux-control.sh",
        "termux-service.sh",
        "termux-supervise.sh",
    ):
        source = REPOSITORY / "scripts" / name
        target = scripts / name
        if source.exists():
            target.write_bytes(source.read_bytes())
        else:
            target.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")

    start_marker = tmp_path / "start-called.txt"
    start_script = scripts / "termux-start.sh"
    start_script.write_text(
        '#!/usr/bin/env bash\necho started >> "$UNAI_TEST_START_MARKER"\n',
        encoding="utf-8",
    )
    start_script.chmod(0o755)

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    tmux = fake_bin / "tmux"
    tmux.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
case "$1" in
  has-session)
    [[ "${UNAI_TEST_HAS_SESSION:-1}" == "1" ]]
    ;;
  list-windows)
    IFS=',' read -r -a windows <<< "${UNAI_TEST_WINDOWS:-model,engine,web}"
    printf '%s\\n' "${windows[@]}"
    ;;
  display-message)
    echo "${UNAI_TEST_PANE_DEAD:-0}"
    ;;
  respawn-pane|new-window|set-window-option)
    printf '%s\\n' "$*" >> "$UNAI_TEST_TMUX_CALLS"
    ;;
  *)
    echo "unexpected tmux command: $*" >&2
    exit 2
    ;;
esac
""",
        encoding="utf-8",
    )
    tmux.chmod(0o755)

    curl = fake_bin / "curl"
    curl.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
url="${*: -1}"
case "$url" in
  http://127.0.0.1:8080/health)
    health="${UNAI_TEST_MODEL_HEALTH:-0}"
    ;;
  http://127.0.0.1:8000/api/health)
    health="${UNAI_TEST_ENGINE_HEALTH:-0}"
    ;;
  http://127.0.0.1:5173/)
    health="${UNAI_TEST_WEB_HEALTH:-0}"
    ;;
  *)
    echo "unexpected health URL: $url" >&2
    exit 2
    ;;
esac
[[ "$health" == "1" ]]
""",
        encoding="utf-8",
    )
    curl.chmod(0o755)

    sleep = fake_bin / "sleep"
    sleep.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    sleep.chmod(0o755)

    calls = tmp_path / "tmux-calls.txt"
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}{os.pathsep}{env.get('PATH', '')}",
            "UNAI_TEST_TMUX_CALLS": str(calls),
            "UNAI_TEST_START_MARKER": str(start_marker),
            "UNAI_TEST_HAS_SESSION": "1",
            "UNAI_TEST_WINDOWS": "model,engine,web",
            "UNAI_TEST_PANE_DEAD": "0",
            "UNAI_TEST_MODEL_HEALTH": "0",
            "UNAI_TEST_ENGINE_HEALTH": "0",
            "UNAI_TEST_WEB_HEALTH": "0",
            "UNAI_RECOVERY_TIMEOUT_SECONDS": "1",
        }
    )
    return scripts / "termux-control.sh", env, calls


def run_control(
    script: Path, env: dict[str, str], *arguments: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(script), *arguments],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )


def test_status_reports_healthy_services_and_live_panes(
    termux_control: tuple[Path, dict[str, str], Path],
) -> None:
    script, env, _calls = termux_control
    env.update(
        {
            "UNAI_TEST_MODEL_HEALTH": "1",
            "UNAI_TEST_ENGINE_HEALTH": "1",
            "UNAI_TEST_WEB_HEALTH": "1",
        }
    )

    result = run_control(script, env, "status")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "tmux session: true" in result.stdout
    assert "[ok] model endpoint healthy; tmux pane: alive" in result.stdout
    assert "[ok] engine endpoint healthy; tmux pane: alive" in result.stdout
    assert "[ok] web endpoint healthy; tmux pane: alive" in result.stdout


def test_recover_never_restarts_a_healthy_service_even_when_pane_is_dead(
    termux_control: tuple[Path, dict[str, str], Path],
) -> None:
    script, env, calls = termux_control
    env["UNAI_TEST_MODEL_HEALTH"] = "1"
    env["UNAI_TEST_PANE_DEAD"] = "1"

    result = run_control(script, env, "recover", "model")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "already healthy; leaving its process untouched" in result.stdout
    assert not calls.exists()


def test_recover_respawns_only_a_dead_pane_and_reports_when_still_down(
    termux_control: tuple[Path, dict[str, str], Path],
) -> None:
    script, env, calls = termux_control
    env["UNAI_TEST_PANE_DEAD"] = "1"

    result = run_control(script, env, "recover", "model")

    assert result.returncode == 1
    call_lines = calls.read_text(encoding="utf-8").splitlines()
    assert sum(line.startswith("respawn-pane ") for line in call_lines) == 1
    assert any(line.startswith("set-window-option ") for line in call_lines)
    assert "still unavailable after 1s" in result.stdout


def test_recover_does_not_duplicate_a_live_supervisor_pane(
    termux_control: tuple[Path, dict[str, str], Path],
) -> None:
    script, env, calls = termux_control
    env["UNAI_TEST_PANE_DEAD"] = "0"

    result = run_control(script, env, "recover", "model")

    assert result.returncode == 1
    assert "tmux pane is alive but endpoint is not ready" in result.stdout
    assert not calls.exists()


def test_recover_creates_a_missing_window_when_no_pane_exists(
    termux_control: tuple[Path, dict[str, str], Path],
) -> None:
    script, env, calls = termux_control
    env["UNAI_TEST_WINDOWS"] = "engine,web"

    result = run_control(script, env, "recover", "model")

    assert result.returncode == 1
    call_lines = calls.read_text(encoding="utf-8").splitlines()
    assert sum(line.startswith("new-window ") for line in call_lines) == 1
    assert any(line.startswith("set-window-option ") for line in call_lines)


def test_missing_session_refuses_full_start_when_any_endpoint_is_alive(
    termux_control: tuple[Path, dict[str, str], Path],
) -> None:
    script, env, calls = termux_control
    env["UNAI_TEST_HAS_SESSION"] = "0"
    env["UNAI_TEST_ENGINE_HEALTH"] = "1"

    result = run_control(script, env, "recover")

    assert result.returncode == 1
    assert "Refusing to launch the entire stack" in result.stdout
    assert not Path(env["UNAI_TEST_START_MARKER"]).exists()
    assert not calls.exists()


def test_missing_session_starts_stack_only_when_all_endpoints_are_down(
    termux_control: tuple[Path, dict[str, str], Path],
) -> None:
    script, env, _calls = termux_control
    env["UNAI_TEST_HAS_SESSION"] = "0"

    result = run_control(script, env, "recover")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "starting the complete local stack" in result.stdout
    assert Path(env["UNAI_TEST_START_MARKER"]).read_text(encoding="utf-8").splitlines() == [
        "started"
    ]
