# Roadmap

## M0 — Foundation (current)
- Establish product constraints and research hypothesis.
- Build web shell and model-agnostic engine API.
- Add local OpenAI-compatible inference adapter.
- Add explicit state machine and workspace path safety tests.
- Publish repository and create durable checkpoint trail.

## M1 — Task compiler and repository map
- Parse user request into typed goals, constraints, acceptance checks, and permissions.
- Index files, symbols, imports, tests, and dependencies.
- Retrieve code slices based on graph traversal and semantic search.
- Return a plan that separates facts, hypotheses, and unknowns.

## M2 — Execution loop
- Add Docker-only command runner with strict resource and network controls.
- Add patch proposals and isolated Git worktrees.
- Capture deterministic evidence for each command.
- Make the agent ask before privileged, destructive, networked, or irreversible operations.

## M3 — Adversarial verification
- Generate targeted tests and counterexamples.
- Detect candidate disagreement and target the distinguishing conditions.
- Run regression suites and static checks.
- Implement evidence-gated delivery and proof bundles.

## M4 — Research-grade benchmarks
- Build a small held-out repository task set.
- Add baseline harness and reproducible run metadata.
- Run ablations and publish results, including negative results.

## M5 — Learning system
- Collect opt-in, sanitized, reproducible trajectories.
- Train a small next-action / verifier policy from outcomes.
- Compare against rule-based selection and the teacher model.
- Distill only after evidence of a useful quality/latency trade-off.

## M6 — Website and deployment
- Improve chat/task UX, live status, diff review, and evidence viewer.
- Add local model setup guide and model compatibility checks.
- Add optional browser inference where model/runtime support permits.
- Add optional self-hosted deployment templates. Shared hosted service only with explicit capacity and quota controls.


## M0.5 — 6 GB mobile feasibility (must precede model expansion)
- [ ] Select a 6 GB RAM Android reference device and record chipset, Android version, free RAM, and thermal state.
- [ ] Benchmark small quantized model baselines with LiteRT-LM and llama.cpp.
- [ ] Record model load time, peak PSS/RSS, TTFT, decode tokens/sec, energy, and sustained thermal performance.
- [ ] Add adaptive context/KV-cache limits and memory-pressure eviction.
- [ ] Prove CPU-only offline inference works before treating GPU/NPU acceleration as available.
- [ ] Set final model size and context based on these measurements.
