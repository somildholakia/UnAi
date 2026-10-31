# unAI Architecture — Outcome-Lattice Execution Intelligence

**Status:** working hypothesis, not a claim of proven novelty or superiority.

## Why this direction

A conventional coding assistant often serializes a task into a large prompt, samples a patch, and relies on a person or a test run to discover errors. This can waste context on repeated repository text, discard lessons from failed attempts, and reward plausible explanations rather than verified outcomes.

OLEI treats a task as a partially observed system whose state changes as tools produce evidence.

## Proposed pipeline

```text
User objective
    |
    v
[1. Intent compiler] ---> task contract + constraints + acceptance tests
    |
    v
[2. Repository mapper] ---> symbol/dependency/test graph + relevant slices
    |
    v
[3. Hypothesis portfolio] ---> independent plans / patch candidates
    |                           (bounded parallel exploration)
    v
[4. Isolated execution] ---> build, tests, lint, type checks, traces
    |
    v
[5. Adversarial verifier] ---> counterexamples + discriminating tests
    |                           (challenge each candidate)
    v
[6. Evidence ledger] ---> ranked candidates + unresolved risks
    |
    +---- failures / new evidence ---> update task state and search again
    |
    v
[7. Evidence gate] ---> verified patch, partial result, or honest blocked state
```

## Key state objects

- **Task contract:** desired outcome, invariants, constraints, acceptance checks, scope boundaries, and user-approved permissions.
- **Repository graph:** files, symbols, imports, call edges, tests, and change-impact edges. Graph nodes reference source ranges rather than duplicating whole files.
- **Hypothesis:** a falsifiable explanation for the bug or a candidate design, with predicted observations and a confidence score.
- **Action:** typed operation such as inspect, search, edit, run-test, lint, typecheck, or ask-user. Actions have explicit inputs and permission requirements.
- **Evidence:** command, environment, exit code, stdout/stderr digest, test identifiers, artifact hash, and timestamp.
- **Trajectory capsule:** compact state delta, hypotheses tested, failure modes discovered, and next useful action. Raw logs remain separately addressable.

## Proposed research contributions

1. **State-space memory instead of transcript memory.** Compress an attempt into graph deltas and falsifiable hypotheses. Preserve exact evidence separately for auditability.
2. **Counterexample-first verification.** Use a verifier to seek inputs that make candidate patches disagree or violate stated invariants. Prefer discriminating test cases over large numbers of random tests.
3. **Portfolio search with shared evidence.** Explore a small bounded set of candidate fixes, but share validated discoveries so independent branches do not repeat the same work.
4. **Proof-carrying patches.** Every delivered change includes a machine-readable evidence bundle: tests run, checks passed/failed/skipped, changed files, and remaining uncertainty.
5. **Outcome-trained action policy.** Train a compact policy to select the next useful action based on task-state deltas and outcome labels, rather than attempting to recreate a general foundation model first.

These are hypotheses. Graph-based code representations, execution feedback, self-refinement, search, and verifier-guided testing already exist in prior work. Our value must come from a measurable new combination, better state representation, or a more effective training signal—not from claiming that each ingredient is unprecedented.

## Model strategy

### Stage A — model-agnostic system

Use a swappable OpenAI-compatible endpoint. Initial local target: Ollama-compatible `/v1/chat/completions`. The system must not be coupled to one vendor or model name.

### Stage B — open-weight teacher and baselines

Evaluate open-weight coding models on the same task harness. Models are replaceable; compare quality, latency, VRAM, and cost. The Qwen3-Coder family is a relevant baseline because its public technical materials describe executable-task synthesis, environment interaction, and reinforcement learning for coding agents.

### Stage C — train unAI components

Do not attempt a frontier-scale pretraining run on day one. Start by learning a small action-selection / verifier policy from reproducible execution trajectories. If data and compute justify it, expand to task-specific model training and distillation.


## Mobile-first model and runtime architecture (new hard constraint)

### Design objective

The reference deployment is an Android device with **6 GB total system RAM**, no dedicated GPU requirement, and no paid remote inference. The system should be CPU-capable and operate offline after installation. A native wrapper or PWA may provide the interface, but the inference core must be measured on real mobile hardware.

We will not attempt to fit a frontier-scale dense model into this budget. Instead, investigate a layered design:

1. **Microcore model:** a compact local model for intent parsing, plan drafting, selecting tools, and turning evidence into concise decisions. Start with a small quantized baseline, then evaluate a custom student/action-policy trained for these roles.
2. **Structured working state:** typed task contracts, repository graphs, symbol tables, dependency slices, and trajectory capsules. These replace repeated full-repository prompt stuffing.
3. **Deterministic specialist engines:** AST parsing, grep/search, type checking, linting, test runners, diff analysis, and sandboxed execution. These perform work where conventional software is more reliable and cheaper than token generation.
4. **Verifier:** challenge a proposed patch with regression tests, generated edge cases, static checks, and counterexamples. The model proposes; evidence decides.
5. **Memory scheduler:** keep only the active model and small working set resident; persist other specialist assets on flash and load them on demand. Evict caches and shrink context when Android reports memory pressure.
6. **Optional acceleration:** use available GPU/NPU backends when supported, but keep a tested CPU fallback.

This is a systems-level route to better coding outcomes, not a claim that a small parameter count magically contains the knowledge of a frontier model. Mixture-of-experts active parameter counts must not be confused with total weight storage: inactive expert weights still consume storage and can consume RAM unless carefully paged.

### Initial resource budgets (acceptance targets, unverified)

| Metric | Initial target | Measurement rule |
|---|---:|---|
| Model + runtime resident memory | <= 1.2 GB | Android process PSS/RSS during generation |
| Whole app peak memory | <= 1.8 GB | Peak process memory during a coding task |
| Default context | 2K–4K tokens | Include KV-cache accounting |
| GPU requirement | None | CPU-only test must complete end-to-end |
| Network requirement | None after setup | Airplane-mode coding task |
| Latency/speed | Device-specific target to be set after baseline | Record TTFT, decode tokens/sec, energy, and thermal throttling |

These are engineering targets, not reported results. First test on at least one physical 6 GB Android device, then expand to low/mid-range chipsets. Avoid relying on emulator or flagship-only data.

### Candidate inference runtimes

- **LiteRT-LM:** evaluate for Android and Web deployments; its official documentation describes CPU/GPU backends, tool use, and supported mobile/web platforms.
- **llama.cpp:** evaluate as a portable GGUF/quantized CPU fallback, including its Android integration.

Choose based on measured memory, sustained token throughput, app size, compatibility, and thermal behaviour—not brand preference.

### Training strategy under the memory constraint

Training and inference are separate problems. The final mobile device only needs to run inference; model training can happen elsewhere. Begin with a small student model or action policy trained on high-quality, reproducible coding trajectories. Distillation from open models may be used for research experiments, but the deployed unAI system must not depend on paid APIs. A genuine from-scratch foundation-model training run is not feasible at frontier scale under the project's free/low-resource constraints.

### Capability boundary

The initial target is **high reliability on bounded software-engineering workflows**, not universal equivalence to Claude Opus. Track both model-only scores and full-system task completion. Compare against strong open models and, where available, a frontier reference under the same tasks and budgets. Report failure cases, latency, energy, memory, and cost.

## Runtime and security boundaries

- The API server never runs user-provided shell commands directly on its host process.
- Execution is delegated to a disposable Docker container with no network, read-only root filesystem, dropped Linux capabilities, CPU/memory/PID limits, and an explicitly mounted workspace.
- Workspace paths are canonicalized and checked to prevent traversal outside the selected project root.
- Secrets are not placed into prompts or candidate workspaces by default.
- Network, package installation, credential access, and destructive actions require explicit configuration and permission.
- Logs are treated as untrusted data; they cannot modify agent policy or permission state.

## Deployment modes

1. **Local-first:** static React frontend plus local engine and local model endpoint. No inference API bill; device compute is still required.
2. **Self-hosted:** user deploys engine and model server on their own GPU or cloud account.
3. **Shared hosted:** optional later service with explicit quotas, queueing, abuse controls, and transparent cost limits. Never advertise unlimited hosted compute without a funded capacity plan.

## Core success metric

Primary: **verified task completion rate under a fixed compute/time budget**, measured on held-out tasks. Secondary metrics: regression rate, unsafe-action rate, p50/p95 latency, tokens and tool calls per success, human intervention rate, and cost per verified task.

## Modular extension contracts (M0.5 implementation)

The engine now exposes explicit plugin contracts under `unai_engine.plugins.contracts`:

- `CognitionPlugin`: produces typed proposals from a bounded task context.
- `MemoryPlugin`: recalls and stores state-delta events, not entire raw transcripts.
- `RepositoryIndexPlugin`: incrementally describes and searches a workspace.
- `SearchPlugin`: bounds the size of a candidate portfolio.
- `VerificationPlugin`: returns explicit evidence records for a proposal.

`PluginRegistry` has named, typed categories and rejects duplicate plugin names. Built-ins are deterministic and inexpensive: a rule microcore, SQLite trajectory memory, streaming filesystem index, bounded proposal selector, and static path verifier. These are infrastructure primitives, not a trained general-purpose model. The registry is intentionally explicit; it does not import arbitrary Python modules or execute third-party plugins dynamically.

### Research direction beyond standard neural architectures

unAI will not assume that the final policy must be a transformer, CNN, ANN, or any single neural architecture. Candidate research tracks are:

1. **Event-sourced symbolic cognition:** task state is a typed graph of facts, goals, constraints, hypotheses, and proof obligations. Updates are operations over this graph rather than free-form token streams.
2. **Program synthesis / constraint solving:** represent small code edits and configuration decisions as constraints; search for solutions satisfying executable invariants.
3. **Algorithmic retrieval and graph traversal:** compute relevance over symbol/dependency graphs; retrieve small slices instead of generating long context.
4. **Active experiment selection:** select the next test or inspection by expected information gain per unit cost. This can begin with deterministic scoring and later learn a policy.
5. **Evolutionary / population search:** maintain a small bounded set of candidate strategies and patches, use deterministic fitness from tests, static analysis, and counterexamples, and preserve diversity.
6. **Learned micro-policies:** investigate compact recurrent/state-space/linear-attention models, quantized transformers, lookup/hashing methods, or hybrid symbolic-neural models only after the rule baseline is measurable.
7. **Proof-carrying outputs:** every code change has a linked evidence set and explicit unresolved risks. No verbal confidence substitutes for test evidence.

Each track must beat a clearly specified baseline on held-out tasks at a matched compute and memory budget. Novelty is a research claim to validate, not a name to assume.
