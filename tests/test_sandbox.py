from pathlib import Path

import pytest

from unai_engine.runtime.sandbox import DockerSandbox, SandboxUnavailable


def test_sandbox_refuses_host_execution_when_docker_is_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("unai_engine.runtime.sandbox.shutil.which", lambda _: None)
    runner = DockerSandbox()
    with pytest.raises(SandboxUnavailable, match="will not execute commands on the host"):
        runner.run(tmp_path, "echo should-not-run")
