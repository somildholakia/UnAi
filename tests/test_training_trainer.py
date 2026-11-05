from __future__ import annotations

import hashlib
import json

import pytest

from training.train_lora import (
    encode_messages,
    read_specialist_jsonl,
    read_specialist_jsonl_with_hash,
    sha256_file,
    verify_corpus_manifest,
)


class FakeTokenizer:
    pad_token_id = 0

    def apply_chat_template(self, messages, *, tokenize, add_generation_prompt):
        assert tokenize is False
        prompt = "|".join(f"{item['role']}:{item['content']}" for item in messages)
        return prompt + ("|assistant:" if add_generation_prompt else "|assistant:answer")

    def __call__(self, text, *, add_special_tokens, truncation):
        assert add_special_tokens is False
        assert truncation is False
        if text.endswith("|assistant:"):
            ids = [1, 2, 3, 4]
        elif text.endswith("|assistant:answer"):
            ids = [1, 2, 3, 4, 5, 6]
        else:
            ids = [99]
        return {"input_ids": ids}


def test_encode_messages_masks_prompt_and_keeps_full_assistant_answer_when_truncating_prompt() -> None:
    messages = [
        {"role": "user", "content": "inspect the repo"},
        {"role": "assistant", "content": "answer"},
    ]

    encoded = encode_messages(FakeTokenizer(), messages, max_length=4)

    assert encoded["input_ids"] == [3, 4, 5, 6]
    assert encoded["labels"] == [-100, -100, 5, 6]
    assert encoded["attention_mask"] == [1, 1, 1, 1]


def test_encode_messages_rejects_non_prefix_templates_and_answer_truncation() -> None:
    class BadPrefixTokenizer(FakeTokenizer):
        def __call__(self, text, *, add_special_tokens, truncation):
            if text.endswith("|assistant:"):
                return {"input_ids": [1, 2]}
            return {"input_ids": [1, 9, 3, 4]}

    messages = [
        {"role": "user", "content": "inspect"},
        {"role": "assistant", "content": "answer"},
    ]
    with pytest.raises(ValueError, match="does not preserve the prompt token prefix"):
        encode_messages(BadPrefixTokenizer(), messages, max_length=8)

    class LongAnswerTokenizer(FakeTokenizer):
        def __call__(self, text, *, add_special_tokens, truncation):
            return {"input_ids": [1, 2, 3, 4] if text.endswith("|assistant:") else [1, 2, 3, 4, 5, 6, 7, 8, 9]}

    with pytest.raises(ValueError, match="Assistant answer has"):
        encode_messages(LongAnswerTokenizer(), messages, max_length=4)


def _training_row(
    *,
    consented: bool = True,
    group: str = "group-1",
    prompt: str = "Inspect the target file.",
    answer: str = "Read the file and related tests.",
) -> dict:
    messages = [
        {"role": "user", "content": prompt},
        {"role": "assistant", "content": answer},
    ]
    record_id = hashlib.sha256(
        json.dumps(
            {"specialist": "next_action", "messages": messages},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    group_id = hashlib.sha256(group.strip().casefold().encode("utf-8")).hexdigest()
    return {
        "specialist": "next_action",
        "messages": messages,
        "record_id": record_id,
        "group_id": group_id,
        "provenance": {
            "consented": consented,
            "source": "approved dataset",
            "license": "Apache-2.0",
            "group_id": group_id,
        },
        "verification": {
            "status": "passed",
            "check_names": ["isolated test suite"],
        },
    }


def _write_prepared_pair(tmp_path):
    train_row = _training_row(group="train-repository")
    eval_row = _training_row(
        group="eval-repository",
        prompt="Inspect the second target file.",
        answer="Read the second file and its tests.",
    )
    train_path = tmp_path / "next_action.train.jsonl"
    eval_path = tmp_path / "next_action.eval.jsonl"
    for path, row in ((train_path, train_row), (eval_path, eval_row)):
        path.write_text(
            json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
    manifest = {
        "schema": "unai.specialist-sft-corpus.v1",
        "specialists": {
            "next_action": {
                "train_examples": 1,
                "eval_examples": 1,
                "train_groups": 1,
                "eval_groups": 1,
                "train_file": train_path.name,
                "train_sha256": sha256_file(train_path),
                "eval_file": eval_path.name,
                "eval_sha256": sha256_file(eval_path),
            }
        },
    }
    (tmp_path / "corpus_manifest.json").write_text(
        json.dumps(manifest, sort_keys=True), encoding="utf-8"
    )
    return train_path, eval_path, [train_row], [eval_row]


def test_verify_corpus_manifest_accepts_matching_prepared_splits(tmp_path) -> None:
    train_path, eval_path, train_rows, eval_rows = _write_prepared_pair(tmp_path)

    manifest = verify_corpus_manifest(
        train_path, eval_path, "next_action", train_rows, eval_rows
    )

    assert manifest["schema"] == "unai.specialist-sft-corpus.v1"


def test_verify_corpus_manifest_rejects_changed_dataset_bytes(tmp_path) -> None:
    train_path, eval_path, train_rows, eval_rows = _write_prepared_pair(tmp_path)
    train_path.write_text(train_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="train file SHA-256"):
        verify_corpus_manifest(
            train_path, eval_path, "next_action", train_rows, eval_rows
        )


def test_verify_corpus_manifest_rejects_tampered_split_counts_and_names(tmp_path) -> None:
    train_path, eval_path, train_rows, eval_rows = _write_prepared_pair(tmp_path)
    manifest_path = tmp_path / "corpus_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["specialists"]["next_action"]["eval_groups"] = 2
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="eval group count"):
        verify_corpus_manifest(
            train_path, eval_path, "next_action", train_rows, eval_rows
        )

    manifest["specialists"]["next_action"]["eval_groups"] = 1
    manifest["specialists"]["next_action"]["train_file"] = "stale.train.jsonl"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="train filename"):
        verify_corpus_manifest(
            train_path, eval_path, "next_action", train_rows, eval_rows
        )


def test_verify_corpus_manifest_requires_adjacent_manifest_and_shared_directory(tmp_path) -> None:
    train_path, eval_path, train_rows, eval_rows = _write_prepared_pair(tmp_path)
    (tmp_path / "corpus_manifest.json").unlink()

    with pytest.raises(ValueError, match="manifest is missing"):
        verify_corpus_manifest(
            train_path, eval_path, "next_action", train_rows, eval_rows
        )

    other_dir = tmp_path / "other"
    other_dir.mkdir()
    second_eval = other_dir / eval_path.name
    second_eval.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="share the prepared corpus directory"):
        verify_corpus_manifest(
            train_path, second_eval, "next_action", train_rows, eval_rows
        )


def test_read_specialist_jsonl_requires_complete_provenance_and_verification(tmp_path) -> None:
    path = tmp_path / "data.jsonl"
    path.write_text(json.dumps(_training_row()) + "\n", encoding="utf-8")
    rows = read_specialist_jsonl(path, "next_action")
    assert len(rows) == 1

    bad = _training_row(consented=False)
    path.write_text(json.dumps(bad) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="verified consent/provenance"):
        read_specialist_jsonl(path, "next_action")


def test_read_specialist_jsonl_rejects_malformed_message_shapes_without_attribute_errors(tmp_path) -> None:
    row = _training_row()
    row["messages"][-1] = "not-a-message-object"
    path = tmp_path / "malformed.jsonl"
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="verified consent/provenance"):
        read_specialist_jsonl(path, "next_action")


def test_manifest_verification_hashes_the_exact_loaded_snapshot_not_a_later_disk_read(tmp_path) -> None:
    train_path, eval_path, train_rows, eval_rows = _write_prepared_pair(tmp_path)
    loaded_train_rows, train_digest = read_specialist_jsonl_with_hash(
        train_path, "next_action"
    )
    loaded_eval_rows, eval_digest = read_specialist_jsonl_with_hash(
        eval_path, "next_action"
    )
    assert loaded_train_rows == train_rows
    assert loaded_eval_rows == eval_rows

    # A concurrent writer changes the path after parsing. Training continues with
    # the already-loaded rows, so the manifest must be checked against those bytes.
    train_path.write_text(train_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    manifest = verify_corpus_manifest(
        train_path,
        eval_path,
        "next_action",
        loaded_train_rows,
        loaded_eval_rows,
        train_digest,
        eval_digest,
    )

    assert manifest["schema"] == "unai.specialist-sft-corpus.v1"


def test_manifest_verification_rejects_a_changed_snapshot_digest(tmp_path) -> None:
    train_path, eval_path, _train_rows, _eval_rows = _write_prepared_pair(tmp_path)
    train_path.write_text(train_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    changed_rows, changed_digest = read_specialist_jsonl_with_hash(
        train_path, "next_action"
    )
    eval_rows, eval_digest = read_specialist_jsonl_with_hash(eval_path, "next_action")

    with pytest.raises(ValueError, match="train file SHA-256"):
        verify_corpus_manifest(
            train_path,
            eval_path,
            "next_action",
            changed_rows,
            eval_rows,
            changed_digest,
            eval_digest,
        )
