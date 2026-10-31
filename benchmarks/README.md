# unAI evaluation harness

No capability or speed claim is valid without an exact task set, environment, baseline, and raw results.

## Required metrics

- Task success: issue-level acceptance checks passed / total.
- Regression rate: previously passing tests broken by the patch.
- False acceptance: proposals marked complete despite failed or missing required evidence.
- Tool efficiency: tool calls, candidate count, generated tokens, and wall time.
- Inference: model load time, time-to-first-token, decode tokens/second, peak process PSS/RSS.
- Mobile: energy estimate, thermal throttling, peak memory, Android version, chipset, quantization, context size.
- Safety: forbidden path access attempts, unsandboxed execution attempts, and permission boundary violations.

## Experimental groups

1. Small local model + single-shot answer.
2. Same model + deterministic repository tools.
3. Same model + OLEI task-state and trajectory memory.
4. Same model + bounded portfolio search.
5. Same model + adversarial verification.
6. Full unAI stack.
7. Non-neural rule microcore + deterministic tooling (zero model weights).

Hold model version, task set, context, token budget, wall-clock budget, and runtime versions constant where possible. Include failures. Never compare different hardware and call the difference an architecture improvement.
