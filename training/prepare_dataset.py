#!/usr/bin/env python3
"""Prepare SFT records from explicitly consented, verified trajectories."""
from __future__ import annotations
import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|access[_-]?token|secret|password)\s*[:=]\s*['\"]?([A-Za-z0-9_./+=-]{12,})"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"),
    # Provider-specific credentials that are often pasted without a key name.
    re.compile(r"(?i)\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    re.compile(r"(?i)\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}\b"),
    re.compile(r"(?i)\bwhsec_[A-Za-z0-9]{16,}\b"),
    re.compile(r"\b(?:npm_[A-Za-z0-9]{20,}|npm-[A-Za-z0-9]{20,})\b"),
    re.compile(r"\bhf_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{16,}\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]{20,}={0,2}"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    re.compile(r"\b[A-Za-z0-9_-]{24}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{25,}\b"),
    # Connection strings and PEM private-key blocks are redacted as whole secrets.
    re.compile(r"(?i)\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?):\/\/[^/\s:@]+:[^/\s@]{8,}@[^/\s]+"),
    re.compile(r"(?im)-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----[\s\S]{20,}?-----END (?:[A-Z0-9 ]+ )?PRIVATE KEY-----"),
    re.compile(r"(?i)[?&](?:sig|signature)=[^&\s]{16,}"),
]

def sanitise(text: str) -> tuple[str, int]:
    count = 0
    for pattern in SECRET_PATTERNS:
        text, n = pattern.subn("[REDACTED_SECRET]", text)
        count += n
    return text, count

def prepare(source: Path, output: Path) -> dict[str, int]:
    counts = {"input": 0, "kept": 0, "rejected": 0, "redactions": 0, "duplicates": 0}
    seen: set[str] = set()
    output.parent.mkdir(parents=True, exist_ok=True)
    with source.open("r", encoding="utf-8") as src, output.open("w", encoding="utf-8") as dst:
        for line in src:
            if not line.strip():
                continue
            counts["input"] += 1
            try:
                row: dict[str, Any] = json.loads(line)
                messages = row.get("messages")
                provenance = row.get("provenance") or {}
                verification = row.get("verification") or {}
                checks = verification.get("checks") or []
                valid_messages = isinstance(messages, list) and len(messages) >= 2 and all(
                    isinstance(m, dict) and m.get("role") in {"system", "user", "assistant", "tool"}
                    and isinstance(m.get("content"), str) and m["content"].strip()
                    for m in messages
                )
                all_checks_pass = bool(checks) and all(
                    isinstance(item, dict) and item.get("passed") is True for item in checks
                )
                license_name = provenance.get("license", "")
                accepted = (
                    valid_messages
                    and row.get("outcome") == "passed"
                    and verification.get("status") == "passed"
                    and all_checks_pass
                    and provenance.get("consented") is True
                    and bool(provenance.get("source"))
                    and isinstance(license_name, str)
                    and license_name.lower() not in {"", "unknown", "none", "unlicensed"}
                )
                if not accepted:
                    counts["rejected"] += 1
                    continue
                cleaned = []
                redactions = 0
                for message in messages:
                    content, n = sanitise(message["content"])
                    redactions += n
                    cleaned.append({"role": message["role"], "content": content})
                counts["redactions"] += redactions
                digest = hashlib.sha256(json.dumps(cleaned, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                if digest in seen:
                    counts["duplicates"] += 1
                    continue
                seen.add(digest)
                dst.write(json.dumps({"messages": cleaned}, ensure_ascii=False) + "\n")
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
        raise SystemExit("No examples passed consent, provenance, and verification checks.")

if __name__ == "__main__":
    main()
