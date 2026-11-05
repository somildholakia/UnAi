#!/usr/bin/env python3
"""Read-only preflight for consented specialist trajectory corpora.

The audit reports metadata only. It never writes prepared splits or prints
conversation text, raw repository identifiers, or provenance source strings.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from training.prepare_corpus import (
    SPECIALISTS,
    _provenance,
    _validated_messages,
    _verified,
    _MAX_LINE_BYTES,
)

_MAX_INPUT_BYTES = 2 * 1024 * 1024 * 1024


def audit(source: Path) -> dict[str, Any]:
    if not source.is_file():
        raise FileNotFoundError(source)
    if source.stat().st_size > _MAX_INPUT_BYTES:
        raise ValueError("Corpus input exceeds the 2 GiB audit limit.")

    digest = hashlib.sha256()
    counts = {
        "input_records": 0,
        "eligible_records": 0,
        "rejected_records": 0,
        "duplicate_records": 0,
        "oversized_records": 0,
    }
    rejected_reasons: dict[str, int] = {}
    per_specialist: dict[str, dict[str, Any]] = {
        role: {"eligible_records": 0, "distinct_groups": 0, "split_ready": False}
        for role in SPECIALISTS
    }
    groups: dict[str, set[str]] = {role: set() for role in SPECIALISTS}
    seen: set[str] = set()

    with source.open("rb") as handle:
        for raw_line in handle:
            digest.update(raw_line)
            if not raw_line.strip():
                continue
            counts["input_records"] += 1
            reason = None
            if len(raw_line) > _MAX_LINE_BYTES:
                counts["oversized_records"] += 1
                reason = "oversized_line"
            else:
                try:
                    row = json.loads(raw_line.decode("utf-8"))
                    if not isinstance(row, dict):
                        reason = "not_object"
                    elif row.get("specialist") not in SPECIALISTS:
                        reason = "unknown_specialist"
                    else:
                        role = row["specialist"]
                        messages = _validated_messages(row.get("messages"))
                        provenance = _provenance(row.get("provenance"))
                        verification = _verified(row.get("verification"), row.get("outcome"))
                        if messages is None:
                            reason = "invalid_messages"
                        elif provenance is None:
                            reason = "missing_consent_or_provenance"
                        elif verification is None:
                            reason = "missing_passing_verification"
                        else:
                            identity = {
                                "specialist": role,
                                "messages": messages,
                            }
                            record_id = hashlib.sha256(
                                json.dumps(
                                    identity,
                                    ensure_ascii=False,
                                    sort_keys=True,
                                    separators=(",", ":"),
                                ).encode("utf-8")
                            ).hexdigest()
                            if record_id in seen:
                                counts["duplicate_records"] += 1
                                reason = "duplicate"
                            else:
                                seen.add(record_id)
                                counts["eligible_records"] += 1
                                per_specialist[role]["eligible_records"] += 1
                                groups[role].add(provenance["group_id"])
                except (UnicodeDecodeError, json.JSONDecodeError, TypeError, AttributeError, ValueError):
                    reason = "malformed_record"

            if reason and reason != "duplicate":
                counts["rejected_records"] += 1
                rejected_reasons[reason] = rejected_reasons.get(reason, 0) + 1

    ready_roles = 0
    for role in SPECIALISTS:
        group_count = len(groups[role])
        per_specialist[role]["distinct_groups"] = group_count
        per_specialist[role]["split_ready"] = (
            per_specialist[role]["eligible_records"] > 0 and group_count >= 2
        )
        ready_roles += int(per_specialist[role]["split_ready"])

    return {
        "schema": "unai.specialist-corpus-audit.v1",
        "source_file_sha256": digest.hexdigest(),
        "source_file_bytes": source.stat().st_size,
        **counts,
        "rejected_reasons": dict(sorted(rejected_reasons.items())),
        "specialists": per_specialist,
        "all_specialists_split_ready": ready_roles == len(SPECIALISTS),
        "ready_specialists": ready_roles,
        "required_specialists": len(SPECIALISTS),
        "read_only": True,
        "limitations": [
            "Consent and license metadata are self-asserted and are not independently authenticated.",
            "Passing verification metadata is not independently replayed by this audit.",
            "Split readiness means at least two repository/project groups, not sufficient training scale or model quality.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(audit(args.input), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
