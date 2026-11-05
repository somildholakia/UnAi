from __future__ import annotations

import json
from pathlib import Path

import pytest

from training.train_specialists import DEFAULT_CONFIG, SPECIALISTS, build_command, load_config


def test_default_model_is_pinned_and_license_is_declared() -> None:
    config = load_config()
    model = config["default_model"]
    assert model["model_id"] == "Qwen/Qwen3-1.7B"
    assert len(model["revision"]) == 40
    assert model["license"] == "Apache-2.0"
    assert model["revision_url"].endswith(model["revision"])


def test_load_config_rejects_mutable_model_revision(tmp_path: Path) -> None:
    config = json.loads(DEFAULT_CONFIG.read_text(encoding="utf-8"))
    config["default_model"]["revision"] = "main"
    path = tmp_path / "model.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(ValueError, match="full immutable"):
        load_config(path)


def test_build_command_creates_separate_specialist_job_and_resume_flags(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    for specialist in SPECIALISTS:
        (data / f"{specialist}.train.jsonl").write_text("{}\n", encoding="utf-8")
        (data / f"{specialist}.eval.jsonl").write_text("{}\n", encoding="utf-8")
    (data / "corpus_manifest.json").write_text("{}", encoding="utf-8")
    config = load_config()["training"]

    command = build_command(
        "planner", model="Qwen/Qwen3-1.7B", revision="a" * 40,
        data_dir=data, output_root=tmp_path / "artifacts",
        training=config, qlora=True, resume=True,
    )

    assert "--specialist" in command and command[command.index("--specialist") + 1] == "planner"
    assert "--qlora" in command
    assert command[-2:] == ["--resume-from-checkpoint", "latest"]
    assert str(tmp_path / "artifacts" / "unai-planner") in command


def test_build_command_rejects_unknown_role_and_missing_prepared_data(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Unknown specialist"):
        build_command(
            "general", model="model", revision="a" * 40, data_dir=tmp_path,
            output_root=tmp_path, training={}, qlora=False,
        )
    with pytest.raises(FileNotFoundError, match="Prepared corpus is incomplete"):
        build_command(
            "verifier", model="model", revision="a" * 40, data_dir=tmp_path,
            output_root=tmp_path, training={}, qlora=False,
        )
