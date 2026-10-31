from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


class SandboxUnavailable(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    command: str
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False


class DockerSandbox:
    """A deliberately narrow Docker runner. There is no host-shell fallback."""

    def __init__(self, image: str = "python:3.12-slim", timeout_seconds: int = 120) -> None:
        self.image = image
        self.timeout_seconds = timeout_seconds

    def run(self, workspace: Path, command: str) -> ExecutionResult:
        docker = shutil.which("docker")
        if not docker:
            raise SandboxUnavailable("Docker is unavailable; unAI will not execute commands on the host.")
        root = workspace.resolve()
        if not root.is_dir():
            raise ValueError("Workspace must be an existing directory.")
        if not command.strip():
            raise ValueError("Command must not be empty.")

        args = [
            docker, "run", "--rm", "--pull=never",
            "--network=none", "--read-only", "--pids-limit=128",
            "--memory=1g", "--cpus=1", "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
            "-v", f"{root}:/workspace:rw", "-w", "/workspace",
            self.image, "sh", "-lc", command,
        ]
        try:
            completed = subprocess.run(
                args,
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
            return ExecutionResult(
                command=command,
                exit_code=completed.returncode,
                stdout=completed.stdout[-20_000:],
                stderr=completed.stderr[-20_000:],
            )
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            return ExecutionResult(command, 124, stdout[-20_000:], stderr[-20_000:], timed_out=True)
