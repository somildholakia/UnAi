#!/usr/bin/env python3
"""Evidence-backed DPO preference tuning. Run on a CUDA training machine."""
from __future__ import annotations
import argparse
import json
from pathlib import Path

def load_rows(path: Path):
    from datasets import Dataset
    rows=[json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not rows:
        raise ValueError(f"No preference examples in {path}")
    return Dataset.from_list(rows)

def main() -> None:
    parser=argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--train-file", required=True, type=Path)
    parser.add_argument("--eval-file", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--epochs", type=float, default=1)
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--grad-accum", type=int, default=16)
    parser.add_argument("--learning-rate", type=float, default=5e-6)
    parser.add_argument("--lora-r", type=int, default=16)
    parser.add_argument("--max-length", type=int, default=2048)
    args=parser.parse_args()
    try:
        import torch
        from peft import LoraConfig
        from transformers import AutoTokenizer
        from trl import DPOConfig, DPOTrainer
    except ImportError as exc:
        raise SystemExit("Install train extras including TRL: pip install -e './apps/engine[train]'") from exc
    if not torch.cuda.is_available():
        raise SystemExit("DPO training is configured for CUDA training hardware, not the 6 GB phone.")
    tokenizer=AutoTokenizer.from_pretrained(args.model, use_fast=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token=tokenizer.eos_token
    bf16=torch.cuda.is_bf16_supported()
    config=DPOConfig(
        output_dir=str(args.output_dir), num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch_size, per_device_eval_batch_size=1,
        gradient_accumulation_steps=args.grad_accum, learning_rate=args.learning_rate,
        max_length=args.max_length, max_prompt_length=max(128,args.max_length//2),
        beta=0.1, logging_steps=10, save_strategy="epoch",
        eval_strategy="epoch" if args.eval_file else "no", report_to="none",
        bf16=bf16, fp16=not bf16, gradient_checkpointing=True, remove_unused_columns=False,
    )
    trainer=DPOTrainer(
        model=args.model, args=config, train_dataset=load_rows(args.train_file),
        eval_dataset=load_rows(args.eval_file) if args.eval_file else None,
        processing_class=tokenizer,
        peft_config=LoraConfig(r=args.lora_r,lora_alpha=args.lora_r*2,lora_dropout=0.05,
            bias="none",task_type="CAUSAL_LM",
            target_modules=["q_proj","k_proj","v_proj","o_proj","gate_proj","up_proj","down_proj"]),
    )
    trainer.train()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    trainer.save_model(str(args.output_dir))
    tokenizer.save_pretrained(args.output_dir)
    (args.output_dir/"dpo_manifest.json").write_text(json.dumps({
        "base_model":args.model,"method":"DPO+LoRA",
        "train_pairs":len(trainer.train_dataset),
        "eval_pairs":len(trainer.eval_dataset) if trainer.eval_dataset is not None else 0,
        "note":"Run the held-out acceptance suite before combining this adapter with SFT adapters."
    },indent=2),encoding="utf-8")

if __name__ == "__main__":
    main()
