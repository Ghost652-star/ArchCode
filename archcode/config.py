from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

_ENV_VAR_RE = re.compile(r"\$\{([^}]+)\}")


class ConfigError(Exception):
    pass


@dataclass
class ProviderConfig:
    name: str
    protocol: str  # "openai-compat" | "openai" | "anthropic"
    base_url: str
    model: str
    api_key: str = ""
    max_output_tokens: int = 4096

    def resolve_api_key(self) -> str:
        if self.api_key:
            return self.api_key
        env_map = {
            "openai": "OPENAI_API_KEY",
            "openai-compat": "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
        }
        return os.environ.get(env_map.get(self.protocol, ""), "")


@dataclass
class AppConfig:
    providers: list[ProviderConfig]
    system_prompt: str = ""


def _resolve_env(value: str) -> str:
    return _ENV_VAR_RE.sub(lambda m: os.environ.get(m.group(1), m.group(0)), value)


def _parse_provider(raw: dict) -> ProviderConfig:
    required = ("name", "protocol", "base_url", "model")
    for key in required:
        if key not in raw:
            raise ConfigError(f"Provider missing required field: {key}")

    protocol = raw["protocol"]
    if protocol not in ("openai-compat", "openai", "anthropic"):
        raise ConfigError(f"Unsupported protocol: {protocol}")

    return ProviderConfig(
        name=raw["name"],
        protocol=protocol,
        base_url=_resolve_env(str(raw["base_url"])),
        model=raw["model"],
        api_key=_resolve_env(str(raw.get("api_key", ""))),
        max_output_tokens=int(raw.get("max_output_tokens", 4096)),
    )


def _load_file(path: Path) -> AppConfig:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ConfigError(f"Failed to parse {path}: {e}") from e

    if not isinstance(raw, dict):
        raise ConfigError(f"Config root must be a mapping: {path}")

    providers_raw = raw.get("providers")
    if not providers_raw:
        raise ConfigError("Config must have at least one provider")

    providers = [_parse_provider(p) for p in providers_raw]
    return AppConfig(
        providers=providers,
        system_prompt=str(raw.get("system_prompt", "")),
    )


def load_config(path: Path | None = None) -> AppConfig:
    """加载配置，按优先级合并：~/.archcode → 项目 .archcode → local。"""
    if path is not None:
        if not path.exists():
            raise ConfigError(f"Config not found: {path}")
        return _load_file(path)

    candidates = [
        Path.home() / ".archcode" / "config.yaml",
        Path.cwd() / ".archcode" / "config.yaml",
        Path.cwd() / ".archcode" / "config.local.yaml",
    ]

    merged: AppConfig | None = None
    for candidate in candidates:
        if not candidate.exists():
            continue
        layer = _load_file(candidate)
        if merged is None:
            merged = layer
        else:
            if layer.providers:
                merged.providers = layer.providers
            if layer.system_prompt:
                merged.system_prompt = layer.system_prompt

    if merged is None:
        raise ConfigError(
            "No config found. Copy .archcode/config.yaml.example to "
            ".archcode/config.yaml and set your API key."
        )
    return merged
