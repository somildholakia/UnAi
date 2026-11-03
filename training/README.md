# unAI training and specialist adapters

M5 has a reproducible training and evaluation workflow for three separate LoRA/QLoRA adapters:

- `next_action`: select the next approved inspection, retrieval, edit or test action.
- `planner`: convert a user goal into bounded plans, acceptance checks and permission requirements.
- `verifier`: interpret test evidence, distinguish failures from unavailable checks, and report unresolved risks.

These are separate adapter roles, not three claims of autonomous capability. Training is offline and requires a compatible model, a suitable machine and a curated dataset. The 6 GB Android profile is an inference/release target, not a training device. No adapter weights are currently committed or claimed trained.

## 1. Collect eligible trajectories

Each raw JSONL record must include:

```json
{
  "specialist": "next_action",
  "messages": [
    {"role": "user", "content": "Inspect the failing function and its tests."},
    {"role": "assistant", "content": "Read the target file and the linked test first."}
  ],
  "outcome": "passed",
  "verification": {
    "status": "passed",
    "checks": [
      {"name": "independent acceptance suite", "passed": true}
    ]
  },
  "provenance": {
    "source": "consented task trajectory source",
    "license": "Apache-2.0",
    "consented": true,
    "repository": "project-or-repository-group"
  }
}
```

Only the three named specialist values are accepted. Messages must be valid user/assistant conversation turns and end with an assistant response. A trajectory is rejected unless the outcome and every independent verification check passed, explicit consent is true, a license is named, and a repository/project grouping key exists.

Never add private conversations, private repositories, credentials, or third-party data without explicit permission and compatible rights. Automated redaction covers common secret formats only; it is not a privacy guarantee. Inspect the prepared examples before training.

## 2. Build deterministic specialist splits

From the repository root:

```bash
python -m training.prepare_corpus \
  --input data/raw-trajectories.jsonl \
  --output-dir data/specialists \
  --seed 20261103 \
  --eval-fraction 0.2
```

The preparation command:
- validates consent, license and passing verification;
- redacts common token/key patterns and deduplicates sanitized conversations;
- hashes repository/project IDs before storing them in output;
- keeps every repository/project group entirely in train or eval;
- requires at least two distinct groups for every specialist;
- writes `next_action.train.jsonl` / `next_action.eval.jsonl`, likewise for planner and verifier, plus `corpus_manifest.json` with dataset hashes and split counts.

Do not train if the manifest reports too few groups or examples. One task split into multiple messages is not independent evidence.

## 3. Train each adapter separately

Install from the repository root:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e "./apps/engine[train]"
```

QLoRA requires supported CUDA hardware and bitsandbytes. LoRA can use supported hardware, but CPU-only training may be impractical. Training never runs from the Engine API or on the Android phone.

Example for the next-action adapter:

```bash
python training/train_lora.py \
  --model YOUR_SMALL_OPEN_WEIGHT_BASE_MODEL \
  --revision IMMUTABLE_MODEL_REVISION \
  --specialist next_action \
  --train-file data/specialists/next_action.train.jsonl \
  --eval-file data/specialists/next_action.eval.jsonl \
  --output-dir artifacts/unai-next-action \
  --qlora
```

Repeat with `--specialist planner` and `--specialist verifier`, pointing to their respective prepared files and unique output directories. The trainer refuses a train/eval group overlap, requires provenance and verification metadata, masks prompt tokens from the loss, preserves assistant responses when truncating long prompts, pads labels with `-100`, and records data hashes, train/eval groups, hyperparameters, package versions, GPU details, metrics and an adapter artifact digest in `unai_adapter_manifest.json`.

A LoRA adapter is not a standalone model. Retain the exact compatible base-model revision and its license.

## 4. Compare matched held-out results

The evaluation harness consumes one JSONL row per system and held-out case. For every `suite_id + case_id + specialist`, provide results for all three systems:

- `base`: base model without tools;
- `adapter`: the same base model with the specialist adapter;
- `tool_augmented`: the tool-augmented system, including non-empty tool names and tool evidence IDs.

Each row must declare `schema: "unai.benchmark-result.v1"` and needs `model_id`, `base_model`, a SHA-256 `case_sha256` of the canonical task prompt plus acceptance criteria, `latency_ms`, `tokens`, a consented/licensed provenance object and a `verification` object containing a status, named boolean checks, an evidence ID and verifier identifier. The same `suite_id + case_id + specialist` must carry the same `case_sha256` for all three systems. Unavailable runs are excluded rather than counted as failures or wins. The evaluator rejects incomplete or duplicate matched sets, contradictory status/check pairs, model or case-content mismatches and missing tool traces.

```bash
python training/evaluate_results.py \
  --input data/heldout-results.jsonl \
  --output reports/next-action-evaluation.json
```

The report includes verified success rates, paired improvements/regressions, latency and token summaries, plus an exact two-sided McNemar test on discordant paired outcomes. The release floor is 30 matched held-out cases and significance alpha is capped at 0.05. An adapter must improve the matched verified success rate, have zero base-pass/adapter-fail regressions, and pass the exact paired test; a tiny sample can show a descriptive gain but cannot pass the release gate. The evaluator stores the case-content hashes and the release gate recomputes p-values from paired counts instead of trusting a reported boolean. This is a measurement on the supplied held-out set, not proof of general capability.

## 5. Physical-device release gate

The evaluation report alone cannot approve a mobile release. Provide a separate instrumented physical-device measurement JSON using schema `unai.mobile-benchmark.v1`:

```json
{
  "schema": "unai.mobile-benchmark.v1",
  "specialist": "next_action",
  "base_model": "YOUR_SMALL_OPEN_WEIGHT_BASE_MODEL",
  "adapter_artifacts_sha256": "SHA256_FROM_ADAPTER_MANIFEST",
  "device_model": "physical Android device model",
  "android_version": "recorded Android version",
  "measured_at": "2026-11-03T12:00:00Z",
  "measurement_method": "documented repeatable sustained local inference test",
  "memory_budget_mb": 6144,
  "peak_rss_mb": 5400,
  "ttft_ms": 2200,
  "ttft_budget_ms": 5000,
  "sustained_tokens_per_second": 7.5,
  "minimum_tokens_per_second": 5.0,
  "thermal_throttling": false
}
```

The numbers above are an example schema only, not measured results or product defaults. Use actual recorded values and choose explicit budgets for your test protocol. The release gate independently checks the adapter artifact hash, specialist/base-model identity, the minimum 30-case paired significance threshold and exact p-value, a memory budget no higher than 6144 MiB, measured peak RSS, TTFT, sustained tokens/second and thermal throttling:

```bash
python training/release_gate.py \
  --adapter-manifest artifacts/unai-next-action/unai_adapter_manifest.json \
  --evaluation-report reports/next-action-evaluation.json \
  --mobile-metrics data/mobile/next-action-device.json \
  --output reports/next-action-release-decision.json
```

A missing mobile measurement, a failed performance comparison, any regression, a hash mismatch, an out-of-budget measurement or thermal throttling blocks release. Do not edit a release decision manually to mark an adapter ready.

## 6. DPO is optional and later

The existing preference-data and DPO scripts remain an experimental second stage. Use them only after a stable SFT baseline and only with chosen/rejected answers whose outcomes have been independently verified. A failed answer is not automatically a useful negative example.

## Current M5 status

The consented-corpus preparation, repository-disjoint splitting, role-specific LoRA/QLoRA trainer, matched-result evaluator and physical-device release gate are implemented. No user-provided corpus has been supplied, no specialist adapter has been trained, and no physical Android benchmark has been run. M5 is therefore training-ready infrastructure, not a completed training or performance milestone.
