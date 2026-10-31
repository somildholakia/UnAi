# Python code graph

The PythonCodeGraphIndexer is a bounded, rebuild-on-demand AST indexer.

It creates file nodes and class/function/method symbol nodes with start and end line references. Edges represent file-to-symbol definitions, class-to-member containment, resolved internal imports, and heuristic test-file-to-source-file relationships. It stores identifiers and line ranges only; source bodies are not persisted in the graph.

Safety and resource controls:

- Ignores generated folders, hidden directories, and symlinked files.
- Skips Python files larger than 300 KB.
- Caps discovery at 1,500 files, symbols at 12,000, and edges at 24,000.
- Captures parse errors per file and continues indexing other files.
- Reports whether the result was truncated.

The current version is Python-only, rebuilds on each request, and does not infer call graphs or guarantee test-to-source mappings. It is a navigation index, not a complete semantic model. A future version can add incremental SQLite persistence and additional language frontends.