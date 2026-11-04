# unAI — Outcome-Lattice Execution Intelligence

**A modular, local-first coding-agent research project targeting CPU-first Android devices with 6 GB RAM.**

unAI investigates whether compact models can deliver stronger coding outcomes when combined with structured task state, repository indexing, durable memory, bounded search, deterministic tools and adversarial verification.

> **Status:** early engineering prototype. No custom model weights have been trained, no Cloudflare deployment has been performed, and the mobile memory/speed targets have not been measured on physical hardware. No Claude-level equivalence or unsupported capability claim is made.

## The design

- **Model-agnostic cognition:** neural, symbolic and hybrid backends can be swapped behind plugin contracts.
- **Outcome-first:** generated patches require evidence; model confidence is not proof.
- **Memory is separate from inference:** unAI Brain stores and retrieves knowledge without GPU inference or embeddings.
- **Local-first:** run an open-weight model locally; no hosted model API is required for offline operation.
- **Safe execution:** generated commands must run inside a constrained sandbox, never through a host-shell fallback.

## Task compiler — deterministic planning

\`POST /api/tasks/compile\` converts a request into a provisional typed contract: task category, explicit constraints, assumptions, unknowns, acceptance checks, bounded plan steps, required permissions, and separately supplied approvals. It uses deterministic rules and does not call a model or execute tools. The compiler is a planning baseline, not a guarantee of semantic understanding; review contracts before acting. A required permission missing from the caller's explicit approval list remains missing.

## M3 inspection and evidence ledger

The task workflow is available through \`POST /api/tasks/inspect\`: it requires explicit \`workspace_read\`, compiles a typed contract, builds a bounded Python graph, ranks candidate symbols and records parse/discovery evidence in SQLite. Retrieve the snapshot through \`GET /api/tasks/{task_id}/evidence\`. To execute tests, the task must also explicitly approve \`run_tests\`, then the caller must separately POST \`{"confirm":"run-python-tests"}\` to \`/api/tasks/{task_id}/run-tests\`. The runner exposes only \`python -m pytest -q\`, stages a filtered temporary copy, and runs it in Docker with networking disabled. Build the pytest image described in \`apps/engine/SANDBOX.md\`. Missing Docker or pytest dependencies are reported as unavailable, never as a passing test. After inspection, read a source file through \`GET /api/tasks/{task_id}/files/{file_path}\` to obtain its content and base SHA-256. Submit one-file replacements to \`POST /api/tasks/{task_id}/patch-proposals\`; the proposal stores a diff and leaves the workspace untouched. Apply only with a separate \`POST /api/tasks/{task_id}/patch-proposals/{proposal_id}/apply\` request carrying \`{"confirm":"apply-patch"}\`. The live file hash is checked again before replacement. Refresh Python parse-quality evidence after a change with \`POST /api/tasks/{task_id}/quality-check\`; the proof summary treats pre-patch quality results as stale. For a selected Python function, \`POST /api/tasks/{task_id}/probes\` generates bounded, unverified boundary-input candidates from simple branch conditions. It does not execute candidates or invent expected outputs. Retrieve an evidence-sequence summary through \`GET /api/tasks/{task_id}/proof\`: it reports whether a patch is pending, awaiting tests, failed/unavailable, or has a passing test run newer than the latest applied patch. This is not a semantic correctness certificate. Configure the ledger with \`UNAI_EVIDENCE_DB_PATH\`.

The Engine API has an installation-wide bearer-token gate. Configure \`UNAI_API_TOKEN\` with a random secret of at least 32 characters for non-loopback access; send it as \`Authorization: Bearer <token>\`. With the token unset, the API accepts loopback clients only and rejects non-loopback peers. This is a shared installation credential, not per-user identity, tenant isolation, or rate limiting; keep public deployment disabled until those controls and a deployment threat model are reviewed.

### Read-only task MCP bridge

A local MCP server now exposes typed tools for task compilation, workspace inspection, source snapshots, unexecuted boundary probes, evidence and proof. Install the optional \`mcp\` extra and run \`unai-task-mcp\` (or \`python -m unai_engine.task_mcp_server\`) from \`apps/engine\`. The default MCP transport is stdio, so an MCP host launches it as a local subprocess.

The bridge has no patch-apply, test-run or shell-execution tools. Workspace inspection is disabled by default. To grant read access, configure the MCP server process with one fixed workspace and an explicit local setting:

\`\`\`dotenv
UNAI_ENGINE_API_URL=http://127.0.0.1:8000
UNAI_ENGINE_API_TOKEN=
UNAI_MCP_WORKSPACE=my-project
UNAI_MCP_ALLOW_WORKSPACE_READ=true
\`\`\`

The model cannot choose another workspace or supply its own permission grant. Keep the Engine API token in the MCP host's server-side environment, never in a browser bundle or prompt. The grant applies to the configured workspace for every client able to invoke this MCP server; use a separate local process/configuration for a different workspace. Patch application and test execution remain separate, explicit host actions through the Engine API.

## M4 grounded verification — early prototype

AST-generated boundary candidates remain unverified until a caller supplies explicit expected outputs and an oracle reference. The grounded workflow checks the source SHA-256, validates the function signature and probe origin, generates a fixed pytest harness and runs it only in the existing disposable Docker staging copy. Supported targets include module functions, static/class methods and ordinary methods on undecorated top-level classes with bounded JSON constructor fixtures; constructor hashes are part of reviewer attestations.

- Generate unverified candidates: \`POST /api/tasks/{task_id}/probes\`.
- Supply user/specification cases and the exact \`run-grounded-oracle-tests\` confirmation: \`POST /api/tasks/{task_id}/grounded-tests\`.
- Retrieve the evidence bundle: \`GET /api/tasks/{task_id}/proof-bundle\`.

A mismatching return value is recorded as a counterexample against the supplied oracle and the exact source hash. Execution records include output fingerprints and bounded redacted previews. The proof bundle preserves unresolved risks. Oracle references are not independently authenticated, and passing cases are not a proof of general correctness. See [verification notes](docs/VERIFICATION.md) for the schema, constraints and limitations.

## Repository code graph — Python AST prototype

The indexing subsystem includes a bounded Python AST graph for files, classes, functions, methods, imports, heuristic test-to-source links, and conservative static call/instantiation relationships. Symbol ranges carry SHA-256 fingerprints of the exact source bytes parsed. After an approved task inspection, `GET /api/tasks/{task_id}/context` returns small excerpts only for symbols discovered by that task; the current file hash must still match the inspection hash or the request fails closed with HTTP 409. Excerpts have strict line/character budgets and never become a general arbitrary-path reader. The graph remains Python-only and uses an incremental SQLite cache configured by `UNAI_CODE_GRAPH_DB_PATH` (default `.data/code-graph.sqlite3`). Each refresh hashes every eligible Python source snapshot, reuses cached AST records only when the exact SHA-256 matches, reparses changed/new files, removes deleted files and derives repository-wide import/test/call relationships. A complete graph generation is published in one WAL-backed SQLite transaction with an optimistic generation check; a failed refresh leaves the previous snapshot intact. The database stores source fingerprints, bounded symbol/call-site metadata and relationships, never source bodies. Static `calls` and `instantiates` edges are emitted only for unambiguous local/direct-import targets; dynamic, shadowed and unresolved calls are not guessed. This is source-level navigation, not a complete runtime call graph or semantic model. See docs/CODE_GRAPH.md.

## unAI Brain — Obsidian-style memory

The Brain is a portable Markdown knowledge graph. It supports YAML frontmatter, aliases, tags, wiki links, backlinks, unresolved links, full-text search and graph traversal. Markdown notes are canonical; SQLite FTS is a rebuildable index. The local agent retrieves a small number of relevant excerpts into context automatically.

- Local FastAPI endpoints and read-only MCP server: apps/engine/src/unai_engine/brain
- Hosted GPU-free API: Cloudflare Worker + D1 in apps/brain-cloud
- Browser graph explorer: apps/brain-web
- Design, local setup, remote access and privacy: docs/BRAIN.md

The Cloudflare Worker is a deployment scaffold, not a deployed service. It is public-read if BRAIN_READ_TOKEN is unset; configure it to protect note reads, and configure BRAIN_WRITE_TOKEN separately for note creation. Cloudflare's Free plan has daily request, CPU, D1 read/write and storage limits; see docs/BRAIN.md. A model does not gain access merely because a website exists: connect the API through the remote MCP bridge or an explicit HTTP tool.

## Training and fine-tuning

The training directory contains M5 specialist infrastructure: consent/license/verification-gated trajectories, deterministic repository-disjoint train/eval splits, separate next-action/planner/verifier LoRA/QLoRA runs, matched base/adapter/tool-augmented held-out evaluation with case-content fingerprints and exact paired significance, and a physical-device release gate. The Engine supports server-configured model routing to each separately served specialist without base-model fallback. Train on suitable GPU hardware; the 6 GB phone is an inference target, not a training machine. No dataset has been supplied, no weights have been trained, and no Android release benchmark is claimed complete.

Read training/README.md before preparing data. Never train on private conversations or repositories without explicit permission and compatible licenses.

## Mobile acceptance targets — unverified

| Metric | Initial target |
|---|---:|
| Device | 6 GB RAM Android |
| Model + runtime PSS | ≤ 1.2 GB |
| Whole app peak | ≤ 1.8 GB |
| Default context | 2K–4K tokens |
| Required accelerator | None; CPU-only path |

These are targets, not results. See docs/MOBILE_PROFILE.md.

## Install and test locally

The fastest first test is Android/Termux. It installs a local CPU inference runtime, downloads a compact GGUF coding model, then starts the model server, Engine and Web UI in one tmux session:

```bash
pkg update -y && pkg install -y git
git clone --branch feat/m4-instance-methods-m5-specialists --single-branch https://github.com/somildholakia/UnAi.git
cd UnAi
bash scripts/termux-install.sh
bash scripts/termux-start.sh
bash scripts/termux-doctor.sh --smoke-chat
```

The doctor checks all three local services and can send a short inference request directly to the local model. Each service has bounded automatic restart after a non-zero crash exit and a rotated log under `$HOME/.local/state/unai`; this works while the Termux/tmux session remains alive, not after Android kills the entire app process. Open `http://127.0.0.1:5173` on the same device. The first install compiles `llama.cpp` and downloads approximately 1.12 GB of Qwen2.5-Coder 1.5B Q4_K_M weights. Keep the initial 2K context; mobile memory and thermal performance must still be measured. See [the local install guide](docs/LOCAL_INSTALL.md).

For desktop setup, point the Engine at any local OpenAI-compatible model server using `UNAI_MODEL_BASE_URL` and `UNAI_MODEL_NAME`. The default preset expects a llama.cpp server at `127.0.0.1:8080`. Ollama can also be used by changing those two settings to its local OpenAI-compatible endpoint and a model that is already installed.

Local mode removes hosted model quotas and per-token provider billing. It is not infinite compute: device RAM, storage, battery, thermals and generation speed remain practical limits. No cloud inference fallback is configured. The starter model is an existing open-weight coding model, not a custom unAI model trained by this repository.

### Run the Engine and UI manually

```bash
cd apps/engine
python -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
uvicorn unai_engine.main:app --host 127.0.0.1 --port 8000
```

In another terminal:

```bash
cd apps/web
npm install
npm run dev
```

Tests:

```bash
PYTHONPATH=apps/engine/src pytest -q tests
```

## Current research hypothesis

The working concept is **Outcome-Lattice Execution Intelligence (OLEI)**: maintain explicit task-state transitions, retrieve a small repository/knowledge graph slice, explore bounded candidate approaches, and challenge each candidate with targeted verification before accepting work.

This is a hypothesis, not a claim of unprecedented novelty. Each contribution must beat a matched baseline on held-out tasks under the same compute and time budgets. See docs/ARCHITECTURE.md, docs/RESEARCH.md, docs/ROADMAP.md and benchmarks/README.md.

## License

No license has been selected. Standard copyright applies until the owner adds one.
