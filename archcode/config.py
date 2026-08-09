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
    thinking: bool = False

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
class MCPServerConfig:
    """单个 MCP server 的配置。

    二选一:
    - stdio: command + args (+ 可选 env)
    - HTTP:  url (+ 可选 headers)
    """

    name: str
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def is_stdio(self) -> bool:
        return self.command is not None


def resolve_env_vars(value: str) -> str:
    """把 ${VAR} 替换为父进程 os.environ 的值,未设的保留字面量。"""
    return _ENV_VAR_RE.sub(lambda m: os.environ.get(m.group(1), m.group(0)), value)


def build_child_env(declared_env: dict[str, str] | None) -> dict[str, str]:
    """构造 stdio 子进程的环境变量:继承父进程,加上 declared_env(已解析)。

    父进程的 PATH 一定继承(否则 npx 等命令找不到),其他 env 由调用方决定是否继承。
    """
    env: dict[str, str] = {}
    for key in ("PATH", "SYSTEMROOT", "TEMP", "TMP", "HOME", "USERPROFILE"):
        val = os.environ.get(key, "")
        if val:
            env[key] = val
    for key, value in (declared_env or {}).items():
        env[key] = resolve_env_vars(value)
    return env


@dataclass
class AppConfig:
    providers: list[ProviderConfig]
    system_prompt: str = ""
    mcp_servers: list[MCPServerConfig] = field(default_factory=list)


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
        thinking=bool(raw.get("thinking", False)),
    )


def _parse_mcp_server(raw: dict) -> MCPServerConfig:
    """解析单个 MCP server 配置 YAML。"""
    name = raw.get("name")
    if not name:
        raise ConfigError("mcp_servers entry missing required field: name")

    env = raw.get("env") or {}
    headers = raw.get("headers") or {}

    return MCPServerConfig(
        name=name,
        command=raw.get("command"),
        args=list(raw.get("args") or []),
        env=dict(env),
        url=raw.get("url"),
        headers=dict(headers),
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
    mcp_servers = [_parse_mcp_server(s) for s in raw.get("mcp_servers", [])]

    return AppConfig(
        providers=providers,
        system_prompt=str(raw.get("system_prompt", "")),
        mcp_servers=mcp_servers,
    )


def _resolve_env(value: str) -> str:
    return _ENV_VAR_RE.sub(lambda m: os.environ.get(m.group(1), m.group(0)), value)


def load_config(path: Path | None = None) -> AppConfig:
    """加载配置,按优先级合并:~/.archcode → 项目 .archcode → local。

    合并策略:destructive(整个 mcp_servers 列表由后加载者覆盖,不按 name 合并)。
    """
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
            if layer.mcp_servers:
                merged.mcp_servers = layer.mcp_servers

    if merged is None:
        raise ConfigError(
            "No config found. Copy .archcode/config.yaml.example to "
            ".archcode/config.yaml and set your API key."
        )
    return merged