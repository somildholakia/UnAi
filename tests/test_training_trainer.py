from __future__ import annotations

import json

import pytest

from training.train_lora import encode_messages, read_specialist_jsonl


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


def _training_row(*, consented: bool = True) -> dict:
    return {
        "specialist": "next_action",
        "messages": [
            {"role": "user", "content": "Inspect the target file."},
            {"role": "assistant", "content": "Read the file and related tests."},
        ],
        "record_id": "record-1",
        "group_id": "group-1",
        "provenance": {
            "consented": consented,
            "source": "approved dataset",
            "license": "Apache-2.0",
        },
        "verification": {
            "status": "passed",
            "check_names": ["isolated test suite"],
        },
    }


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
