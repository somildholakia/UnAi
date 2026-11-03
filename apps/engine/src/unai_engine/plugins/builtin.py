from pathlib import Path

from .contracts import PluginRegistry
from ..cognition.rule_microcore import RuleMicrocore
from ..indexing.filesystem import FilesystemIndex
from ..indexing.code_graph import PythonCodeGraphIndexer
from ..memory.trajectory import SQLiteTrajectoryMemory
from ..search.portfolio import BoundedPortfolio
from ..verification.static import StaticEvidenceVerifier


def build_registry(code_graph_db_path: str | Path | None = None) -> PluginRegistry:
    registry = PluginRegistry()
    registry.register("cognition", RuleMicrocore())
    registry.register("indexing", FilesystemIndex())
    registry.register("indexing", PythonCodeGraphIndexer(cache_path=code_graph_db_path))
    registry.register("memory", SQLiteTrajectoryMemory())
    registry.register("search", BoundedPortfolio())
    registry.register("verification", StaticEvidenceVerifier())
    return registry
