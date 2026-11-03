# Python code graph

The PythonCodeGraphIndexer is a bounded, rebuild-on-demand AST indexer.

It creates file nodes and class/function/method symbol nodes with start and end line references. Edges represent file-to-symbol definitions, class-to-member containment, resolved internal imports, and heuristic test-file-to-source-file relationships. Each parsed file and symbol carries the SHA-256 digest of the exact UTF-8 source bytes used for AST parsing. Source bodies are not persisted in the graph.

Safety and resource controls:

- Ignores generated folders, hidden directories, and symlinked files.
- Skips Python files larger than 300 KB.
- Caps discovery at 1,500 files, symbols at 12,000, and edges at 24,000.
- Captures parse errors per file and continues indexing other files.
- Reports whether the result was truncated.

Create a project directory under the configured workspace root (default: .workspaces), then use GET /api/repository/graph?workspace=project-name to return the graph or GET /api/repository/search?workspace=project-name&q=class-name to find symbol ranges. The API does not return source bodies.

After an approved POST /api/tasks/inspect request grants `workspace_read`, GET /api/tasks/{task_id}/context returns excerpts only for symbols selected by that inspection. It does not accept caller-supplied paths or symbols. Every excerpt is re-read through the task's guarded file reader and its current SHA-256 must match the fingerprint stored during inspection; a changed file returns HTTP 409 and requires re-inspection. The response includes line numbers, source range, digest and truncation status.

Context budgets are deliberately small: at most 10 symbols, 120 source lines and 6,000 characters per excerpt, and 24,000 characters total. The optional `limit` is 1–10 and `context_lines` is 0–8. Large symbols may be truncated; use the source-range metadata and do not treat a partial excerpt as the complete file.

The current version is Python-only, rebuilds on each request, and does not infer call graphs or guarantee test-to-source mappings. It is a navigation/retrieval aid, not a complete semantic model. SQLite persistence and incremental refresh remain future work.
