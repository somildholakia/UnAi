# Python code graph

The PythonCodeGraphIndexer is a bounded Python AST indexer with optional incremental SQLite persistence. The Engine enables the cache by default at `UNAI_CODE_GRAPH_DB_PATH` (`.data/code-graph.sqlite3`); direct indexer users can omit `cache_path` to keep the cache disabled.

It creates file nodes and class/function/method symbol nodes with start and end line references. Edges represent file-to-symbol definitions, class-to-member containment, resolved internal imports, heuristic test-file-to-source-file relationships, conservative static `calls` relationships, and class `instantiates` relationships. Each parsed file and symbol carries the SHA-256 digest of the exact UTF-8 source bytes used for AST parsing. Source bodies are not persisted in the graph.

Safety and resource controls:

- Ignores generated folders, hidden directories, and symlinked files.
- Skips Python files larger than 300 KB.
- Caps discovery at 1,500 files, symbols at 12,000, and edges at 24,000.
- Collects at most 100 dotted-name call sites per file and 12,000 per repository refresh.
- Captures parse errors per file and continues indexing other files.
- Reports whether the result was truncated.

Create a project directory under the configured workspace root (default: .workspaces), then use GET /api/repository/graph?workspace=project-name to return the graph or GET /api/repository/search?workspace=project-name&q=class-name to find symbol ranges. The API does not return source bodies.

After an approved POST /api/tasks/inspect request grants `workspace_read`, GET /api/tasks/{task_id}/context returns excerpts only for symbols selected by that inspection. It does not accept caller-supplied paths or symbols. Every excerpt is re-read through the task's guarded file reader and its current SHA-256 must match the fingerprint stored during inspection; a changed file returns HTTP 409 and requires re-inspection. The response includes line numbers, source range, digest and truncation status.

Context budgets are deliberately small: at most 10 symbols, 120 source lines and 6,000 characters per excerpt, and 24,000 characters total. The optional `limit` is 1–10 and `context_lines` is 0–8. Large symbols may be truncated; use the source-range metadata and do not treat a partial excerpt as the complete file.

## Incremental SQLite cache

- Every refresh walks the bounded candidate set and hashes the exact bytes read for each eligible Python file. A before/after stat check rejects files that change while being read; freshness never relies on size or mtime alone.
- A workspace key is derived from its resolved absolute path. The cache stores per-file SHA-256, size/mtime metadata, parsed file/symbol ranges, parse diagnostics and import-resolution inputs. Source bodies are never written to SQLite.
- Files whose exact content digest is unchanged reuse their cached AST records. Changed or new files are parsed again; deleted files are removed from the next complete snapshot. Imports and heuristic test edges are re-resolved from the current full candidate set so changes to module names cannot leave old relationships behind.
- The repository fingerprint is SHA-256 over sorted relative paths and per-file source digests. Graph responses include `summary.source_fingerprint` and `summary.cache` (`miss`, `fresh`, `stale` or `disabled`, generation, files hashed/reparsed/reused/removed).
- A refresh is committed with `BEGIN IMMEDIATE` in one SQLite transaction. File rows, repository metadata and the derived edge set switch together; rollback preserves the prior complete generation after a failed write. An optimistic generation check detects concurrent refreshes and the indexer retries once.
- SQLite uses WAL, foreign keys, a busy timeout and full synchronous writes. The schema is versioned via `PRAGMA user_version`; unknown newer schema versions fail closed rather than being rewritten. Keep the database in local application data and back it up using normal SQLite-aware procedures.

## Conservative static calls and class construction

The indexer records dotted-name call sites inside function and method bodies, then resolves only targets that can be identified unambiguously from the parsed graph and module-scope imports:

- A bare function call may resolve to one uniquely named top-level function in the same module or one directly imported function alias.
- `self.method()` and `cls.method()` may resolve to one method on the enclosing class.
- `ClassName.method()` may resolve to a method on a uniquely identified local class; an imported class alias may resolve to one method in its source module.
- Calling a uniquely identified local or directly imported class creates an `instantiates` edge; function and method invocations create `calls` edges.
- Module aliases such as `import service as svc` may resolve `svc.function()` to an indexed callable in that module.
- Local parameters, assignments and local imports shadow module-level imported aliases. Ambiguous duplicate names, arbitrary instance attributes, computed callees, reflection, dynamic dispatch and monkey-patching are not guessed. Imports inside function scopes are not treated as module-level aliases.

The graph summary reports `call_edges`, `instantiation_edges`, `call_sites_indexed`, `call_sites_unresolved` and `call_sites_truncated`. Unresolved counts can include built-ins and third-party/dynamic calls; they do not mean a call is invalid. Reaching a call-site or edge budget marks the graph as truncated.

These are source-level syntactic relationships, not observed runtime calls or a complete call graph. The indexer does not infer dynamic dispatch, inheritance-based dispatch, reflection, monkey-patching or execution order. Static call metadata is persisted with the per-file AST records. SQLite schema version 2 adds call-site and import-binding columns; an existing version-1 cache is migrated and all eligible files are reparsed once to backfill them. Source bodies remain uncached.

The graph remains Python-only. Heuristic test-to-source mappings are not guaranteed. It is a navigation/retrieval aid, not a complete semantic model.

## Incomplete directory discovery and cache publication

A directory walk that reaches the `MAX_FILES` discovery cap is not a complete view of the repository. The indexer still returns the bounded partial graph for that request, but marks `summary.scan_complete=false` and (when SQLite caching is enabled) `summary.cache.state="incomplete"`. It does not publish a new SQLite generation, reports `snapshot_persisted=false`, retains the previous generation number and records zero confirmed removals. This prevents files beyond the walk cap from being mistaken for deleted files.

A later complete refresh hashes the full eligible candidate set, reconciles additions and removals, and atomically publishes one new generation. This rule concerns incomplete file discovery; symbol, edge and call-site budgets remain separately visible through the graph's truncation indicators.

