# unAI — Outcome-Lattice Execution Intelligence

**A modular, local-first AI engineering system designed to investigate how much coding capability can be delivered on a 6 GB RAM mobile device.**

unAI is not another thin chat wrapper. Its research hypothesis is that a compact model can become a much more effective coding system when paired with structured task state, repository graphs, persistent trajectory memory, bounded candidate search, deterministic tools, and adversarial verification.

> **Research status:** early engineering prototype. No custom model weights have been trained, mobile budgets have not yet been measured on physical hardware, and no claim of Claude-level equivalence or 100× superiority is made. Capability claims require held-out benchmarks.

## Design principles

- **Architecture-agnostic:** transformers are one optional cognition backend, not a foundational constraint. Symbolic reasoning, program synthesis, graph algorithms, constraint solving, evolutionary search, recurrent/state-space models, and hybrid approaches can be explored behind stable plugin interfaces.
- **Evidence over plausible output:** a task is not complete just because the model says it is. Patches need test results, static checks, counterexamples, and explicit residual risk.
- **Local first:** use open-weight runtimes on the user's own hardware; no hosted paid API is required for local operation.
- **Modular by contract:** swap cognition, memory, indexing, search, verification, and execution independently.
- **Small resident footprint:** keep working state compact, persist discoveries, load optional specialists on demand, and react to memory pressure.
- **Safe execution:** arbitrary generated shell commands must never execute directly on the host. Use a constrained sandbox boundary.

## Initial mobile acceptance targets

| Metric | Initial target (unverified) |
|---|---:|
| Target device class | 6 GB RAM Android |
| Model + runtime PSS | ≤ 1.2 GB |
| Whole unAI process peak | ≤ 1.8 GB |
| Default context | 2K–4K tokens |
| Required accelerator | None; CPU-only path |
| Network | Offline after model assets are installed |

These are test targets, not achieved measurements. The model file size alone is not enough; KV cache, native runtime, tokenizer, indexes, and temporary buffers count toward memory. The device profile and measurement protocol live in [`docs/MOBILE_PROFILE.md`](docs/MOBILE_PROFILE.md).

## System shape

```text
                    User objective
                         |
                  Intent / task compiler
                         |
           +-------------+----------------+
           |                              |
   Structured task graph           Trajectory memory
           |                              |
           +---------------+--------------+
                           |
                Pluggable cognition core
              (symbolic / neural / hybrid)
                           |
                Bounded candidate search
                           |
                Repository understanding
                           |
               Isolated execution tools
                           |
               Adversarial verification
                           |
                 Evidence ledger / gate
                           |
             Verified patch or honest blocker
```

## Repository structure

```text
apps/
  engine/       FastAPI backend, plugin contracts, local model adapter, state/runtime
  web/          React + Vite browser interface
benchmarks/     evaluation protocol and result templates
docs/           architecture, research, roadmap, mobile targets, checkpoints
tests/          safety, state, API, plugin and indexing tests
scripts/        developer tooling
```

## Current modules

| Module | Purpose | Current status |
|---|---|---|
| `plugins/contracts.py` | Stable cognition/memory/index/search/verification interfaces | Implemented |
| `cognition/rule_microcore.py` | Zero-weight task routing and baseline plan generation | Prototype |
| `memory/trajectory.py` | Bounded SQLite task-event memory | Implemented |
| `indexing/filesystem.py` | Low-memory streaming workspace index/search | Prototype |
| `search/portfolio.py` | Bounded, deterministic proposal deduplication | Prototype |
| `verification/static.py` | Workspace path evidence checks | Prototype |
| `providers/openai_compat.py` | Pluggable local OpenAI-compatible model endpoint | Implemented |
| `runtime/sandbox.py` | Docker-only execution boundary | Prototype; execution tool not wired into agent loop |

## Run locally

### Engine

```bash
cd apps/engine
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
pip install -e '.[dev]'
cp .env.example .env
uvicorn unai_engine.main:app --reload --host 127.0.0.1 --port 8000
```

Start a compatible local model runtime separately (for example Ollama exposing `/v1/chat/completions`) and set `UNAI_MODEL_NAME`. A default model name is only a placeholder; pick a quantized model that fits your actual device.

### Web UI

```bash
cd apps/web
npm install
npm run dev
```

Set `VITE_API_BASE_URL` when the engine is not available at `http://localhost:8000`.

### Tests

```bash
cd apps/engine
pytest ../../tests -q
```

## Research direction

The working research name is **Outcome-Lattice Execution Intelligence (OLEI)**. The system stores task evolution as structured state transitions, explores a small portfolio of candidate approaches, and tries to falsify each candidate using cheap and targeted checks before expensive evaluation.

This is a hypothesis, not a claim of unprecedented novelty. Related ideas already exist in graph-based code understanding, agentic training, search, self-refinement, and execution-guided verification. unAI must demonstrate that its specific combination and resource allocation outperform baselines.

Read [`docs/RESEARCH.md`](docs/RESEARCH.md), [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md), and [`benchmarks/README.md`](benchmarks/README.md).

## Development continuity

Before continuing in a different chat, read [`docs/CONTINUITY.md`](docs/CONTINUITY.md) and [`docs/CHECKPOINT.md`](docs/CHECKPOINT.md). Update the checkpoint after every meaningful milestone. Canonical public repository: https://github.com/somildholakia/UnAi (default branch: `main`). The complete source tree, documentation, tests, and local web interface are maintained there.

## License

No license has been selected. Until one is added, standard copyright applies; do not assume the code is licensed for reuse.