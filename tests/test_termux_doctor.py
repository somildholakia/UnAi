from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


REPOSITORY = Path(__file__).resolve().parents[1]
MIN_MODEL_BYTES = 1_000_000_000


@pytest.fixture
def termux_install(tmp_path: Path) -> tuple[Path, dict[str, str], Path]:
    """Create a sparse, mocked Termux installation without downloading model weights."""
    root = tmp_path / "unai"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(
        REPOSITORY / "scripts" / "termux-doctor.sh",
        scripts / "termux-doctor.sh",
    )
    shutil.copy2(
        REPOSITORY / "scripts" / "termux-supervise.sh",
        scripts / "termux-supervise.sh",
    )
    for helper in ("termux-service.sh", "termux-control.sh"):
        shutil.copy2(REPOSITORY / "scripts" / helper, scripts / helper)

    engine_bin = root / "apps" / "engine" / ".venv" / "bin"
    web_bin = root / "apps" / "web" / "node_modules" / ".bin"
    engine_bin.mkdir(parents=True)
    web_bin.mkdir(parents=True)
    (engine_bin / "uvicorn").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (web_bin / "vite").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (engine_bin / "uvicorn").chmod(0o755)
    (web_bin / "vite").chmod(0o755)

    home = tmp_path / "home"
    prefix = tmp_path / "termux-prefix"
    llama_server = home / "src" / "llama.cpp" / "build" / "bin" / "llama-server"
    model = home / "models" / "unai" / "model.gguf"
    llama_server.parent.mkdir(parents=True)
    model.parent.mkdir(parents=True)
    llama_server.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    llama_server.chmod(0o755)
    with model.open("wb") as handle:
        handle.truncate(MIN_MODEL_BYTES)
    prefix.mkdir()

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    curl = fake_bin / "curl"
    curl.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
url="${*: -1}"
if [[ -n "${UNAI_TEST_FAIL_URL:-}" && "$url" == "$UNAI_TEST_FAIL_URL" ]]; then
  exit 7
fi
if [[ "$url" == */v1/chat/completions ]]; then
  if [[ "${UNAI_TEST_EMPTY_CHAT:-0}" == "1" ]]; then
    printf '%s' '{"choices":[{"message":{"content":""}}]}'
  else
    printf '%s' '{"choices":[{"message":{"content":"READY"}}]}'
  fi
fi
""",
        encoding="utf-8",
    )
    curl.chmod(0o755)
    for command in ("node", "npm", "tmux"):
        stub = fake_bin / command
        stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        stub.chmod(0o755)
    python_link = fake_bin / "python"
    python_link.symlink_to(sys.executable)

    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "PREFIX": str(prefix),
            "PATH": f"{fake_bin}{os.pathsep}{env.get('PATH', '')}",
            "UNAI_LLAMA_CPP_DIR": str(home / "src" / "llama.cpp"),
            "UNAI_MODEL_DIR": str(model.parent),
            "UNAI_MODEL_FILE": str(model),
            "UNAI_PROBE_TIMEOUT_SECONDS": "1",
            "UNAI_LOG_DIR": str(home / ".local" / "state" / "unai"),
            "UNAI_SMOKE_TIMEOUT_SECONDS": "5",
        }
    )
    return scripts / "termux-doctor.sh", env, model


def run_doctor(
    script: Path, env: dict[str, str], *arguments: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(script), *arguments],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )


def test_termux_doctor_passes_install_and_all_service_probes(
    termux_install: tuple[Path, dict[str, str], Path],
) -> None:
    script, env, _model = termux_install

    result = run_doctor(script, env)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "[ok] llama-server executable found" in result.stdout
    assert "[ok] Model server reachable" in result.stdout
    assert "[ok] Engine API reachable" in result.stdout
    assert "[ok] Web UI reachable" in result.stdout
    assert "All requested checks passed." in result.stdout


def test_termux_doctor_smoke_chat_requires_non_empty_completion(
    termux_install: tuple[Path, dict[str, str], Path],
) -> None:
    script, env, _model = termux_install

    result = run_doctor(script, env, "--smoke-chat")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "[ok] local model returned a non-empty completion" in result.stdout


def test_termux_doctor_rejects_empty_model_completion(
    termux_install: tuple[Path, dict[str, str], Path],
) -> None:
    script, env, _model = termux_install
    env["UNAI_TEST_EMPTY_CHAT"] = "1"

    result = run_doctor(script, env, "--smoke-chat")

    assert result.returncode == 1
    assert "completion payload was invalid or empty" in result.stdout


def test_termux_doctor_reports_unreachable_engine(
    termux_install: tuple[Path, dict[str, str], Path],
) -> None:
    script, env, _model = termux_install
    env["UNAI_TEST_FAIL_URL"] = "http://127.0.0.1:8000/api/health"

    result = run_doctor(script, env)

    assert result.returncode == 1
    assert "[FAIL] Engine API unreachable" in result.stdout


def test_termux_doctor_rejects_partial_model_download(
    termux_install: tuple[Path, dict[str, str], Path],
) -> None:
    script, env, model = termux_install
    with model.open("wb") as handle:
        handle.write(b"partial")

    result = run_doctor(script, env)

    assert result.returncode == 1
    assert "model file is incomplete" in result.stdout

def test_termux_doctor_includes_recent_log_when_a_service_is_unreachable(
    termux_install: tuple[Path, dict[str, str], Path],
) -> None:
    script, env, _model = termux_install
    log_dir = Path(env["UNAI_LOG_DIR"])
    log_dir.mkdir(parents=True)
    (log_dir / "engine.log").write_text(
        "engine supervisor started\\nengine crashed while loading configuration\\n",
        encoding="utf-8",
    )
    env["UNAI_TEST_FAIL_URL"] = "http://127.0.0.1:8000/api/health"

    result = run_doctor(script, env)

    assert result.returncode == 1
    assert "Recent engine supervisor log:" in result.stdout
    assert "engine crashed while loading configuration" in result.stdout

