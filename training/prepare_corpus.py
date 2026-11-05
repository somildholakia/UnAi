#!/usr/bin/env python3
"""Create leakage-resistant, consented specialist SFT splits with provenance.

A project/repository group is kept entirely in either train or evaluation.
This tool does not make unverified tasks eligible for training.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from training.prepare_dataset import sanitise

SPECIALISTS = ("next_action", "planner", "verifier")
_ALLOWED_ROLES = {"system", "user", "assistant", "tool"}
_MAX_LINE_BYTES = 1_000_000
_MAX_MESSAGES = 100
_MAX_MESSAGE_CHARS = 100_000

_MAX_INPUT_BYTES = 2 * 1024 * 1024 * 1024


def _read_bounded_jsonl_line(handle, digest):
    """Read one JSONL record without ever buffering more than one bounded chunk."""
    first = handle.readline(_MAX_LINE_BYTES + 1)
    if not first:
        return None, False, 0, False

    digest.update(first)
    total_bytes = len(first)
    has_content = bool(first.strip())
    oversized = len(first) > _MAX_LINE_BYTES
    chunk = first

    # Once a record is known to be too large, drain it in bounded chunks without
    # retaining its contents. Every byte is still included in the source digest.
    while oversized and chunk and not chunk.endswith(b"\n"):
        chunk = handle.readline(_MAX_LINE_BYTES + 1)
        if not chunk:
            break
        digest.update(chunk)
        total_bytes += len(chunk)
        has_content = has_content or bool(chunk.strip())

    return (None if oversized else first), oversized, total_bytes, has_content


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _jsonl_payload(rows: list[dict[str, Any]]) -> bytes:
    return "".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        for row in rows
    ).encode("utf-8")


def _validated_messages(value: Any) -> list[dict[str, str]] | None:
    if not isinstance(value, list) or not 2 <= len(value) <= _MAX_MESSAGES:
        return None
    cleaned: list[dict[str, str]] = []
    for item in value:
        if (
            not isinstance(item, dict)
            or item.get("role") not in _ALLOWED_ROLES
            or not isinstance(item.get("content"), str)
            or not item["content"].strip()
            or len(item["content"]) > _MAX_MESSAGE_CHARS
        ):
            return None
        text, _count = sanitise(item["content"])
        if not text.strip():
            return None
        cleaned.append({"role": item["role"], "content": text})
    if cleaned[-1]["role"] != "assistant":
        return None
    if not any(item["role"] == "user" for item in cleaned):
        return None
    return cleaned


def _provenance(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict) or value.get("consented") is not True:
        return None
    source = value.get("source")
    license_name = value.get("license")
    group = (
        value.get("group_id")
        or value.get("repository")
        or value.get("project")
    )
    if (
        not isinstance(source, str)
        or not source.strip()
        or len(source) > 500
        or not isinstance(license_name, str)
        or license_name.strip().casefold() in {"", "unknown", "none", "unlicensed"}
        or len(license_name) > 120
        or not isinstance(group, str)
        or not group.strip()
        or len(group) > 500
    ):
        return None
    clean_source, _ = sanitise(source.strip())
    clean_license, _ = sanitise(license_name.strip())
    if not clean_source.strip() or "[REDACTED_SECRET]" in clean_license:
        return None

    source_type = value.get("source_type")
    optional_text: dict[str, str] = {}
    for field, maximum in (
        ("source_type", 40),
        ("source_url", 2_000),
        ("source_revision", 240),
        ("license_url", 2_000),
        ("generator", 200),
        ("generator_version", 80),
        ("rights_basis", 500),
        ("authorization_reference", 500),
    ):
        raw = value.get(field)
        if raw is None:
            continue
        if not isinstance(raw, str) or not raw.strip() or len(raw) > maximum:
            return None
        cleaned, _ = sanitise(raw.strip())
        if not cleaned.strip() or "[REDACTED_SECRET]" in cleaned:
            return None
        optional_text[field] = cleaned

    if source_type is not None:
        if source_type not in {"synthetic", "public", "authorized_local"}:
            return None
        if source_type == "synthetic" and not all(
            optional_text.get(field)
            for field in ("generator", "generator_version", "rights_basis")
        ):
            return None
        if source_type == "public":
            source_url = optional_text.get("source_url", "")
            license_url = optional_text.get("license_url", "")
            revision = optional_text.get("source_revision", "")
            for url in (source_url, license_url):
                parsed = urlsplit(url)
                if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
                    return None
            if not revision:
                return None
        if source_type == "authorized_local" and not optional_text.get("authorization_reference"):
            return None

    result = {
        "source": clean_source.strip(),
        "license": clean_license.strip(),
        "consented": True,
        "group_id": _sha256(group.strip().casefold().encode("utf-8")),
    }
    result.update(optional_text)
    return result


def _verified(value: Any, outcome: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict) or outcome != "passed" or value.get("status") != "passed":
        return None
    checks = value.get("checks")
    if not isinstance(checks, list) or not checks:
        return None
    names: list[str] = []
    for check in checks:
        if (
            not isinstance(check, dict)
            or check.get("passed") is not True
            or not isinstance(check.get("name"), str)
            or not check["name"].strip()
        ):
            return None
        names.append(check["name"].strip()[:120])
    result = {"status": "passed", "check_names": names}
    method = value.get("method")
    if method is not None:
        if not isinstance(method, str) or not method.strip() or len(method) > 120:
            return None
        cleaned_method, _ = sanitise(method.strip())
        if not cleaned_method.strip() or "[REDACTED_SECRET]" in cleaned_method:
            return None
        result["method"] = cleaned_method
    return result


def _split_groups(groups: set[str], *, seed: int, specialist: str, fraction: float) -> tuple[set[str], set[str]]:
    if len(groups) < 2:
        raise ValueError(
            f"Specialist {specialist!r} needs verified examples from at least two distinct repository/project groups."
        )
    ordered = sorted(
        groups,
        key=lambda group: _sha256(f"{seed}\0{specialist}\0{group}".encode("utf-8")),
    )
    eval_count = max(1, round(len(ordered) * fraction))
    eval_count = min(len(ordered) - 1, eval_count)
    eval_groups = set(ordered[:eval_count])
    return set(ordered[eval_count:]), eval_groups


def prepare(
    source: Path,
    output_dir: Path,
    *,
    seed: int = 20261103,
    eval_fraction: float = 0.2,
) -> dict[str, Any]:
    if not 0.05 <= eval_fraction <= 0.5:
        raise ValueError("eval_fraction must be between 0.05 and 0.5.")
    if not source.is_file():
        raise FileNotFoundError(source)
    if source.stat().st_size > _MAX_INPUT_BYTES:
        raise ValueError("Corpus input exceeds the 2 GiB preparation limit.")

    counts = {
        "input": 0,
        "accepted": 0,
        "rejected": 0,
        "duplicates": 0,
        "redactions": 0,
    }
    records: dict[str, list[dict[str, Any]]] = {name: [] for name in SPECIALISTS}
    seen: set[str] = set()
    source_digest = hashlib.sha256()
    source_bytes = 0

    with source.open("rb") as handle:
        while True:
            raw_line, oversized, record_bytes, has_content = _read_bounded_jsonl_line(
                handle, source_digest
            )
            if raw_line is None and not oversized:
                break
            source_bytes += record_bytes
            if source_bytes > _MAX_INPUT_BYTES:
                raise ValueError("Corpus input exceeds the 2 GiB preparation limit.")
            if not has_content:
                continue
            counts["input"] += 1
            if oversized:
                counts["rejected"] += 1
                continue
            assert raw_line is not None
            try:
                row = json.loads(raw_line.decode("utf-8"))
                if not isinstance(row, dict):
                    raise ValueError("Record must be a JSON object.")
                specialist = row.get("specialist")
                messages = _validated_messages(row.get("messages"))
                provenance = _provenance(row.get("provenance"))
                verification = _verified(row.get("verification"), row.get("outcome"))
                if (
                    specialist not in SPECIALISTS
                    or messages is None
                    or provenance is None
                    or verification is None
                ):
                    counts["rejected"] += 1
                    continue

                redaction_count = 0
                for source_message, cleaned_message in zip(row["messages"], messages):
                    _cleaned, redactions = sanitise(source_message["content"])
                    redaction_count += redactions
                counts["redactions"] += redaction_count

                record_identity = {
                    "specialist": specialist,
                    "messages": messages,
                }
                record_id = _sha256(
                    json.dumps(
                        record_identity,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                )
                if record_id in seen:
                    counts["duplicates"] += 1
                    continue
                seen.add(record_id)
                records[specialist].append(
                    {
                        "specialist": specialist,
                        "messages": messages,
                        "record_id": record_id,
                        "group_id": provenance["group_id"],
                        "provenance": provenance,
                        "verification": verification,
                    }
                )
                counts["accepted"] += 1
            except (UnicodeDecodeError, json.JSONDecodeError, TypeError, AttributeError, ValueError):
                counts["rejected"] += 1

    # Validate and serialize every specialist before touching the output directory.
    specialist_manifest: dict[str, Any] = {}
    prepared_payloads: dict[Path, bytes] = {}
    total_train = 0
    total_eval = 0

    for specialist in SPECIALISTS:
        rows = records[specialist]
        groups = {row["group_id"] for row in rows}
        train_groups, eval_groups = _split_groups(
            groups, seed=seed, specialist=specialist, fraction=eval_fraction
        )
        train_rows = [row for row in rows if row["group_id"] in train_groups]
        eval_rows = [row for row in rows if row["group_id"] in eval_groups]
        if not train_rows or not eval_rows:
            raise ValueError(f"Specialist {specialist!r} produced an empty train/eval split.")

        train_path = output_dir / f"{specialist}.train.jsonl"
        eval_path = output_dir / f"{specialist}.eval.jsonl"
        train_payload = _jsonl_payload(train_rows)
        eval_payload = _jsonl_payload(eval_rows)
        train_sha = _sha256(train_payload)
        eval_sha = _sha256(eval_payload)

        if {row["group_id"] for row in train_rows} & {row["group_id"] for row in eval_rows}:
            raise AssertionError("Repository/project group leakage detected.")

        specialist_manifest[specialist] = {
            "train_examples": len(train_rows),
            "eval_examples": len(eval_rows),
            "train_groups": len(train_groups),
            "eval_groups": len(eval_groups),
            "train_file": train_path.name,
            "train_sha256": train_sha,
            "eval_file": eval_path.name,
            "eval_sha256": eval_sha,
        }
        prepared_payloads[train_path] = train_payload
        prepared_payloads[eval_path] = eval_payload
        total_train += len(train_rows)
        total_eval += len(eval_rows)

    manifest = {
        "schema": "unai.specialist-sft-corpus.v1",
        "seed": seed,
        "eval_fraction": eval_fraction,
        "source_file_sha256": source_digest.hexdigest(),
        "source_file_bytes": source_bytes,
        "input_records": counts["input"],
        "accepted_records": counts["accepted"],
        "rejected_records": counts["rejected"],
        "duplicate_records": counts["duplicates"],
        "redactions": counts["redactions"],
        "train_examples": total_train,
        "eval_examples": total_eval,
        "specialists": specialist_manifest,
        "split_policy": "deterministic repository/project-group split; no group appears in both train and eval",
        "privacy_note": "group IDs are SHA-256 pseudonyms; review sanitized examples manually before training",
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    for path, payload in prepared_payloads.items():
        path.write_bytes(payload)
    (output_dir / "corpus_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--seed", type=int, default=20261103)
    parser.add_argument("--eval-fraction", type=float, default=0.2)
    args = parser.parse_args()
    report = prepare(
        args.input,
        args.output_dir,
        seed=args.seed,
        eval_fraction=args.eval_fraction,
    )
    print(json.dumps(report, indent=2))
    if report["accepted_records"] == 0:
        raise SystemExit("No examples passed consent, provenance and verification checks.")


if __name__ == "__main__":
    main()
