# unAI training and specialist adapters

M5 has a reproducible training and evaluation workflow for three separate LoRA/QLoRA adapters:

- `next_action`: select the next approved inspection, retrieval, edit or test action.
- `planner`: convert a user goal into bounded plans, acceptance checks and permission requirements.
- `verifier`: interpret test evidence, distinguish failures from unavailable checks, and report unresolved risks.

These are separate adapter roles, not three claims of autonomous capability. Training is offline and requires a compatible model, a suitable machine and a curated dataset. The 6 GB Android profile is an inference/release target, not a training device. No adapter weights are currently committed or claimed trained.

## 0. Create a first-party bootstrap corpus (no third-party dataset wait)

The repository includes a deterministic synthetic collector so development and training-pipeline smoke tests can start immediately without downloading third-party data:

```bash
python -m training.collect_synthetic \
  --output data/raw/unai-synthetic.jsonl \
  --groups 20 \
  --examples-per-group 12 \
  --seed 20261105 \
  --license CC0-1.0

python -m training.audit_corpus --input data/raw/unai-synthetic.jsonl

python -m training.prepare_corpus \
  --input data/raw/unai-synthetic.jsonl \
  --output-dir data/specialists \
  --seed 20261105 \
  --eval-fraction 0.2
```

The default creates 720 deterministic records (240 per specialist) across 20 project groups per role. Each generated answer is checked against a role-specific deterministic contract before the JSONL file is written. The adjacent `.manifest.json` records the generator version, seed, counts, output digest and limitations. Generated data is kept local and `data/raw/`, `data/specialists/`, `artifacts/` and `reports/` are git-ignored.

These examples are explicitly marked `source_type: synthetic`, carry generator/version/rights-basis provenance, and use the selected output license (default `CC0-1.0`). Change `--license` only when you intend to use that license for the generated corpus. Synthetic verification validates the generator's own response contracts; it is not a real codebase test, real user trajectory or evidence of model capability. Treat this as a bootstrap corpus for testing the pipeline and initial experiments, not a substitute for diverse, independently verified tasks.

For public datasets, do not treat public visibility as permission to train. Every record marked `source_type: public` must include an HTTPS `source_url`, immutable `source_revision`, HTTPS `license_url`, named license, explicit consent/permission metadata and a repository/project grouping key. The audit reports source types in aggregate only. Data from local projects requires explicit authorization; do not ingest private chats or repositories by default.

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

Only the three named specialist values are accepted. Messages must be valid user/assistant conversation turns and end with an assistant response. A trajectory is rejected unless the outcome and every verification check passed, explicit consent is true, a license is named, and a repository/project grouping key exists. New public-source records must also pin their HTTPS source, immutable revision and HTTPS license evidence; first-party synthetic records must identify their generator, version and rights basis. Legacy records without a source_type remain readable for compatibility, but new collection flows should always declare it.

Never add private conversations, private repositories, credentials, or third-party data without explicit permission and compatible rights. Automated redaction covers common secret formats only; it is not a privacy guarantee. Inspect the prepared examples before training.

## 1A. Read-only corpus preflight

Before preparing any files, audit the raw JSONL without writing split files:

```bash
python -m training.audit_corpus --input data/raw-trajectories.jsonl
```

The report includes the raw input SHA-256/byte count, eligible/rejected/duplicate counts, per-specialist eligible record and distinct repository/project-group counts, and whether each specialist has enough groups for a deterministic train/eval split. Rejection reasons are aggregated; conversation text, raw repository identifiers and provenance source strings are never printed. The audit is read-only and enforces a 2 GiB total-input limit while reading records in chunks no larger than 1 MB plus one byte. Overlong records are drained without buffering the full line, counted as rejected, and still included in the exact source digest. The corpus preparer applies the same streaming limits.

A role is marked split-ready only when it has at least one eligible record and at least two distinct repository/project groups. This is only a structural preflight: it does not establish that consent or license claims are authentic, independently replay verification, establish dataset quality, or mean there are enough examples to train a useful model. Review source permissions and sanitized samples before preparation and training.

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

Repeat with `--specialist planner` and `--specialist verifier`, pointing to their respective prepared files and unique output directories. Before loading the model, the trainer requires the adjacent `corpus_manifest.json` and verifies the selected specialist's exact train/eval filenames, SHA-256 values of the exact byte snapshots parsed into the in-memory datasets, example counts and repository-group counts. Hashing and parsing one snapshot prevents a file change between separate reads from making the manifest describe different bytes than the rows used for training. It also recomputes every record ID, requires SHA-256-shaped group IDs, checks provenance/group consistency, rejects malformed or oversized message sequences, and refuses train/eval group overlap. It then masks prompt tokens from the loss, preserves assistant responses when truncating long prompts, pads labels with `-100`, and records data hashes, train/eval groups, hyperparameters, package versions, GPU details, metrics and an adapter artifact digest in `unai_adapter_manifest.json`.

A LoRA adapter is not a standalone model. Retain the exact compatible base-model revision and its license.

## 4. Compare matched held-out results

The evaluation harness consumes one JSONL row per system and held-out case. For every `suite_id + case_id + specialist`, provide results for all three systems:

- `base`: base model without tools;
- `adapter`: the same base model with the specialist adapter;
- `tool_augmented`: the tool-augmented system, including non-empty tool names and tool evidence IDs.

Each row must declare `schema: "unai.benchmark-result.v1"` and include a `case_contract` object with the exact `prompt` and non-empty `acceptance_criteria` list. It also needs `model_id`, `base_model`, a SHA-256 `case_sha256` recomputed from that canonical contract, `latency_ms`, `tokens`, a consented/licensed provenance object and a `verification` object containing a status, named boolean checks, an evidence ID and verifier identifier. The same `suite_id + case_id + specialist` must carry the same `case_sha256` for all three systems. The accepted record shape is published in [benchmark-result.schema.json](schemas/benchmark-result.schema.json); unknown fields are rejected by the evaluator. Unavailable runs are excluded rather than counted as failures or wins. The evaluator rejects incomplete or duplicate matched sets, contradictory status/check pairs, model or case-content mismatches and missing tool traces.

Compute `case_sha256` from the exact task and acceptance contract that all three systems receive, not from the response or observed outcome:

```python
import hashlib
import json

case_contract = {
    "prompt": "Fix the off-by-one error in the range calculation.",
    "acceptance_criteria": ["boundary test passes", "no unrelated file changes"],
}
canonical = json.dumps(
    case_contract,
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
    allow_nan=False,
).encode("utf-8")
case_sha256 = hashlib.sha256(canonical).hexdigest()
```

Include the same canonical case contract and its digest in every base, adapter and tool-augmented row. The evaluator recomputes the digest and rejects mismatches. Keep the task suite frozen before evaluating any adapter; do not derive acceptance checks after seeing model outputs.

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

## 6. Serve and route the three specialists

The Engine can route a request to a separately served OpenAI-compatible adapter model. Configure only model IDs that your local inference server actually exposes:

```dotenv
UNAI_SPECIALIST_NEXT_ACTION_MODEL=unai-next-action-lora
UNAI_SPECIALIST_PLANNER_MODEL=unai-planner-lora
UNAI_SPECIALIST_VERIFIER_MODEL=unai-verifier-lora
```

Leave a value blank until that adapter is trained, validated and loaded by the serving runtime. The model ID is selected by server configuration, never by caller input. The routes are:

```text
POST /api/specialists/next_action/chat
POST /api/specialists/planner/chat
POST /api/specialists/verifier/chat
```

Each request accepts `message` and optional user/assistant `history`; the response identifies the selected specialist and configured model ID. The API inherits the Engine bearer-token/loopback perimeter. If a specialist model is missing, the endpoint returns HTTP 503 and does not silently use the base model. This is model routing, not proof that a specialist adapter has been trained or released.

## 7. DPO is optional and later

The existing preference-data and DPO scripts remain an experimental second stage. Use them only after a stable SFT baseline and only with chosen/rejected answers whose outcomes have been independently verified. A failed answer is not automatically a useful negative example.

## Current M5 status

The consented-corpus preparation, repository-disjoint splitting, role-specific LoRA/QLoRA trainer, matched-result evaluator and physical-device release gate are implemented. No user-provided corpus has been supplied, no specialist adapter has been trained, and no physical Android benchmark has been run. M5 is therefore training-ready infrastructure, not a completed training or performance milestone.
