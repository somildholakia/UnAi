# Development checkpoint

**Checkpoint:** M0.5 — Modular intelligence foundation
**Date:** 2026-10-31
**Repository:** https://github.com/somildholakia/UnAi (public; default branch `main`)

## Completed in the published source tree
- Research direction named Outcome-Lattice Execution Intelligence (OLEI).
- Documented architecture, research references, roadmap, mobile targets, security limits, and continuation instructions.
- Created a React/Vite browser interface and a FastAPI engine with an OpenAI-compatible local model adapter.
- Added explicit task-state transitions and workspace path validation.
- Added a Docker-only execution boundary (disabled unless Docker is available and a workspace is provided).
- Added typed plugin contracts and registry for cognition, memory, indexing, search, and verification.
- Added a deterministic zero-weight cognition microcore, bounded SQLite trajectory memory, streaming filesystem index, bounded candidate portfolio, and static path verifier.
- Exposed the `/api/plugins` manifest endpoint.
- Added unit tests covering API behavior, plugin registry, task state, sandbox refusal, and workspace path safety.
- Published the complete source and documentation to the public GitHub repository.

## Verification and limitations
- Engine suite: `cd apps/engine && pytest ../../tests -q` -> **15 passed**.
- `python -m compileall -q apps/engine/src` completed successfully.
- Frontend dependency installation/build was attempted, but timed out in the environment; the production build remains unverified.
- No end-to-end model-backed test was run; a compatible local model endpoint is required.
- No model weights have been trained. The rule microcore is a deterministic fallback, not a language model.
- No 6 GB physical-device memory, latency, thermal, or energy measurements have been completed. The published mobile budgets remain acceptance targets, not achieved results.
- No Claude-level equivalence or 100× gain is claimed.

## Next actions
1. Implement a typed task compiler and acceptance-check schema.
2. Replace repeated filesystem scans with persistent SQLite FTS and an optional syntax-tree/symbol graph index.
3. Build an evidence-producing execution runner with per-task Docker resource limits and no host-shell fallback.
4. Implement candidate isolation, counterexample generation, and a proof/evidence bundle.
5. Benchmark a compact open-weight baseline on a physical 6 GB Android device before choosing the runtime/model.
6. Add a quantised CPU backend behind a separate `InferenceBackend` contract; keep model assets optional and downloadable by the user.

## Mobile-first 6 GB profile
- Target device class: 6 GB RAM Android, CPU-first and offline after model assets are installed.
- Initial unverified budgets: model/runtime PSS <= 1.2 GB, whole-app peak <= 1.8 GB, and bounded 2K–4K context.
- Measure load time, peak PSS/RSS, time-to-first-token, decode throughput, energy, and sustained thermal performance.

These targets must be validated on real hardware. Model file size alone is not a meaningful memory measurement.
