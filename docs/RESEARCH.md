# Research notes and evidence map

Research snapshot: 2026-10-31. These references are starting points, not endorsements. Recheck availability, methods, and reported results before making implementation or performance claims.

## Findings relevant to unAI

### Agentic coding must be evaluated as a system
The Qwen3-Coder technical report describes training around executable coding tasks, environment interaction, and reinforcement learning. This supports evaluating tool-use policy and execution outcomes, not only next-token code completion.

- Project: https://github.com/QwenLM/Qwen3-Coder
- Report: https://arxiv.org/abs/2603.00729

### Reusable trajectories can improve long-horizon agents
The 2026 paper *Scaling Test-Time Compute for Agentic Coding* investigates retaining and reusing information from prior attempts. unAI should store compact task-state deltas and failure modes rather than replaying large raw transcripts.

- Paper: https://arxiv.org/abs/2604.16529

### Adversarial verification deserves a first-class role
The ICML 2026 paper *Scaling Agentic Verifier for Competitive Coding* explores execution-based verifiers that search for discriminative tests and counterexamples. unAI should challenge candidate patches with targeted checks rather than only summarize a test run.

- Paper: https://proceedings.mlr.press/v306/ma26av.html

### Benchmark hygiene is essential
SWE-bench supplies Docker-based evaluation for real GitHub issues, but the full harness is resource intensive. OpenAI has also discussed contamination concerns in SWE-bench Verified. Start with small, held-out repository tasks and deterministic tests; report benchmark configuration and failures.

- Harness: https://github.com/SWE-bench/SWE-bench/blob/main/docs/reference/harness.md
- FAQ: https://www.swebench.com/SWE-bench/faq/
- Contamination discussion: https://openai.com/index/why-we-no-longer-evaluate-swe-bench-verified/

### Browser and local inference can reduce hosted dependencies
WebLLM and Transformers.js provide browser inference paths for supported models. LiteRT-LM and llama.cpp are candidate Android runtimes. Compatibility, memory and sustained speed are device/model dependent; browser execution does not make GPU compute free.

- WebLLM: https://github.com/mlc-ai/web-llm
- Transformers.js: https://huggingface.co/docs/transformers.js
- LiteRT-LM: https://developers.google.com/edge/litert-lm/overview
- llama.cpp Android: https://github.com/ggml-org/llama.cpp/blob/master/docs/android.md

## Falsifiable research questions

1. Does a structured task-state capsule reduce tool calls and tokens without reducing verified completion?
2. Does counterexample-first verification catch more regressions than repository tests alone?
3. Does a bounded candidate portfolio outperform a single trajectory under the same wall-clock and token budget?
4. Can an action-selection policy learn which inspection, test, or edit is most useful from execution evidence?
5. Which components matter most under ablation: repository graph, state capsule, parallel candidates, verifier, or evidence ledger?

## Mobile feasibility

A 6 GB Android target is a strict deployment constraint, not evidence of frontier-level capability. Candidate runtimes include LiteRT-LM and llama.cpp. Benchmark actual devices for model load time, peak process PSS/RSS, time-to-first-token, sustained tokens/second, energy use, and thermal throttling. Model file size alone is not a useful memory measurement.

## Experimental protocol

- Freeze model, runtime, task set, context, token budget, and wall-clock budget.
- Include held-out tasks and disclose contamination limits.
- Compare a base model, tool-augmented baseline, state-memory ablation, portfolio ablation, verifier ablation, and full system.
- Report verified success, regressions, false acceptance, unsafe action attempts, latency, tokens/tool calls, and failures by category.
- Preserve raw logs and patch hashes. Never report unverified completion as success.

## Research boundary

The proposed OLEI architecture is a hypothesis, not a claim of unprecedented novelty. Graph-based code representations, execution feedback, self-refinement, search, and verifier-guided testing already exist in prior work. unAI must demonstrate a measurable advantage from its particular combination and resource allocation.
