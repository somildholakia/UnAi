"""Per-tenant runtime construction and storage namespaces."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from unai_engine.agent.inspection import TaskInspector
from unai_engine.agent.orchestrator import AgentOrchestrator
from unai_engine.brain.vault import MarkdownVault
from unai_engine.indexing.code_graph import PythonCodeGraphIndexer
from unai_engine.plugins.builtin import build_registry
from unai_engine.providers.openai_compat import OpenAICompatibleProvider
from unai_engine.runtime.patches import PatchManager
from unai_engine.runtime.sandbox import DockerSandbox
from unai_engine.runtime.test_runner import IsolatedPythonTestRunner
from unai_engine.verification.grounded import GroundedTestVerifier
from unai_engine.verification.ledger import EvidenceLedger
from unai_engine.verification.probes import BoundaryProbeGenerator


@dataclass(slots=True)
class TenantServices:
    tenant_id: str
    workspace_root: Path
    plugins: object
    brain: MarkdownVault
    agent: AgentOrchestrator
    evidence_ledger: EvidenceLedger
    task_inspector: TaskInspector
    task_test_runner: IsolatedPythonTestRunner
    patch_manager: PatchManager
    probe_generator: BoundaryProbeGenerator
    grounded_test_verifier: GroundedTestVerifier


def build_tenant_services(
    tenant_id: str,
    workspace_root: Path,
    data_root: Path,
    provider: OpenAICompatibleProvider,
    *,
    specialist_models: dict[str, str] | None = None,
    docker_image: str = "unai-python-test:latest",
    sandbox_timeout_seconds: int = 120,
    test_max_files: int = 5000,
    test_max_bytes: int = 50 * 1024 * 1024,
    trusted_oracle_public_keys: dict[str, str] | None = None,
    trusted_oracle_publisher_keys: dict[str, dict] | None = None,
) -> TenantServices:
    """Create physically separate workspace, Brain, evidence and graph-cache state."""
    if re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", tenant_id) is None or tenant_id == "default":
        raise ValueError("A non-reserved lowercase tenant identifier is required.")
    workspace_base = Path(workspace_root).expanduser().resolve()
    data_base = Path(data_root).expanduser().resolve()
    workspace = (workspace_base / tenant_id).resolve()
    data = (data_base / tenant_id).resolve()
    try:
        workspace.relative_to(workspace_base)
        data.relative_to(data_base)
    except ValueError as exc:
        raise ValueError("Tenant storage path escapes its configured root.") from exc
    workspace.mkdir(parents=True, exist_ok=True)
    data.mkdir(parents=True, exist_ok=True)

    registry = build_registry(code_graph_db_path=data / "code-graph.sqlite3")
    vault = MarkdownVault(data / "brain-vault", data / "brain.sqlite3")
    ledger = EvidenceLedger(data / "evidence.sqlite3")
    task_inspector = TaskInspector(registry.indexing["python-code-graph"], ledger)
    task_test_runner = IsolatedPythonTestRunner(
        ledger,
        DockerSandbox(docker_image, sandbox_timeout_seconds),
        max_files=test_max_files,
        max_bytes=test_max_bytes,
    )
    patch_manager = PatchManager(ledger)
    probe_generator = BoundaryProbeGenerator(ledger)
    grounded = GroundedTestVerifier(
        ledger,
        patch_manager,
        task_test_runner,
        trusted_oracle_public_keys=trusted_oracle_public_keys or {},
        trusted_oracle_publisher_keys=trusted_oracle_publisher_keys or {},
    )
    agent = AgentOrchestrator(
        provider,
        context_search=vault.search,
        specialist_models=specialist_models or {},
    )
    return TenantServices(
        tenant_id=tenant_id,
        workspace_root=workspace,
        plugins=registry,
        brain=vault,
        agent=agent,
        evidence_ledger=ledger,
        task_inspector=task_inspector,
        task_test_runner=task_test_runner,
        patch_manager=patch_manager,
        probe_generator=probe_generator,
        grounded_test_verifier=grounded,
    )
