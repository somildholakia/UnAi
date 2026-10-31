"""Bounded Python AST graph for repository navigation.

The graph stores file/symbol identifiers and source ranges, not source bodies.
It is rebuilt on demand, uses only the Python standard library, and skips generated,
hidden, symlinked, and oversized files.
"""
from __future__ import annotations

import ast
import os
from pathlib import Path
from typing import Any

from .filesystem import IGNORED


MAX_FILES = 1_500
MAX_FILE_BYTES = 300_000
MAX_SYMBOLS = 12_000
MAX_EDGES = 24_000


def _is_test_file(path: Path) -> bool:
    return "tests" in path.parts or path.name.startswith("test_") or path.name.endswith("_test.py")


def _module_name(relative: Path) -> str:
    parts = list(relative.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


class _SymbolVisitor(ast.NodeVisitor):
    def __init__(
        self,
        path: str,
        file_id: str,
        nodes: list[dict[str, Any]],
        edges: list[dict[str, str]],
        limit: int = MAX_SYMBOLS,
    ) -> None:
        self.path = path
        self.file_id = file_id
        self.nodes = nodes
        self.edges = edges
        self.limit = max(0, limit)
        self.scope: list[str] = []
        self.symbol_count = 0

    def _record(self, node: ast.AST, name: str, kind: str) -> None:
        if self.symbol_count >= self.limit:
            return
        line = int(getattr(node, "lineno", 1))
        end_line = int(getattr(node, "end_lineno", line) or line)
        qualified = ".".join([*self.scope, name])
        symbol_id = f"symbol:{self.path}::{qualified}@{line}"
        self.nodes.append({
            "id": symbol_id,
            "type": kind,
            "name": name,
            "qualified_name": qualified,
            "path": self.path,
            "start_line": line,
            "end_line": end_line,
            "is_test": _is_test_file(Path(self.path)),
        })
        parent_id = self.file_id if not self.scope else f"symbol:{self.path}::{'.'.join(self.scope)}@{self._scope_lines[-1]}"
        edge_kind = "defines" if not self.scope else "contains"
        if len(self.edges) < MAX_EDGES:
            self.edges.append({"source": parent_id, "target": symbol_id, "type": edge_kind})
        self.symbol_count += 1

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        parent_scope = list(self.scope)
        parent_lines = list(getattr(self, "_scope_lines", []))
        self._scope_lines = parent_lines
        self._record(node, node.name, "class")
        self.scope.append(node.name)
        self._scope_lines.append(int(getattr(node, "lineno", 1)))
        for child in node.body:
            self.visit(child)
        self.scope = parent_scope
        self._scope_lines = parent_lines

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        kind = "method" if self.scope and self.scope[-1] and any(
            item.get("type") == "class"
            and item.get("path") == self.path
            and item.get("qualified_name") == ".".join(self.scope)
            for item in self.nodes[-min(len(self.nodes), MAX_SYMBOLS):]
        ) else "function"
        parent_scope = list(self.scope)
        parent_lines = list(getattr(self, "_scope_lines", []))
        self._record(node, node.name, kind)
        self.scope.append(node.name)
        self._scope_lines = parent_lines + [int(getattr(node, "lineno", 1))]
        for child in node.body:
            self.visit(child)
        self.scope = parent_scope
        self._scope_lines = parent_lines


class PythonCodeGraphIndexer:
    name = "python-code-graph"

    def _build(self, root: str) -> dict[str, Any]:
        base = Path(root).resolve(strict=True)
        candidates: list[Path] = []
        skipped_large = 0
        walk_truncated = False

        for current, dirs, names in os.walk(base):
            dirs[:] = [
                name for name in dirs
                if name not in IGNORED and not name.startswith(".")
            ]
            for name in names:
                path = Path(current) / name
                if path.is_symlink() or path.suffix.lower() != ".py":
                    continue
                try:
                    size = path.stat().st_size
                    if size > MAX_FILE_BYTES:
                        skipped_large += 1
                        continue
                    relative = path.relative_to(base)
                except (OSError, ValueError):
                    continue
                candidates.append(relative)
                if len(candidates) >= MAX_FILES:
                    walk_truncated = True
                    break
            if walk_truncated:
                break

        module_paths: dict[str, str] = {}
        ambiguous_modules: set[str] = set()
        for relative in candidates:
            module = _module_name(relative)
            if module in module_paths:
                ambiguous_modules.add(module)
            else:
                module_paths[module] = relative.as_posix()
        for module in ambiguous_modules:
            module_paths.pop(module, None)

        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, str]] = []
        errors: list[dict[str, Any]] = []
        file_by_path: dict[str, str] = {}
        external_imports = 0
        parsed_files = 0
        symbol_count = 0

        for relative in candidates:
            rel = relative.as_posix()
            file_id = f"file:{rel}"
            file_by_path[rel] = file_id
            test_file = _is_test_file(relative)
            nodes.append({
                "id": file_id,
                "type": "file",
                "name": relative.name,
                "path": rel,
                "is_test": test_file,
            })

        def add_import(source_id: str, module: str) -> bool:
            target_path = module_paths.get(module)
            if not target_path:
                suffix_matches = {
                    path for name, path in module_paths.items()
                    if name.endswith("." + module)
                }
                if len(suffix_matches) == 1:
                    target_path = next(iter(suffix_matches))
            if not target_path:
                return False
            if len(edges) < MAX_EDGES:
                edge = {
                    "source": source_id,
                    "target": file_by_path.get(target_path, f"file:{target_path}"),
                    "type": "imports",
                }
                if edge not in edges:
                    edges.append(edge)
            return True

        for relative in candidates:
            rel = relative.as_posix()
            file_id = file_by_path[rel]
            full_path = base / relative
            try:
                if full_path.is_symlink():
                    continue
                with full_path.open("rb") as handle:
                    raw_source = handle.read(MAX_FILE_BYTES + 1)
                if len(raw_source) > MAX_FILE_BYTES:
                    skipped_large += 1
                    continue
                source = raw_source.decode("utf-8")
                tree = ast.parse(source, filename=rel)
                parsed_files += 1
            except (OSError, UnicodeError, SyntaxError) as exc:
                errors.append({
                    "path": rel,
                    "kind": type(exc).__name__,
                    "line": getattr(exc, "lineno", None),
                    "message": str(exc)[:240],
                })
                continue

            if symbol_count >= MAX_SYMBOLS:
                break
            visitor = _SymbolVisitor(
                rel, file_id, nodes, edges, limit=MAX_SYMBOLS - symbol_count
            )
            visitor.visit(tree)
            symbol_count += visitor.symbol_count

            current_parts = list(relative.with_suffix("").parts)
            current_package = current_parts if current_parts and current_parts[-1] == "__init__" else current_parts[:-1]

            for item in ast.walk(tree):
                if isinstance(item, ast.Import):
                    for alias in item.names:
                        if not add_import(file_id, alias.name):
                            external_imports += 1
                elif isinstance(item, ast.ImportFrom):
                    base_parts = list(current_package) if item.level else []
                    for _ in range(max(0, item.level - 1)):
                        if base_parts:
                            base_parts.pop()
                    if item.module:
                        base_parts.extend(item.module.split("."))
                    full_module = ".".join(base_parts)
                    resolved = add_import(file_id, full_module) if full_module else False
                    imported_child = False
                    for alias in item.names:
                        if alias.name == "*":
                            continue
                        candidate = ".".join([part for part in (full_module, alias.name) if part])
                        if candidate and add_import(file_id, candidate):
                            imported_child = True
                    if not resolved and not imported_child:
                        external_imports += 1

            if len(edges) >= MAX_EDGES:
                break

        # A test file links to a production file only when the basename match is unique.
        by_stem: dict[str, list[str]] = {}
        for relative in candidates:
            by_stem.setdefault(relative.stem, []).append(relative.as_posix())

        for relative in candidates:
            if not _is_test_file(relative):
                continue
            stem = relative.stem
            target_stem = stem[5:] if stem.startswith("test_") else (
                stem[:-5] if stem.endswith("_test") else ""
            )
            targets = by_stem.get(target_stem, [])
            if len(targets) == 1 and targets[0] != relative.as_posix() and len(edges) < MAX_EDGES:
                edges.append({
                    "source": file_by_path[relative.as_posix()],
                    "target": file_by_path[targets[0]],
                    "type": "tests",
                })

        return {
            "root": str(base),
            "language": "python",
            "nodes": nodes[: MAX_FILES + MAX_SYMBOLS],
            "edges": edges[:MAX_EDGES],
            "errors": errors[:200],
            "summary": {
                "files_discovered": len(candidates),
                "files_parsed": parsed_files,
                "symbols": symbol_count,
                "internal_import_edges": sum(1 for edge in edges if edge["type"] == "imports"),
                "test_edges": sum(1 for edge in edges if edge["type"] == "tests"),
                "external_imports_unresolved": external_imports,
                "parse_errors": len(errors),
                "skipped_large_files": skipped_large,
                "truncated": walk_truncated or symbol_count >= MAX_SYMBOLS or len(edges) >= MAX_EDGES,
                "strategy": "bounded-python-ast-no-source-body-cache",
            },
        }

    async def index(self, root: str) -> dict[str, Any]:
        return self._build(root)

    async def query(self, root: str, query: str, limit: int = 12) -> list[dict[str, Any]]:
        graph = self._build(root)
        terms = [term.casefold() for term in query.split() if len(term) > 1]
        matches: list[dict[str, Any]] = []
        cap = max(1, min(limit, 100))
        for node in graph["nodes"]:
            if node["type"] == "file":
                continue
            haystack = " ".join([
                str(node.get("name", "")),
                str(node.get("qualified_name", "")),
                str(node.get("path", "")),
            ]).casefold()
            if terms and all(term in haystack for term in terms):
                matches.append({
                    "path": node["path"],
                    "name": node.get("qualified_name", node["name"]),
                    "kind": node["type"],
                    "start_line": node["start_line"],
                    "end_line": node["end_line"],
                })
                if len(matches) >= cap:
                    break
        return matches
