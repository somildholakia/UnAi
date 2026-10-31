# Contributing

1. Read `docs/CONTINUITY.md` and `docs/ARCHITECTURE.md` before changing the agent loop.
2. Add tests for state transitions, permissions, path handling, and every new tool.
3. Keep providers behind the model adapter interface.
4. Keep execution inside the sandbox; no host-shell fallback is allowed.
5. Add benchmark evidence for claims about capability or speed.
6. Update `docs/CHECKPOINT.md` after each meaningful milestone.
