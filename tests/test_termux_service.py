from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


REPOSITORY = Path(__file__).resolve().parents[1]
SERVICE_SCRIPT = REPOSITORY / "scripts" / "termux-service.sh"


@pytest.fixture
def service_install(tmp_path: Path) -> tuple[Path, dict[str, str], Path]:
    """Build a fake installation and record the exact command passed to the supervisor."""
    root = tmp_path / "unai test root"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(SERVICE_SCRIPT, scripts / "termux-service.sh")
    supervisor = scripts / "termux-supervise.sh"
    supervisor.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
service="$1"
shift
[[ "$1" == "--" ]] || exit 90
shift
printf 'service=%s\\ncwd=%s\\n' "$service" "$PWD"
for argument in "$@"; do
  printf 'arg=%s\\n' "$argument"
done
""",
        encoding="utf-8",
    )
    supervisor.chmod(0o755)

    engine_bin = root / "apps" / "engine" / ".venv" / "bin"
    web_bin = root / "apps" / "web" / "node_modules" / ".bin"
    engine_bin.mkdir(parents=True)
    web_bin.mkdir(parents=True)
    uvicorn = engine_bin / "uvicorn"
    vite = web_bin / "vite"
    uvicorn.write_text("#!/bin/sh\\nexit 0\\n", encoding="utf-8")
    vite.write_text("#!/bin/sh\\nexit 0\\n", encoding="utf-8")
    uvicorn.chmod(0o755)
    vite.chmod(0o755)

    home = tmp_path / "termux home"
    llama_dir = home / "src" / "llama.cpp"
    model_dir = home / "models" / "unai"
    llama_bin = llama_dir / "build" / "bin" / "llama-server"
    model_file = model_dir / "model.gguf"
    llama_bin.parent.mkdir(parents=True)
    model_dir.mkdir(parents=True)
    llama_bin.write_text("#!/bin/sh\\nexit 0\\n", encoding="utf-8")
    llama_bin.chmod(0o755)
    model_file.write_bytes(b"fixture weights")

    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "UNAI_LLAMA_CPP_DIR": str(llama_dir),
            "UNAI_MODEL_DIR": str(model_dir),
            "UNAI_MODEL_FILE": str(model_file),
            "UNAI_LLAMA_SERVER_BIN": str(llama_bin),
            "UNAI_CPU_THREADS": "6",
        }
    )
    return scripts / "termux-service.sh", env, root


def run_service(
    script: Path,
    env: dict[str, str],
    service: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(script), service],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )


def _arguments(result: subprocess.CompletedProcess[str]) -> list[str]:
    return [
        line.removeprefix("arg=")
        for line in result.stdout.splitlines()
        if line.startswith("arg=")
    ]


def test_model_service_uses_local_runtime_model_and_configured_threads(service_install) -> None:
    script, env, _root = service_install
    result = run_service(script, env, "model")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "service=model" in result.stdout
    assert _arguments(result) == [
        env["UNAI_LLAMA_SERVER_BIN"],
        "-m",
        env["UNAI_MODEL_FILE"],
        "--host",
        "127.0.0.1",
        "--port",
        "8080",
        "--alias",
        "unai-local-coder",
        "--ctx-size",
        "2048",
        "--threads",
        "6",
        "--batch-size",
        "128",
    ]


def test_engine_service_uses_venv_uvicorn_and_expected_working_directory(service_install) -> None:
    script, env, root = service_install
    result = run_service(script, env, "engine")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "service=engine" in result.stdout
    assert "cwd=" + str(root / "apps" / "engine") in result.stdout
    assert _arguments(result) == [
        str(root / "apps" / "engine" / ".venv" / "bin" / "uvicorn"),
        "unai_engine.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        "8000",
    ]


def test_web_service_uses_local_vite_development_server(service_install) -> None:
    script, env, root = service_install
    result = run_service(script, env, "web")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "service=web" in result.stdout
    assert "cwd=" + str(root / "apps" / "web") in result.stdout
    assert _arguments(result) == [
        "npm",
        "run",
        "dev",
        "--",
        "--host",
        "127.0.0.1",
        "--port",
        "5173",
        "--strictPort",
    ]


@pytest.mark.parametrize("service, dependency, message", [
    ("model", "model", "Model runtime or weights are missing"),
    ("engine", "engine", "Engine virtual environment is missing"),
    ("web", "web", "Web dependencies are missing"),
])
def test_service_launcher_fails_closed_when_a_dependency_is_missing(
    service_install, service: str, dependency: str, message: str
) -> None:
    script, env, root = service_install
    if dependency == "model":
        Path(env["UNAI_MODEL_FILE"]).unlink()
    elif dependency == "engine":
        (root / "apps" / "engine" / ".venv" / "bin" / "uvicorn").unlink()
    else:
        (root / "apps" / "web" / "node_modules" / ".bin" / "vite").unlink()

    result = run_service(script, env, service)

    assert result.returncode == 1
    assert message in result.stderr


def test_service_launcher_rejects_unknown_service(service_install) -> None:
    script, env, _root = service_install

    result = run_service(script, env, "database")

    assert result.returncode == 2
    assert "Usage:" in result.stderr
