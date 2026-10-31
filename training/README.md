# unAI training and fine-tuning

Training is an optional offline workflow. The phone runs inference only. Training requires suitable GPU hardware, storage and a reproducible dataset; having a free API key or model endpoint does not mean model weights can be fine-tuned for free.

## Training strategy

Use small specialist adapters, not a from-scratch frontier pretraining run:

1. Collect task trajectories only from sources with explicit consent and compatible licenses.
2. Keep only tasks with verifiable outcomes, acceptance checks and source provenance.
3. Remove secrets, deduplicate records and split by repository/project so evaluation does not leak training examples.
4. LoRA/QLoRA supervised fine-tuning teaches task execution style, tool selection and structured responses.
5. DPO is a later optional stage using a chosen and rejected response whose outcomes have been independently checked.
6. Evaluate the adapter on held-out coding tasks with the same budget as the base model before deployment.

The trainer writes an adapter and a JSON manifest. It does not create a self-contained model; keep the original base model and its license with the adapter.

## Install

From repository root:

    python -m venv .venv
    source .venv/bin/activate
    pip install -e "./apps/engine[train]"

QLoRA needs a supported CUDA GPU and bitsandbytes. Non-quantized LoRA can run on supported hardware but CPU-only training is generally impractical for useful coding models. Do not run training on the 6 GB Android target.

## SFT JSONL format

Each line contains a messages array, a verified outcome and provenance:

    {
      "messages": [
        {"role": "user", "content": "Fix the bug..."},
        {"role": "assistant", "content": "The verified fix is..."}
      ],
      "outcome": "passed",
      "verification": {
        "status": "passed",
        "checks": [{"name": "unit tests", "passed": true}]
      },
      "provenance": {
        "source": "your consented task dataset",
        "license": "Apache-2.0",
        "consented": true
      }
    }

Prepare and inspect the cleaned file:

    python training/prepare_dataset.py --input data/raw.jsonl --output data/sft.jsonl

The filter rejects samples without explicit consent, source/license metadata and passing verification checks. It redacts common token/key patterns and deduplicates messages. Review the report and cleaned examples before training; automated redaction is not a guarantee of privacy.

## Run LoRA / QLoRA

    python training/train_lora.py       --model YOUR_SMALL_BASE_MODEL       --train-file data/sft.jsonl       --eval-file data/heldout.jsonl       --output-dir artifacts/unai-sft       --qlora

Start with a small, supported open-weight coding model. The right size depends on your GPU memory, context length, runtime support and license. Do not infer mobile feasibility from parameter count alone.

## DPO preference stage

Preference JSONL must contain prompt, chosen and rejected message arrays, provenance, and independently measured checks. The chosen response must pass; rejected must fail.

    python training/prepare_preferences.py --input data/preferences-raw.jsonl --output data/preferences.jsonl
    python training/train_dpo.py       --model YOUR_BASE_MODEL       --train-file data/preferences.jsonl       --eval-file data/preferences-heldout.jsonl       --output-dir artifacts/unai-dpo

DPO is optional and should only follow a stable SFT baseline. A rejected answer is not automatically a good negative example; keep the reason and evidence in your source data and curate it.

## Release gate

Do not ship an adapter unless it passes held-out acceptance tests, has a reproducible manifest, respects the base model license, contains no secrets/private data, and fits the target inference budget. Compare base model, adapter-only and full unAI system separately. No custom weights are currently trained or included in this repository.
