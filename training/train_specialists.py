#!/usr/bin/env python3
"""Run the three independent UnAi specialist LoRA/QLoRA jobs sequentially."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SPECIALISTS = ("next_action", "planner", "verifier")
DEFAULT_CONFIG = Path(__file__).with_name("model_config.json")


def load_config(path: Path = DEFAULT_CONFIG) -> dict[str, Any]:
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read training model config {path}: {exc}") from exc
    if config.get("schema") != "unai.training-model-config.v1":
        raise ValueError("Unsupported training model config schema.")
    model = config.get("default_model")
    training = config.get("training")
    if not isinstance(model, dict) or not isinstance(training, dict):
        raise ValueError("Training config must contain default_model and training objects.")
    for field in ("model_id", "revision", "license", "license_url"):
        if not isinstance(model.get(field), str) or not model[field].strip():
            raise ValueError(f"Training model config is missing {field}.")
    if len(model["revision"]) != 40 or any(c not in "0123456789abcdef" for c in model["revision"]):
        raise ValueError("Base model revision must be a full immutable 40-character commit SHA.")
    return config


def build_command(
    specialist: str,
    *,
    model: str,
    revision: str,
    data_dir: Path,
    output_root: Path,
    training: dict[str, Any],
    qlora: bool = True,
    resume: bool = False,
) -> list[str]:
    if specialist not in SPECIALISTS:
        raise ValueError(f"Unknown specialist: {specialist}")
    train_file = data_dir / f"{specialist}.train.jsonl"
    eval_file = data_dir / f"{specialist}.eval.jsonl"
    manifest = data_dir / "corpus_manifest.json"
    missing = [str(path) for path in (train_file, eval_file, manifest) if not path.is_file()]
    if missing:
        raise FileNotFoundError("Prepared corpus is incomplete: " + ", ".join(missing))
    command = [
        sys.executable, str(ROOT / "training" / "train_lora.py"),
        "--model", model, "--revision", revision, "--specialist", specialist,
        "--train-file", str(train_file), "--eval-file", str(eval_file),
        "--output-dir", str(output_root / f"unai-{specialist}"),
        "--max-length", str(training.get("max_length", 2048)),
        "--epochs", str(training.get("epochs", 2)),
        "--learning-rate", str(training.get("learning_rate", 2e-4)),
        "--batch-size", str(training.get("batch_size", 1)),
        "--grad-accum", str(training.get("gradient_accumulation", 16)),
        "--lora-r", str(training.get("lora_rank", 16)),
        "--seed", str(training.get("seed", 20261105)),
        "--target-modules", ",".join(training.get("target_modules", [])),
    ]
    if qlora:
        command.append("--qlora")
    if resume:
        command.extend(["--resume-from-checkpoint", "latest"])
    return command


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data" / "specialists")
    parser.add_argument("--output-root", type=Path, default=ROOT / "artifacts")
    parser.add_argument("--model")
    parser.add_argument("--revision")
    parser.add_argument("--specialists", nargs="+", choices=SPECIALISTS, default=list(SPECIALISTS))
    parser.add_argument("--no-qlora", action="store_true", help="Use ordinary LoRA instead of 4-bit QLoRA.")
    parser.add_argument("--resume", action="store_true", help="Resume each specialist from its latest checkpoint.")
    parser.add_argument("--dry-run", action="store_true", help="Validate inputs and print commands without loading model weights.")
    args = parser.parse_args()

    config = load_config(args.config)
    model_cfg = config["default_model"]
    model = args.model or model_cfg["model_id"]
    revision = args.revision or model_cfg["revision"]
    if len(revision) != 40 or any(c not in "0123456789abcdef" for c in revision):
        raise SystemExit("--revision must be a full immutable 40-character commit SHA.")
    commands = [
        build_command(
            specialist, model=model, revision=revision,
            data_dir=args.data_dir, output_root=args.output_root,
            training=config["training"], qlora=not args.no_qlora, resume=args.resume,
        )
        for specialist in args.specialists
    ]
    if args.dry_run:
        print(json.dumps({"model": model, "revision": revision, "license": model_cfg["license"],
                          "commands": commands, "execution": "not_started"}, indent=2))
        return
    args.output_root.mkdir(parents=True, exist_ok=True)
    for specialist, command in zip(args.specialists, commands):
        print(f"Starting {specialist} specialist training.", flush=True)
        subprocess.run(command, cwd=ROOT, check=True)
        print(f"Completed {specialist} training process. Review its adapter manifest before promotion.", flush=True)


if __name__ == "__main__":
    main()
