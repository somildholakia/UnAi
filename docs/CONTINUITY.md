# Continuity guide for future chats / contributors

Read these files first, in order:

1. `README.md` — scope, constraints, current setup.
2. `docs/CHECKPOINT.md` — exact completed and incomplete work.
3. `docs/ROADMAP.md` — milestones and next actions.
4. `docs/ARCHITECTURE.md` — design principles and component boundaries.
5. `docs/RESEARCH.md` — evidence and sources.

## Rules for continuing work

- Do not claim that an item is implemented unless it exists in code and has been tested.
- Do not claim the model is 100× stronger without a controlled benchmark demonstrating it.
- Update `docs/CHECKPOINT.md` whenever a meaningful milestone is completed; include commit SHA after the project is pushed.
- Keep a clean distinction between model-generated hypotheses and verified facts.
- Never run arbitrary model-generated commands directly on the host. Use the isolated sandbox boundary.
- Do not expose API keys in the browser bundle, logs, repository, prompts, or test fixtures.
- Prefer local/open-weight inference by default; clearly disclose compute requirements and device limitations.
- Do not promise unlimited hosted inference. Use explicit quotas for any shared deployment.

## M0.5 continuation notes (2026-10-31)

Latest local checkpoint: modular intelligence foundation.

Implemented:
- Plugin contracts at `apps/engine/src/unai_engine/plugins/contracts.py`.
- Built-in registry at `plugins/builtin.py`.
- Deterministic zero-weight cognition at `cognition/rule_microcore.py`.
- SQLite event/trajectory memory at `memory/trajectory.py`.
- Streaming filesystem index at `indexing/filesystem.py`.
- Bounded candidate portfolio at `search/portfolio.py`.
- Static path evidence verifier at `verification/static.py`.
- `/api/plugins` manifest endpoint and tests.
- `docs/MOBILE_PROFILE.md` and `benchmarks/README.md`.

Verification:
- Engine tests: `cd apps/engine && pytest ../../tests -q` -> 15 passed.
- Frontend: `npm install && npm run build` attempted but command timed out during install; do not assume build passed.

Next steps:
1. Completed: publish to the standalone public repository `https://github.com/somildholakia/UnAi` on `main`. Verify the latest source commit and keep all further changes in this repository.
2. Implement typed task compiler and acceptance-check schema.
3. Replace filesystem scan query with persistent SQLite FTS + optional syntax-tree/symbol graph index.
4. Build evidence-producing execution runner with per-task Docker resource limits and no host-shell fallback.
5. Implement counterexample generator, candidate isolation, and proof bundle.
6. Benchmark a compact open-weight baseline on the user's actual 6 GB Android device before selecting runtime/model.
7. Add quantised CPU backend behind a separate `InferenceBackend` contract; keep model assets optional and downloadable by the user.

Do not claim a model has been trained, 6 GB performance achieved, or frontier-equivalent capability. Publish checkpoint updates after every meaningful milestone; automated 4–5 minute background commits are not possible from this chat unless a durable automation/workflow is explicitly configured.
