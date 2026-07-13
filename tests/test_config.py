from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from archcode.config import ConfigError, load_config


def test_build_system_prompt_from_prompt_package() -> None:
    from archcode.prompts.builder import build_system_prompt

    prompt = build_system_prompt(work_dir="/workspace", extra="Use Chinese.")
    assert "Working directory: /workspace" in prompt
    assert prompt.endswith("Use Chinese.")


def test_load_config_missing(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="Config not found"):
        load_config(tmp_path / "nope.yaml")


def test_load_config_valid(tmp_path: Path) -> None:
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        textwrap.dedent("""
        providers:
          - name: test
            protocol: openai-compat
            base_url: http://localhost:8000/v1
            model: test-model
            api_key: sk-test
        """),
        encoding="utf-8",
    )
    config = load_config(config_file)
    assert len(config.providers) == 1
    assert config.providers[0].model == "test-model"
