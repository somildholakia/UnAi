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

## Task compiler — deterministic planning\n\n`POST /api/tasks/compile` converts a request into a provisional typed contract: task category, explicit constraints, assumptions, unknowns, acceptance checks, bounded plan steps, required permissions, and separately supplied approvals. It uses deterministic rules and does not call a model or execute tools. The compiler is a planning baseline, not a guarantee of semantic understanding; review contracts before acting. A required permission missing from the caller's explicit approval list remains missing.\n\n## Repository code graph — Python AST prototype

The indexing subsystem now includes a bounded Python AST graph for files, classes, functions, methods, internal imports, and heuristic test-to-source links. Graph entries point to line ranges instead of storing source bodies. It skips generated/hidden directories, symlinks, and oversized files; it reports parse errors and truncation. This first version is Python-only and does not yet persist or incrementally refresh the graph. See docs/CODE_GRAPH.md.

## unAI Brain — Obsidian-style memory

The Brain is a portable Markdown knowledge graph. It supports YAML frontmatter, aliases, tags, wiki links, backlinks, unresolved links, full-text search and graph traversal. Markdown notes are canonical; SQLite FTS is a rebuildable index. The local agent retrieves a small number of relevant excerpts into context automatically.

- Local FastAPI endpoints and read-only MCP server: apps/engine/src/unai_engine/brain
- Hosted GPU-free API: Cloudflare Worker + D1 in apps/brain-cloud
- Browser graph explorer: apps/brain-web
- Design, local setup, remote access and privacy: docs/BRAIN.md

The Cloudflare Worker is a deployment scaffold, not a deployed service. It is public-read if BRAIN_READ_TOKEN is unset; configure it to protect note reads, and configure BRAIN_WRITE_TOKEN separately for note creation. Cloudflare's Free plan has daily request, CPU, D1 read/write and storage limits; see docs/BRAIN.md. A model does not gain access merely because a website exists: connect the API through the remote MCP bridge or an explicit HTTP tool.

## Training and fine-tuning

The training directory contains a provenance/consent/verification-gated data preparation pipeline, LoRA/QLoRA supervised fine-tuning, and a second-stage DPO preference harness. Train on suitable GPU hardware, then evaluate and export an adapter for a compatible local runtime. The phone is an inference target, not a training machine. No training run or adapter is claimed complete.

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

## Run locally

    cd apps/engine
    python -m venv .venv
    source .venv/bin/activate
    pip install -e '.[dev,mcp]'
    cp .env.example .env
    uvicorn unai_engine.main:app --host 127.0.0.1 --port 8000

Start a compatible local model server separately. Then index your Markdown vault:

    curl -X POST http://127.0.0.1:8000/api/brain/index

Run the web UI in apps/web with npm install && npm run dev.

Tests:

    PYTHONPATH=apps/engine/src pytest -q tests

## Current research hypothesis

The working concept is **Outcome-Lattice Execution Intelligence (OLEI)**: maintain explicit task-state transitions, retrieve a small repository/knowledge graph slice, explore bounded candidate approaches, and challenge each candidate with targeted verification before accepting work.

This is a hypothesis, not a claim of unprecedented novelty. Each contribution must beat a matched baseline on held-out tasks under the same compute and time budgets. See docs/ARCHITECTURE.md, docs/RESEARCH.md, docs/ROADMAP.md and benchmarks/README.md.

## License

No license has been selected. Standard copyright applies until the owner adds one.
