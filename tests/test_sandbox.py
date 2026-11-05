from pathlib import Path
import os
import subprocess

import pytest

from unai_engine.runtime.sandbox import DockerSandbox, SandboxUnavailable


def test_sandbox_refuses_host_execution_when_docker_is_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("unai_engine.runtime.sandbox.shutil.which", lambda _: None)
    runner = DockerSandbox()
    with pytest.raises(SandboxUnavailable, match="will not execute commands on the host"):
        runner.run(tmp_path, "echo should-not-run")


def test_sandbox_defaults_to_unai_pytest_image() -> None:
    runner = DockerSandbox()
    assert runner.image == "unai-python-test:latest"


def test_external_side_effect_fixture_is_opt_in_ephemeral_and_non_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(args, 0, "ok", "")

    monkeypatch.setattr("unai_engine.runtime.sandbox.shutil.which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr("unai_engine.runtime.sandbox.subprocess.run", fake_run)

    result = DockerSandbox().run(
        tmp_path,
        "python -m pytest -q",
        external_side_effect_fixture=True,
    )

    assert result.exit_code == 0
    args = captured["args"]
    assert "--tmpfs" in args
    assert "/unai_side_effects:rw,noexec,nosuid,nodev,size=16m" in args
    assert "UNAI_SIDE_EFFECTS_ROOT=/unai_side_effects" in args
    assert "--network=none" in args
    assert "--read-only" in args
    assert "--cap-drop=ALL" in args
    if os.name == "posix":
        assert args[args.index("--user") + 1] == f"{os.getuid()}:{os.getgid()}"


def test_external_side_effect_fixture_is_absent_from_default_sandbox(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        return subprocess.CompletedProcess(args, 0, "ok", "")

    monkeypatch.setattr("unai_engine.runtime.sandbox.shutil.which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr("unai_engine.runtime.sandbox.subprocess.run", fake_run)

    DockerSandbox().run(tmp_path, "python -m pytest -q")

    assert "/unai_side_effects:rw,noexec,nosuid,nodev,size=16m" not in captured["args"]
    assert "UNAI_SIDE_EFFECTS_ROOT=/unai_side_effects" not in captured["args"]