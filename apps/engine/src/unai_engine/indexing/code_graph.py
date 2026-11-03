"""Bounded Python AST graph with incremental SQLite persistence.

Call edges are conservative static relationships: only unambiguous local or
explicitly imported function/method targets are linked. Dynamic dispatch,
unknown attributes and runtime behavior are not inferred.
"""
from __future__ import annotations

import ast
import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .filesystem import IGNORED
from .graph_store import GraphStoreConflict, SQLiteCodeGraphStore


MAX_FILES = 1_500
MAX_FILE_BYTES = 300_000
MAX_SYMBOLS = 12_000
MAX_EDGES = 24_000
MAX_CALL_SITES_PER_FILE = 100
MAX_CALL_SITES = 12_000
_GRAPH_RECORD_VERSION = 2


def _is_test_file(path: Path) -> bool:
    return "tests" in path.parts or path.name.startswith("test_") or path.name.endswith("_test.py")


def _module_name(relative: Path) -> str:
    parts = list(relative.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _dotted_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _dotted_name(node.value)
        return f"{parent}.{node.attr}" if parent else None
    return None


class _SymbolVisitor(ast.NodeVisitor):
    def __init__(
        self,
        path: str,
        file_id: str,
        nodes: list[dict[str, Any]],
        edges: list[dict[str, str]],
        source_sha256: str,
        limit: int = MAX_SYMBOLS,
    ) -> None:
        self.path = path
        self.file_id = file_id
        self.nodes = nodes
        self.edges = edges
        self.source_sha256 = source_sha256
        self.limit = max(0, limit)
        self.scope: list[str] = []
        self.scope_kinds: list[str] = []
        self._scope_lines: list[int] = []
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
            "source_sha256": self.source_sha256,
        })
        parent_id = self.file_id if not self.scope else (
            f"symbol:{self.path}::{'.'.join(self.scope)}@{self._scope_lines[-1]}"
        )
        edge_kind = "defines" if not self.scope else "contains"
        if len(self.edges) < MAX_EDGES:
            self.edges.append({"source": parent_id, "target": symbol_id, "type": edge_kind})
        self.symbol_count += 1

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        parent_scope = list(self.scope)
        parent_kinds = list(self.scope_kinds)
        parent_lines = list(self._scope_lines)
        self._record(node, node.name, "class")
        self.scope.append(node.name)
        self.scope_kinds.append("class")
        self._scope_lines.append(int(getattr(node, "lineno", 1)))
        for child in node.body:
            self.visit(child)
        self.scope = parent_scope
        self.scope_kinds = parent_kinds
        self._scope_lines = parent_lines

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        kind = "method" if self.scope_kinds and self.scope_kinds[-1] == "class" else "function"
        parent_scope = list(self.scope)
        parent_kinds = list(self.scope_kinds)
        parent_lines = list(self._scope_lines)
        self._record(node, node.name, kind)
        self.scope.append(node.name)
        self.scope_kinds.append("function")
        self._scope_lines.append(int(getattr(node, "lineno", 1)))
        for child in node.body:
            self.visit(child)
        self.scope = parent_scope
        self.scope_kinds = parent_kinds
        self._scope_lines = parent_lines


class _LocalBindingVisitor(ast.NodeVisitor):
    """Collect names bound in one function without entering nested scopes."""

    def __init__(self) -> None:
        self.names: set[str] = set()

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.names.add(node.id)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.names.add(alias.asname or alias.name.split(".")[0])

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if alias.name != "*":
                self.names.add(alias.asname or alias.name)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.names.add(node.name)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.names.add(node.name)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.names.add(node.name)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name:
            self.names.add(node.name)
        if node.type:
            self.visit(node.type)
        for child in node.body:
            self.visit(child)


class _CallSiteVisitor(ast.NodeVisitor):
    """Collect bounded dotted-name calls from the innermost lexical function."""

    def __init__(self, path: str, limit: int = MAX_CALL_SITES_PER_FILE) -> None:
        self.path = path
        self.limit = max(0, limit)
        self.class_stack: list[str] = []
        self.caller_stack: list[str] = []
        self.call_sites: list[dict[str, Any]] = []
        self.truncated = False
        self.bound_stack: list[set[str]] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.class_stack.append(node.name)
        for child in node.body:
            self.visit(child)
        self.class_stack.pop()

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        qualified = ".".join([*self.class_stack, node.name])
        caller = f"symbol:{self.path}::{qualified}@{node.lineno}"
        bound_names = {
            argument.arg
            for argument in (
                node.args.posonlyargs + node.args.args + node.args.kwonlyargs
            )
        }
        if node.args.vararg:
            bound_names.add(node.args.vararg.arg)
        if node.args.kwarg:
            bound_names.add(node.args.kwarg.arg)
        local_bindings = _LocalBindingVisitor()
        for child in node.body:
            local_bindings.visit(child)
        bound_names.update(local_bindings.names)
        self.caller_stack.append(caller)
        self.bound_stack.append(bound_names)
        for child in node.body:
            self.visit(child)
        self.bound_stack.pop()
        self.caller_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_Call(self, node: ast.Call) -> None:
        if self.caller_stack:
            callee = _dotted_name(node.func)
            if callee:
                if len(self.call_sites) < self.limit:
                    self.call_sites.append({
                        "caller_id": self.caller_stack[-1],
                        "callee": callee,
                        "class_name": self.class_stack[-1] if self.class_stack else None,
                        "shadowed_names": sorted(self.bound_stack[-1]),
                        "line": int(getattr(node, "lineno", 1)),
                    })
                else:
                    self.truncated = True
        self.generic_visit(node)


def _import_base(relative: Path, level: int, module: str | None) -> str:
    current_parts = list(relative.with_suffix("").parts)
    current_package = (
        current_parts
        if current_parts and current_parts[-1] == "__init__"
        else current_parts[:-1]
    )
    base_parts = list(current_package) if level else []
    for _ in range(max(0, level - 1)):
        if base_parts:
            base_parts.pop()
    if module:
        base_parts.extend(module.split("."))
    return ".".join(base_parts)


def _import_groups(tree: ast.AST, relative: Path) -> list[list[str]]:
    """Return import statements as alternative local module paths."""
    groups: list[list[str]] = []
    for item in ast.walk(tree):
        if isinstance(item, ast.Import):
            groups.extend([[alias.name] for alias in item.names])
        elif isinstance(item, ast.ImportFrom):
            full_module = _import_base(relative, item.level, item.module)
            candidates = [full_module] if full_module else []
            for alias in item.names:
                if alias.name == "*":
                    continue
                candidate = ".".join([part for part in (full_module, alias.name) if part])
                if candidate and candidate not in candidates:
                    candidates.append(candidate)
            groups.append(candidates)
    return groups


def _import_bindings(tree: ast.AST, relative: Path) -> list[dict[str, str]]:
    """Keep only the syntactic names needed for conservative imported-call linking."""
    bindings: list[dict[str, str]] = []
    for item in tree.body:
        if isinstance(item, ast.Import):
            for alias in item.names:
                local = alias.asname or alias.name.split(".")[0]
                module = alias.name if alias.asname else alias.name.split(".")[0]
                bindings.append({"kind": "module", "local": local, "module": module, "name": ""})
        elif isinstance(item, ast.ImportFrom):
            module = _import_base(relative, item.level, item.module)
            if not module:
                continue
            for alias in item.names:
                if alias.name == "*":
                    continue
                bindings.append({
                    "kind": "symbol",
                    "local": alias.asname or alias.name,
                    "module": module,
                    "name": alias.name,
                })
    return bindings[:2_000]


def _parse_file(relative: Path, raw_source: bytes, source_sha256: str) -> dict[str, Any]:
    rel = relative.as_posix()
    file_id = f"file:{rel}"
    try:
        source = raw_source.decode("utf-8")
    except UnicodeError as exc:
        return {
            "path": rel, "source_sha256": source_sha256, "parse_status": "decode_error",
            "nodes": [], "structural_edges": [], "import_groups": [],
            "call_sites": [], "call_sites_truncated": False, "import_bindings": [],
            "error": {
                "path": rel, "kind": type(exc).__name__, "line": None,
                "message": str(exc)[:240],
            },
        }

    try:
        tree = ast.parse(source, filename=rel)
    except SyntaxError as exc:
        return {
            "path": rel, "source_sha256": source_sha256, "parse_status": "syntax_error",
            "nodes": [], "structural_edges": [], "import_groups": [],
            "call_sites": [], "call_sites_truncated": False, "import_bindings": [],
            "error": {
                "path": rel, "kind": type(exc).__name__, "line": exc.lineno,
                "message": str(exc)[:240],
            },
        }

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, str]] = []
    visitor = _SymbolVisitor(rel, file_id, nodes, edges, source_sha256, limit=MAX_SYMBOLS)
    visitor.visit(tree)
    calls = _CallSiteVisitor(rel)
    calls.visit(tree)
    return {
        "path": rel,
        "source_sha256": source_sha256,
        "parse_status": "parsed",
        "nodes": nodes,
        "structural_edges": edges,
        "import_groups": _import_groups(tree, relative),
        "call_sites": calls.call_sites,
        "call_sites_truncated": calls.truncated,
        "import_bindings": _import_bindings(tree, relative),
        "error": None,
    }


class PythonCodeGraphIndexer:
    name = "python-code-graph"

    def __init__(self, cache_path: str | Path | None = None) -> None:
        self.store = SQLiteCodeGraphStore(cache_path) if cache_path is not None else None

    def _candidates(self, base: Path) -> tuple[list[Path], int, bool]:
        candidates: list[Path] = []
        skipped_large = 0
        walk_truncated = False
        for current, dirs, names in os.walk(base):
            dirs[:] = sorted(
                name for name in dirs
                if name not in IGNORED and not name.startswith(".")
            )
            for name in sorted(names):
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
        return candidates, skipped_large, walk_truncated

    @staticmethod
    def _read_snapshot(path: Path) -> tuple[bytes, int, int]:
        if path.is_symlink():
            raise OSError("Symbolic links are not indexed.")
        before = path.stat()
        if before.st_size > MAX_FILE_BYTES:
            raise OSError("Python file exceeded the configured graph limit during refresh.")
        with path.open("rb") as handle:
            raw = handle.read(MAX_FILE_BYTES + 1)
        after = path.stat()
        if (
            path.is_symlink()
            or before.st_ino != after.st_ino
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or len(raw) != after.st_size
        ):
            raise OSError("Repository file changed during graph refresh; retry the index request.")
        if len(raw) > MAX_FILE_BYTES:
            raise OSError("Python file exceeded the configured graph limit during refresh.")
        return raw, int(after.st_size), int(after.st_mtime_ns)

    @staticmethod
    def _fingerprint(files: dict[str, dict[str, Any]]) -> str:
        digest = hashlib.sha256()
        for path, item in sorted(files.items()):
            digest.update(path.encode("utf-8"))
            digest.update(b"\0")
            digest.update(item["source_sha256"].encode("ascii"))
            digest.update(b"\n")
        return digest.hexdigest()

    @staticmethod
    def _resolve_module(module: str, module_paths: dict[str, str]) -> str | None:
        target = module_paths.get(module)
        if target:
            return target
        suffix_matches = {
            path for name, path in module_paths.items()
            if name.endswith("." + module)
        }
        return next(iter(suffix_matches)) if len(suffix_matches) == 1 else None

    @staticmethod
    def _unique_symbol(
        symbols_by_path: dict[str, dict[str, list[dict[str, Any]]]],
        path: str | None,
        qualified_name: str,
        allowed_types: set[str],
    ) -> dict[str, Any] | None:
        if path is None:
            return None
        matches = [
            item for item in symbols_by_path.get(path, {}).get(qualified_name, [])
            if item.get("type") in allowed_types
        ]
        return matches[0] if len(matches) == 1 else None

    @classmethod
    def _unique_callable(
        cls,
        symbols_by_path: dict[str, dict[str, list[dict[str, Any]]]],
        path: str | None,
        qualified_name: str,
    ) -> dict[str, Any] | None:
        return cls._unique_symbol(
            symbols_by_path, path, qualified_name, {"function", "method"}
        )

    @classmethod
    def _unique_class(
        cls,
        symbols_by_path: dict[str, dict[str, list[dict[str, Any]]]],
        path: str | None,
        qualified_name: str,
    ) -> dict[str, Any] | None:
        return cls._unique_symbol(
            symbols_by_path, path, qualified_name, {"class"}
        )

    def _resolve_call_edges(
        self,
        files: dict[str, dict[str, Any]],
        file_by_path: dict[str, str],
        symbols_by_path: dict[str, dict[str, list[dict[str, Any]]]],
        module_paths: dict[str, str],
        add_edge: Any,
    ) -> tuple[int, int, int]:
        indexed_sites = 0
        unresolved_sites = 0
        instantiation_edges = 0
        indexed_symbol_ids = {
            item["id"]
            for path_symbols in symbols_by_path.values()
            for values in path_symbols.values()
            for item in values
        }
        for source_path, record in files.items():
            bindings = record.get("import_bindings", [])
            for site in record.get("call_sites", []):
                if indexed_sites >= MAX_CALL_SITES:
                    return indexed_sites, unresolved_sites, instantiation_edges
                indexed_sites += 1
                caller_id = site.get("caller_id")
                callee = site.get("callee")
                if not isinstance(caller_id, str) or not isinstance(callee, str):
                    unresolved_sites += 1
                    continue

                target: dict[str, Any] | None = None
                edge_type = "calls"
                class_name = site.get("class_name")
                shadowed_names = set(site.get("shadowed_names", []))
                if callee.startswith(("self.", "cls.")) and isinstance(class_name, str):
                    target = self._unique_callable(
                        symbols_by_path, source_path, f"{class_name}.{callee.split('.', 1)[1]}"
                    )
                elif "." in callee:
                    first, suffix = callee.split(".", 1)
                    # Explicit module aliases, e.g. import service as s; s.save().
                    module_bindings = [
                        item for item in bindings
                        if first not in shadowed_names
                        and item.get("kind") == "module" and item.get("local") == first
                    ]
                    if len(module_bindings) == 1:
                        module_path = self._resolve_module(module_bindings[0]["module"], module_paths)
                        target = self._unique_callable(symbols_by_path, module_path, suffix)
                        if target is None:
                            target = self._unique_class(symbols_by_path, module_path, suffix)
                            if target is not None:
                                edge_type = "instantiates"
                    if target is None and first not in shadowed_names:
                        # Local class-qualified method calls, e.g. Worker.run().
                        target = self._unique_callable(symbols_by_path, source_path, callee)
                    if target is None and first not in shadowed_names:
                        # Imported class aliases, e.g. from worker import Worker as W.
                        symbol_bindings = [
                            item for item in bindings
                            if item.get("kind") == "symbol" and item.get("local") == first
                        ]
                        if len(symbol_bindings) == 1:
                            binding = symbol_bindings[0]
                            module_path = self._resolve_module(binding["module"], module_paths)
                            target = self._unique_callable(
                                symbols_by_path, module_path,
                                ".".join(part for part in (binding["name"], suffix) if part),
                            )
                elif callee not in shadowed_names:
                    target = self._unique_callable(symbols_by_path, source_path, callee)
                    if target is None:
                        target = self._unique_class(symbols_by_path, source_path, callee)
                        if target is not None:
                            edge_type = "instantiates"
                    if target is None:
                        symbol_bindings = [
                            item for item in bindings
                            if item.get("kind") == "symbol" and item.get("local") == callee
                        ]
                        if len(symbol_bindings) == 1:
                            binding = symbol_bindings[0]
                            module_path = self._resolve_module(binding["module"], module_paths)
                            target = self._unique_callable(symbols_by_path, module_path, binding["name"])
                            if target is None:
                                target = self._unique_class(symbols_by_path, module_path, binding["name"])
                                if target is not None:
                                    edge_type = "instantiates"

                if target is None:
                    unresolved_sites += 1
                    continue
                if caller_id not in indexed_symbol_ids:
                    unresolved_sites += 1
                    continue
                edge_added = add_edge(caller_id, target["id"], edge_type)
                if edge_added and edge_type == "instantiates":
                    instantiation_edges += 1
        return indexed_sites, unresolved_sites, instantiation_edges

    def _assemble(
        self,
        base: Path,
        candidates: list[Path],
        files: dict[str, dict[str, Any]],
        skipped_large: int,
        walk_truncated: bool,
    ) -> tuple[list[dict[str, Any]], list[dict[str, str]], dict[str, Any]]:
        module_paths: dict[str, str] = {}
        ambiguous_modules: set[str] = set()
        for relative in candidates:
            if relative.as_posix() not in files:
                continue
            module = _module_name(relative)
            if module in module_paths:
                ambiguous_modules.add(module)
            else:
                module_paths[module] = relative.as_posix()
        for module in ambiguous_modules:
            module_paths.pop(module, None)

        nodes: list[dict[str, Any]] = []
        structural_edges: list[dict[str, str]] = []
        imports_by_path: dict[str, list[list[str]]] = {}
        errors: list[dict[str, Any]] = []
        external_imports = 0
        parsed_files = 0
        symbol_count = 0
        file_by_path: dict[str, str] = {}
        symbols_by_path: dict[str, dict[str, list[dict[str, Any]]]] = {}

        for relative in candidates:
            rel = relative.as_posix()
            record = files.get(rel)
            if record is None:
                continue
            file_id = f"file:{rel}"
            file_by_path[rel] = file_id
            nodes.append({
                "id": file_id,
                "type": "file",
                "name": relative.name,
                "path": rel,
                "is_test": _is_test_file(relative),
                "source_sha256": record["source_sha256"],
            })
            if record["parse_status"] == "parsed":
                parsed_files += 1
            if record["error"] is not None and len(errors) < 200:
                errors.append(record["error"])

            remaining = max(0, MAX_SYMBOLS - symbol_count)
            selected_nodes = record["nodes"][:remaining]
            nodes.extend(selected_nodes)
            symbol_count += len(selected_nodes)
            selected_ids = {item["id"] for item in selected_nodes}
            symbols_by_path[rel] = {}
            for item in selected_nodes:
                symbols_by_path[rel].setdefault(item["qualified_name"], []).append(item)
            structural_edges.extend(
                edge for edge in record["structural_edges"]
                if edge["target"] in selected_ids
                and (edge["source"].startswith("file:") or edge["source"] in selected_ids)
            )
            imports_by_path[rel] = record["import_groups"]

        edges: list[dict[str, str]] = []
        seen_edges: set[tuple[str, str, str]] = set()
        edge_truncated = False

        def add_edge(source: str, target: str, kind: str) -> bool:
            nonlocal edge_truncated
            key = (source, target, kind)
            if key in seen_edges:
                return False
            if len(edges) >= MAX_EDGES:
                edge_truncated = True
                return False
            seen_edges.add(key)
            edges.append({"source": source, "target": target, "type": kind})
            return True

        for edge in structural_edges:
            add_edge(edge["source"], edge["target"], edge["type"])

        for rel, groups in imports_by_path.items():
            source_id = file_by_path[rel]
            for alternatives in groups:
                resolved = False
                for module in alternatives:
                    target_path = self._resolve_module(module, module_paths)
                    if target_path:
                        add_edge(source_id, file_by_path.get(target_path, f"file:{target_path}"), "imports")
                        resolved = True
                if not resolved:
                    external_imports += 1

        by_stem: dict[str, list[str]] = {}
        for relative in candidates:
            if relative.as_posix() in file_by_path:
                by_stem.setdefault(relative.stem, []).append(relative.as_posix())
        for relative in candidates:
            if not _is_test_file(relative):
                continue
            stem = relative.stem
            target_stem = stem[5:] if stem.startswith("test_") else (
                stem[:-5] if stem.endswith("_test") else ""
            )
            targets = by_stem.get(target_stem, [])
            test_path = relative.as_posix()
            if test_path in file_by_path and len(targets) == 1 and targets[0] != test_path:
                add_edge(file_by_path[test_path], file_by_path[targets[0]], "tests")

        call_sites, unresolved_call_sites, instantiation_edges = self._resolve_call_edges(
            files, file_by_path, symbols_by_path, module_paths, add_edge
        )
        call_edges = sum(1 for edge in edges if edge["type"] == "calls")
        call_sites_truncated = (
            call_sites >= MAX_CALL_SITES
            or any(record.get("call_sites_truncated", False) for record in files.values())
        )
        summary = {
            "files_discovered": len(file_by_path),
            "files_parsed": parsed_files,
            "symbols": symbol_count,
            "internal_import_edges": sum(1 for edge in edges if edge["type"] == "imports"),
            "test_edges": sum(1 for edge in edges if edge["type"] == "tests"),
            "call_edges": call_edges,
            "instantiation_edges": instantiation_edges,
            "call_sites_indexed": call_sites,
            "call_sites_unresolved": unresolved_call_sites,
            "call_sites_truncated": call_sites_truncated,
            "external_imports_unresolved": external_imports,
            "parse_errors": len(errors),
            "skipped_large_files": skipped_large,
            "truncated": (
                walk_truncated or symbol_count >= MAX_SYMBOLS
                or edge_truncated or len(structural_edges) >= MAX_EDGES
                or call_sites_truncated
            ),
        }
        return nodes[: MAX_FILES + MAX_SYMBOLS], edges[:MAX_EDGES], summary

    def _build(self, root: str) -> dict[str, Any]:
        base = Path(root).resolve(strict=True)
        candidates, skipped_large, walk_truncated = self._candidates(base)
        root_path = str(base)
        root_key = hashlib.sha256(root_path.encode("utf-8")).hexdigest()

        prior_meta: dict[str, Any] | None = None
        prior_files: dict[str, dict[str, Any]] = {}
        prior_edges: list[dict[str, str]] = []
        if self.store is not None:
            prior_meta, prior_files, prior_edges = self.store.load(root_key, root_path)

        cache_format_current = (
            prior_meta is not None
            and int(prior_meta.get("schema_version", 0)) >= _GRAPH_RECORD_VERSION
        )
        current_files: dict[str, dict[str, Any]] = {}
        files_reparsed = 0
        files_reused = 0
        for relative in candidates:
            full_path = base / relative
            try:
                raw, size_bytes, mtime_ns = self._read_snapshot(full_path)
            except FileNotFoundError:
                continue
            source_sha256 = hashlib.sha256(raw).hexdigest()
            rel = relative.as_posix()
            cached = prior_files.get(rel) if cache_format_current else None
            if cached is not None and cached["source_sha256"] == source_sha256:
                record = dict(cached)
                record["size_bytes"] = size_bytes
                record["mtime_ns"] = mtime_ns
                files_reused += 1
            else:
                record = _parse_file(relative, raw, source_sha256)
                record["size_bytes"] = size_bytes
                record["mtime_ns"] = mtime_ns
                files_reparsed += 1
            current_files[rel] = record

        fingerprint = self._fingerprint(current_files)
        removed_count = len(set(prior_files) - set(current_files))
        snapshot_changed = (
            prior_meta is None
            or not cache_format_current
            or prior_meta["source_fingerprint"] != fingerprint
            or files_reparsed > 0
            or removed_count > 0
            or set(prior_files) != set(current_files)
        )

        nodes, built_edges, summary = self._assemble(
            base, candidates, current_files, skipped_large, walk_truncated
        )
        cache_state = "disabled" if self.store is None else (
            "miss" if prior_meta is None else ("stale" if snapshot_changed else "fresh")
        )
        generation = 0
        if self.store is not None:
            if snapshot_changed:
                generation = self.store.save_snapshot(
                    root_key=root_key,
                    root_path=root_path,
                    source_fingerprint=fingerprint,
                    generation_expected=int(prior_meta["generation"]) if prior_meta else 0,
                    files=current_files,
                    edges=built_edges,
                    refreshed_at=datetime.now(timezone.utc).isoformat(),
                )
                cache_edges = built_edges
            else:
                generation = int(prior_meta["generation"])
                cache_edges = prior_edges
            edges = cache_edges
            summary["strategy"] = "bounded-python-ast-sqlite-incremental-no-source-body-cache"
        else:
            edges = built_edges
            summary["strategy"] = "bounded-python-ast-no-source-body-cache"

        summary["source_fingerprint"] = fingerprint
        summary["cache"] = {
            "state": cache_state,
            "generation": generation,
            "files_hashed": len(current_files),
            "files_reparsed": files_reparsed,
            "files_reused": files_reused,
            "files_removed": removed_count,
        }
        return {
            "root": root_path,
            "language": "python",
            "nodes": nodes,
            "edges": edges,
            "errors": [
                item["error"] for item in current_files.values()
                if item["error"] is not None
            ][:200],
            "summary": summary,
        }

    async def index(self, root: str) -> dict[str, Any]:
        if self.store is None:
            return self._build(root)
        try:
            return self._build(root)
        except GraphStoreConflict:
            # One retry handles simultaneous requests without losing either refresh.
            return self._build(root)

    async def query(self, root: str, query: str, limit: int = 12) -> list[dict[str, Any]]:
        graph = await self.index(root)
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
