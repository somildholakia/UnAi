#!/usr/bin/env python3
"""Train one unAI LoRA/QLoRA specialist on verified, repository-split data."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import re
from pathlib import Path
from typing import Any


SPECIALISTS = ("next_action", "planner", "verifier")
_MAX_MESSAGES = 100
_MAX_MESSAGE_CHARS = 100_000
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_corpus_manifest(
    train_path: Path,
    eval_path: Path,
    specialist: str,
    train_rows: list[dict[str, Any]],
    eval_rows: list[dict[str, Any]],
    train_file_sha256: str | None = None,
    eval_file_sha256: str | None = None,
) -> dict[str, Any]:
    """Verify prepared split bytes and counts before importing/loading model weights."""
    if train_path.parent.resolve() != eval_path.parent.resolve():
        raise ValueError("Train and eval files must share the prepared corpus directory.")
    manifest_path = train_path.parent / "corpus_manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(
            f"Prepared corpus manifest is missing: {manifest_path}. "
            "Re-run training.prepare_corpus before training."
        ) from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Prepared corpus manifest is not valid UTF-8 JSON.") from exc

    if not isinstance(manifest, dict) or manifest.get("schema") != "unai.specialist-sft-corpus.v1":
        raise ValueError("Prepared corpus manifest has an unsupported schema.")
    specialists = manifest.get("specialists")
    entry = specialists.get(specialist) if isinstance(specialists, dict) else None
    if not isinstance(entry, dict):
        raise ValueError(f"Prepared corpus manifest has no {specialist!r} split.")

    expected_files = (
        ("train", train_path, train_rows),
        ("eval", eval_path, eval_rows),
    )
    group_sets: dict[str, set[str]] = {}
    for split, path, rows in expected_files:
        declared_name = entry.get(f"{split}_file")
        declared_sha = entry.get(f"{split}_sha256")
        if declared_name != path.name:
            raise ValueError(
                f"Prepared corpus manifest {split} filename does not match {path.name!r}."
            )
        if not isinstance(declared_sha, str) or not _SHA256_RE.fullmatch(declared_sha):
            raise ValueError(f"Prepared corpus manifest has an invalid {split} SHA-256.")
        actual_sha = train_file_sha256 if split == "train" else eval_file_sha256
        if actual_sha is None:
            actual_sha = sha256_file(path)
        if not isinstance(actual_sha, str) or not _SHA256_RE.fullmatch(actual_sha):
            raise ValueError(f"Loaded corpus {split} snapshot has an invalid SHA-256.")
        if actual_sha != declared_sha:
            raise ValueError(
                f"Prepared corpus {split} file SHA-256 does not match corpus_manifest.json."
            )

        declared_examples = entry.get(f"{split}_examples")
        if (
            isinstance(declared_examples, bool)
            or not isinstance(declared_examples, int)
            or declared_examples != len(rows)
        ):
            raise ValueError(
                f"Prepared corpus manifest {split} example count does not match the file."
            )
        groups = {row["group_id"] for row in rows}
        declared_groups = entry.get(f"{split}_groups")
        if (
            isinstance(declared_groups, bool)
            or not isinstance(declared_groups, int)
            or declared_groups != len(groups)
        ):
            raise ValueError(
                f"Prepared corpus manifest {split} group count does not match the file."
            )
        group_sets[split] = groups

    if group_sets["train"] & group_sets["eval"]:
        raise ValueError("Prepared corpus manifest contains train/eval repository-group leakage.")
    return manifest


def sha256_adapter_artifacts(root: Path) -> str:
    """Hash deployable adapter/tokenizer files, excluding training checkpoints and manifests."""
    digest = hashlib.sha256()
    files = sorted(
        path for path in root.rglob("*")
        if path.is_file()
        and not any(part.startswith("checkpoint-") for part in path.relative_to(root).parts)
        and path.name not in {"unai_adapter_manifest.json"}
    )
    if not files:
        raise ValueError("Training completed without any adapter/tokenizer artifact files.")
    for path in files:
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def read_specialist_jsonl_with_hash(
    path: Path, specialist: str
) -> tuple[list[dict[str, Any]], str]:
    """Parse a single byte snapshot and return its digest for manifest verification."""
    raw_bytes = path.read_bytes()
    try:
        source_text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{path} is not valid UTF-8 JSONL.") from exc
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line_number, line in enumerate(source_text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_number} is not valid JSON.") from exc
        if not isinstance(row, dict) or row.get("specialist") != specialist:
            raise ValueError(f"{path}:{line_number} does not belong to specialist {specialist!r}.")
        messages = row.get("messages")
        provenance = row.get("provenance")
        verification = row.get("verification")
        record_id = row.get("record_id")
        group_id = row.get("group_id")
        valid_messages = (
            isinstance(messages, list)
            and 2 <= len(messages) <= _MAX_MESSAGES
            and all(
                isinstance(message, dict)
                and isinstance(message.get("role"), str)
                and message.get("role") in {"system", "user", "assistant", "tool"}
                and isinstance(message.get("content"), str)
                and message["content"].strip()
                and len(message["content"]) <= _MAX_MESSAGE_CHARS
                for message in messages
            )
            and any(message["role"] == "user" for message in messages)
            and messages[-1]["role"] == "assistant"
        ) if isinstance(messages, list) and messages else False
        if (
            not valid_messages
            or not isinstance(provenance, dict)
            or provenance.get("consented") is not True
            or not isinstance(provenance.get("source"), str)
            or not provenance.get("source").strip()
            or len(provenance.get("source", "")) > 500
            or not isinstance(provenance.get("license"), str)
            or provenance.get("license", "").strip().casefold() in {"", "unknown", "none", "unlicensed"}
            or len(provenance.get("license", "")) > 120
            or not isinstance(verification, dict)
            or verification.get("status") != "passed"
            or not isinstance(verification.get("check_names"), list)
            or not verification["check_names"]
            or not all(isinstance(name, str) and name.strip() for name in verification["check_names"])
            or not isinstance(record_id, str)
            or not isinstance(group_id, str)
            or not _SHA256_RE.fullmatch(record_id)
            or not _SHA256_RE.fullmatch(group_id)
            or provenance.get("group_id") != group_id
        ):
            raise ValueError(f"{path}:{line_number} is missing verified consent/provenance metadata.")
        expected_record_id = hashlib.sha256(
            json.dumps(
                {"specialist": specialist, "messages": messages},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        if record_id != expected_record_id:
            raise ValueError(f"{path}:{line_number} record_id does not match canonical messages.")
        if record_id in seen:
            raise ValueError(f"{path}:{line_number} duplicates record_id {record_id}.")
        seen.add(record_id)
        rows.append(row)
    if not rows:
        raise ValueError(f"No validated specialist examples in {path}.")
    return rows, hashlib.sha256(raw_bytes).hexdigest()


def read_specialist_jsonl(path: Path, specialist: str) -> list[dict[str, Any]]:
    """Compatibility wrapper for callers that need rows but not manifest checking."""
    rows, _digest = read_specialist_jsonl_with_hash(path, specialist)
    return rows


def encode_messages(tokenizer, messages: list[dict[str, str]], max_length: int) -> dict[str, list[int]]:
    if messages[-1]["role"] != "assistant":
        raise ValueError("Every specialist SFT record must end with an assistant response.")
    prompt = tokenizer.apply_chat_template(
        messages[:-1], tokenize=False, add_generation_prompt=True
    )
    complete = tokenizer.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=False
    )
    prompt_ids = tokenizer(
        prompt, add_special_tokens=False, truncation=False
    )["input_ids"]
    complete_ids = tokenizer(
        complete, add_special_tokens=False, truncation=False
    )["input_ids"]
    if complete_ids[: len(prompt_ids)] != prompt_ids:
        raise ValueError(
            "The tokenizer's chat template does not preserve the prompt token prefix; refusing to train with misaligned labels."
        )
    answer_ids = complete_ids[len(prompt_ids) :]
    if not answer_ids:
        raise ValueError("Assistant continuation tokenized to an empty sequence.")
    if len(answer_ids) > max_length:
        raise ValueError(
            f"Assistant answer has {len(answer_ids)} tokens, exceeding max_length={max_length}; shorten the sample or raise the limit."
        )
    prompt_budget = max_length - len(answer_ids)
    if prompt_budget <= 0:
        raise ValueError("No room remains for prompt context after reserving the assistant answer.")
    kept_prompt = prompt_ids[-prompt_budget:]
    input_ids = kept_prompt + answer_ids
    labels = [-100] * len(kept_prompt) + answer_ids
    return {
        "input_ids": input_ids,
        "attention_mask": [1] * len(input_ids),
        "labels": labels,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True, help="Local model path or Hugging Face model ID")
    parser.add_argument("--revision", default=None, help="Optional immutable base-model revision/commit")
    parser.add_argument("--specialist", required=True, choices=SPECIALISTS)
    parser.add_argument("--train-file", required=True, type=Path)
    parser.add_argument("--eval-file", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--qlora", action="store_true", help="4-bit NF4; requires supported CUDA and bitsandbytes")
    parser.add_argument("--max-length", type=int, default=2048)
    parser.add_argument("--epochs", type=float, default=2)
    parser.add_argument("--learning-rate", type=float, default=2e-4)
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--grad-accum", type=int, default=16)
    parser.add_argument("--lora-r", type=int, default=16)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--target-modules",
        default="q_proj,k_proj,v_proj,o_proj,gate_proj,up_proj,down_proj",
        help="Comma-separated module suffixes for PEFT LoRA injection.",
    )
    args = parser.parse_args()

    if args.max_length < 32 or args.max_length > 8192:
        raise SystemExit("--max-length must be between 32 and 8192.")
    if args.epochs <= 0 or args.learning_rate <= 0 or args.batch_size < 1 or args.grad_accum < 1 or args.lora_r < 1:
        raise SystemExit("Training hyperparameters must be positive.")
    if args.train_file.resolve() == args.eval_file.resolve():
        raise SystemExit("Training and evaluation files must be different.")

    train_rows, train_file_sha256 = read_specialist_jsonl_with_hash(
        args.train_file, args.specialist
    )
    eval_rows, eval_file_sha256 = read_specialist_jsonl_with_hash(
        args.eval_file, args.specialist
    )
    verify_corpus_manifest(
        args.train_file,
        args.eval_file,
        args.specialist,
        train_rows,
        eval_rows,
        train_file_sha256,
        eval_file_sha256,
    )
    train_groups = {row["group_id"] for row in train_rows}
    eval_groups = {row["group_id"] for row in eval_rows}
    if train_groups & eval_groups:
        raise SystemExit("Train/eval repository groups overlap; refusing a leakage-prone run.")
    train_ids = {row["record_id"] for row in train_rows}
    eval_ids = {row["record_id"] for row in eval_rows}
    if train_ids & eval_ids:
        raise SystemExit("Train/eval record IDs overlap.")

    try:
        import torch
        from datasets import Dataset
        from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
        from transformers import (
            AutoModelForCausalLM,
            AutoTokenizer,
            Trainer,
            TrainingArguments,
        )
    except ImportError as exc:
        raise SystemExit("Install training extras: pip install -e './apps/engine[train]'") from exc

    if args.qlora and not torch.cuda.is_available():
        raise SystemExit("QLoRA requires CUDA. Do not run model training on the target Android phone.")

    tokenizer_kwargs: dict[str, Any] = {"use_fast": True}
    model_kwargs: dict[str, Any] = {"low_cpu_mem_usage": True}
    if args.revision:
        tokenizer_kwargs["revision"] = args.revision
        model_kwargs["revision"] = args.revision
    tokenizer = AutoTokenizer.from_pretrained(args.model, **tokenizer_kwargs)
    if tokenizer.pad_token is None:
        if tokenizer.eos_token is None:
            raise SystemExit("Tokenizer must define eos_token or pad_token.")
        tokenizer.pad_token = tokenizer.eos_token
    tokenizer.padding_side = "right"

    if args.qlora:
        from transformers import BitsAndBytesConfig

        dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
        model_kwargs.update(
            {
                "quantization_config": BitsAndBytesConfig(
                    load_in_4bit=True,
                    bnb_4bit_quant_type="nf4",
                    bnb_4bit_compute_dtype=dtype,
                    bnb_4bit_use_double_quant=True,
                ),
                "device_map": "auto",
            }
        )
    else:
        model_kwargs["torch_dtype"] = (
            torch.bfloat16
            if torch.cuda.is_available() and torch.cuda.is_bf16_supported()
            else torch.float16
            if torch.cuda.is_available()
            else torch.float32
        )
        if torch.cuda.is_available():
            model_kwargs["device_map"] = "auto"

    model = AutoModelForCausalLM.from_pretrained(args.model, **model_kwargs)
    if args.qlora:
        model = prepare_model_for_kbit_training(model)
    model.config.use_cache = False

    target_modules = [item.strip() for item in args.target_modules.split(",") if item.strip()]
    if not target_modules:
        raise SystemExit("--target-modules must contain at least one module suffix.")
    model = get_peft_model(
        model,
        LoraConfig(
            r=args.lora_r,
            lora_alpha=args.lora_r * 2,
            lora_dropout=0.05,
            bias="none",
            task_type="CAUSAL_LM",
            target_modules=target_modules,
        ),
    )
    model.print_trainable_parameters()

    encoded_train = [
        encode_messages(tokenizer, row["messages"], args.max_length) for row in train_rows
    ]
    encoded_eval = [
        encode_messages(tokenizer, row["messages"], args.max_length) for row in eval_rows
    ]

    def collate(batch: list[dict[str, list[int]]]) -> dict[str, Any]:
        width = max(len(item["input_ids"]) for item in batch)
        pad_id = tokenizer.pad_token_id
        input_ids = []
        attention_masks = []
        labels = []
        for item in batch:
            padding = width - len(item["input_ids"])
            input_ids.append(item["input_ids"] + [pad_id] * padding)
            attention_masks.append(item["attention_mask"] + [0] * padding)
            labels.append(item["labels"] + [-100] * padding)
        return {
            "input_ids": torch.tensor(input_ids, dtype=torch.long),
            "attention_mask": torch.tensor(attention_masks, dtype=torch.long),
            "labels": torch.tensor(labels, dtype=torch.long),
        }

    bf16 = bool(torch.cuda.is_available() and torch.cuda.is_bf16_supported())
    training_args = TrainingArguments(
        output_dir=str(args.output_dir),
        num_train_epochs=args.epochs,
        learning_rate=args.learning_rate,
        per_device_train_batch_size=args.batch_size,
        per_device_eval_batch_size=1,
        gradient_accumulation_steps=args.grad_accum,
        gradient_checkpointing=True,
        logging_steps=10,
        save_strategy="epoch",
        eval_strategy="epoch",
        load_best_model_at_end=True,
        metric_for_best_model="eval_loss",
        greater_is_better=False,
        save_total_limit=2,
        report_to="none",
        bf16=bf16,
        fp16=bool(torch.cuda.is_available() and not bf16),
        optim="paged_adamw_8bit" if args.qlora else "adamw_torch",
        remove_unused_columns=False,
        seed=args.seed,
        data_seed=args.seed,
        dataloader_num_workers=0,
    )
    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=Dataset.from_list(encoded_train),
        eval_dataset=Dataset.from_list(encoded_eval),
        data_collator=collate,
    )
    train_result = trainer.train()
    eval_metrics = trainer.evaluate()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    trainer.save_model(str(args.output_dir))
    tokenizer.save_pretrained(args.output_dir)

    def package_version(name: str) -> str | None:
        try:
            return importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            return None

    adapter_artifacts_sha256 = sha256_adapter_artifacts(args.output_dir)
    manifest = {
        "schema": "unai.specialist-adapter.v1",
        "adapter_artifacts_sha256": adapter_artifacts_sha256,
        "specialist": args.specialist,
        "base_model": args.model,
        "base_model_revision": args.revision,
        "method": "QLoRA-4bit" if args.qlora else "LoRA",
        "rank": args.lora_r,
        "target_modules": target_modules,
        "max_length": args.max_length,
        "seed": args.seed,
        "epochs": args.epochs,
        "learning_rate": args.learning_rate,
        "batch_size": args.batch_size,
        "gradient_accumulation": args.grad_accum,
        "train_examples": len(train_rows),
        "eval_examples": len(eval_rows),
        "train_groups": len(train_groups),
        "eval_groups": len(eval_groups),
        "train_record_ids_sha256": hashlib.sha256(
            "\n".join(sorted(train_ids)).encode("utf-8")
        ).hexdigest(),
        "eval_record_ids_sha256": hashlib.sha256(
            "\n".join(sorted(eval_ids)).encode("utf-8")
        ).hexdigest(),
        "train_file_sha256": sha256_file(args.train_file),
        "eval_file_sha256": sha256_file(args.eval_file),
        "train_metrics": train_result.metrics,
        "eval_metrics": eval_metrics,
        "library_versions": {
            "torch": package_version("torch"),
            "transformers": package_version("transformers"),
            "peft": package_version("peft"),
            "datasets": package_version("datasets"),
            "trl": package_version("trl"),
        },
        "hardware": {
            "cuda_available": bool(torch.cuda.is_available()),
            "gpu_name": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
            "peak_cuda_memory_allocated_bytes": (
                int(torch.cuda.max_memory_allocated()) if torch.cuda.is_available() else None
            ),
        },
        "release_status": "training-complete-not-yet-evaluated-for-promotion",
        "note": "This adapter requires the original licensed base model. A passing eval loss is not a verified coding-task improvement.",
    }
    (args.output_dir / "unai_adapter_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
