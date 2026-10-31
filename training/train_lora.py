#!/usr/bin/env python3
"""LoRA/QLoRA SFT for an unAI specialist policy. Run on training hardware, not a phone."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from typing import Any

def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not rows:
        raise ValueError(f"No examples in {path}")
    return rows

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True, help="Local model path or Hugging Face model id")
    parser.add_argument("--train-file", required=True, type=Path)
    parser.add_argument("--eval-file", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--qlora", action="store_true", help="4-bit NF4; requires supported CUDA + bitsandbytes")
    parser.add_argument("--max-length", type=int, default=2048)
    parser.add_argument("--epochs", type=float, default=2)
    parser.add_argument("--learning-rate", type=float, default=2e-4)
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--grad-accum", type=int, default=16)
    parser.add_argument("--lora-r", type=int, default=16)
    args = parser.parse_args()

    try:
        import torch
        from datasets import Dataset
        from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
        from transformers import AutoModelForCausalLM, AutoTokenizer, Trainer, TrainingArguments
    except ImportError as exc:
        raise SystemExit("Install train extras: pip install -e './apps/engine[train]'") from exc

    if args.qlora and not torch.cuda.is_available():
        raise SystemExit("QLoRA requires CUDA. Do not run model training on the target Android phone.")

    tokenizer = AutoTokenizer.from_pretrained(args.model, use_fast=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    kwargs: dict[str, Any] = {"low_cpu_mem_usage": True}
    if args.qlora:
        from transformers import BitsAndBytesConfig
        dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
        kwargs.update({
            "quantization_config": BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                bnb_4bit_compute_dtype=dtype, bnb_4bit_use_double_quant=True),
            "device_map": "auto",
        })
    else:
        kwargs["torch_dtype"] = torch.bfloat16 if torch.cuda.is_available() and torch.cuda.is_bf16_supported() else (
            torch.float16 if torch.cuda.is_available() else torch.float32
        )
        if torch.cuda.is_available():
            kwargs["device_map"] = "auto"
    model = AutoModelForCausalLM.from_pretrained(args.model, **kwargs)
    if args.qlora:
        model = prepare_model_for_kbit_training(model)
    model = get_peft_model(model, LoraConfig(
        r=args.lora_r, lora_alpha=args.lora_r * 2, lora_dropout=0.05,
        bias="none", task_type="CAUSAL_LM",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
    ))
    model.print_trainable_parameters()

    def encode(row: dict[str, Any]) -> dict[str, Any]:
        messages = row["messages"]
        if not isinstance(messages, list) or len(messages) < 2 or messages[-1].get("role") != "assistant":
            raise ValueError("Every SFT record must end with an assistant response.")
        prefix = tokenizer.apply_chat_template(messages[:-1], tokenize=False, add_generation_prompt=True)
        complete = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=False)
        prefix_ids = tokenizer(prefix, truncation=True, max_length=args.max_length)["input_ids"]
        ids = tokenizer(complete, truncation=True, max_length=args.max_length)["input_ids"]
        boundary = min(len(prefix_ids), len(ids))
        labels = [-100] * boundary + ids[boundary:]
        if len(labels) != len(ids) or all(x == -100 for x in labels):
            raise ValueError("Assistant answer was truncated; increase max-length or clean the sample.")
        return {"input_ids": ids, "attention_mask": [1] * len(ids), "labels": labels}

    train_rows = [encode(row) for row in read_jsonl(args.train_file)]
    eval_rows = [encode(row) for row in read_jsonl(args.eval_file)] if args.eval_file else None
    bf16 = bool(torch.cuda.is_available() and torch.cuda.is_bf16_supported())
    train_args = TrainingArguments(
        output_dir=str(args.output_dir), num_train_epochs=args.epochs, learning_rate=args.learning_rate,
        per_device_train_batch_size=args.batch_size, per_device_eval_batch_size=1,
        gradient_accumulation_steps=args.grad_accum, gradient_checkpointing=True,
        logging_steps=10, save_strategy="epoch",
        evaluation_strategy="epoch" if eval_rows is not None else "no",
        report_to="none", bf16=bf16, fp16=bool(torch.cuda.is_available() and not bf16),
        optim="paged_adamw_8bit" if args.qlora else "adamw_torch", remove_unused_columns=False,
    )
    trainer = Trainer(model=model, args=train_args, train_dataset=Dataset.from_list(train_rows),
        eval_dataset=Dataset.from_list(eval_rows) if eval_rows is not None else None,
        data_collator=lambda rows: tokenizer.pad({"input_ids":[r["input_ids"] for r in rows],
            "attention_mask":[r["attention_mask"] for r in rows], "labels":[r["labels"] for r in rows]},
            padding=True, return_tensors="pt"))
    trainer.train()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(args.output_dir)
    tokenizer.save_pretrained(args.output_dir)
    (args.output_dir / "unai_adapter_manifest.json").write_text(json.dumps({
        "base_model": args.model, "method": "QLoRA-4bit" if args.qlora else "LoRA",
        "rank": args.lora_r, "max_length": args.max_length, "train_examples": len(train_rows),
        "eval_examples": len(eval_rows) if eval_rows else 0,
        "note": "Adapter requires the original base model; validate on held-out coding tasks before release."
    }, indent=2), encoding="utf-8")

if __name__ == "__main__":
    main()
