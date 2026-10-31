#!/usr/bin/env python3
"""Filter preference pairs to examples with evidence-backed outcomes and consent."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from typing import Any

from training.prepare_dataset import sanitise

def prepare(source: Path, output: Path) -> dict[str, int]:
    counts = {"input": 0, "kept": 0, "rejected": 0, "redactions": 0}
    output.parent.mkdir(parents=True, exist_ok=True)
    with source.open("r", encoding="utf-8") as src, output.open("w", encoding="utf-8") as dst:
        for line in src:
            if not line.strip():
                continue
            counts["input"] += 1
            try:
                row: dict[str, Any] = json.loads(line)
                provenance = row.get("provenance") or {}
                chosen_check = row.get("chosen_verification") or {}
                rejected_check = row.get("rejected_verification") or {}
                license_name = provenance.get("license", "")
                turns = [row.get(k) for k in ("prompt", "chosen", "rejected")]
                valid_turns = all(
                    isinstance(v, list) and v and all(
                        isinstance(m, dict) and m.get("role") in {"user", "assistant", "tool"}
                        and isinstance(m.get("content"), str) and m["content"].strip() for m in v
                    ) for v in turns
                )
                accepted = (
                    valid_turns and turns[1][-1]["role"] == "assistant" and turns[2][-1]["role"] == "assistant"
                    and chosen_check.get("passed") is True and rejected_check.get("passed") is False
                    and provenance.get("consented") is True and bool(provenance.get("source"))
                    and isinstance(license_name, str)
                    and license_name.lower() not in {"", "unknown", "none", "unlicensed"}
                )
                if not accepted:
                    counts["rejected"] += 1
                    continue
                cleaned = {}
                for key in ("prompt", "chosen", "rejected"):
                    messages = []
                    for message in row[key]:
                        content, number = sanitise(message["content"])
                        counts["redactions"] += number
                        messages.append({"role": message["role"], "content": content})
                    cleaned[key] = messages
                dst.write(json.dumps(cleaned, ensure_ascii=False) + "\n")
                counts["kept"] += 1
            except (json.JSONDecodeError, TypeError, AttributeError, KeyError):
                counts["rejected"] += 1
    return counts

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = prepare(args.input, args.output)
    print(json.dumps(report, indent=2))
    if report["kept"] == 0:
        raise SystemExit("No preference examples passed evidence and consent checks.")

if __name__ == "__main__":
    main()
