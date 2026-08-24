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
class SummaryProviderConfig:
    """摘要专用 provider 配置。

    启用时,压缩模块会用这个 client 跑 SUMMARY_PROMPT(独立于主对话 client)。
    默认走 MiniMax(OpenAI 兼容协议),通过环境变量 ``MINIMAX_API_KEY`` 拿 key。
    base_url / model 用户可在 YAML 覆盖。
    """

    enabled: bool = False  # 默认关闭,需要时在 YAML 里启用
    protocol: str = "openai-compat"
    base_url: str = "https://api.MiniMax.io/v1"
    model: str = "MiniMax-M3"
    api_key: str = ""  # 缺省时按 api_key_env 查环境变量
    api_key_env: str = "MINIMAX_API_KEY"
    max_output_tokens: int = 4096

    def resolve_api_key(self) -> str:
        if self.api_key:
            return self.api_key
        return os.environ.get(self.api_key_env, "")


@dataclass
class CompressionConfig:
    """上下文压缩总配置。

    所有阈值跟 MewCode 一致,详见 ``archcode/context/compactor.py``。
    字段含义:
    - single_char_limit / aggregate_char_limit:Layer 1 单条 + 单消息聚合阈值
    - summary_output_reserve / auto_safety_margin / manual_safety_margin:Layer 2 阈值
    - preview_chars / old_result_snip_chars:替换后的预览长度
    - keep_recent_turns / keep_recent_tokens / keep_max_tokens / min_keep_turns:压缩时保留窗口
    - min_summarize_prefix_tokens:摘要调用本身的最小前缀(防止微调被空总结)
    - recovery_*:RecoveryState 渲染附件的预算
    - max_summary_failures / max_force_compact_failures:两类熔断器的阈值
    - summary_provider:可选独立摘要 client
    """

    enabled: bool = True
    single_char_limit: int = 50_000
    aggregate_char_limit: int = 200_000
    summary_output_reserve: int = 20_000
    auto_safety_margin: int = 13_000
    manual_safety_margin: int = 3_000
    preview_chars: int = 2_000
    old_result_snip_chars: int = 200
    keep_recent_turns: int = 10
    keep_recent_tokens: int = 10_000
    keep_max_tokens: int = 40_000
    min_keep_turns: int = 1
    min_summarize_prefix_tokens: int = 2_000
    recovery_file_limit: int = 5
    recovery_tokens_per_file: int = 5_000
    recovery_skills_budget: int = 25_000
    recovery_tokens_per_skill: int = 5_000
    max_summary_retries: int = 3
    max_summary_failures: int = 3  # auto_compact 熔断阈值
    max_force_compact_failures: int = 2  # force_compact 熔断阈值
    summary_provider: SummaryProviderConfig = field(
        default_factory=SummaryProviderConfig
    )


@dataclass
class AppConfig:
    providers: list[ProviderConfig]
    system_prompt: str = ""
    mcp_servers: list[MCPServerConfig] = field(default_factory=list)
    compression: CompressionConfig = field(default_factory=CompressionConfig)


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
    compression = _parse_compression(raw.get("compression", {}))

    return AppConfig(
        providers=providers,
        system_prompt=str(raw.get("system_prompt", "")),
        mcp_servers=mcp_servers,
        compression=compression,
    )


def _parse_summary_provider(raw: dict | None) -> SummaryProviderConfig:
    """解析 summary_provider YAML 块。空块返回默认值(默认禁用)。"""
    if not raw:
        return SummaryProviderConfig()
    return SummaryProviderConfig(
        enabled=bool(raw.get("enabled", False)),
        protocol=str(raw.get("protocol", "openai-compat")),
        base_url=str(raw.get("base_url", "https://api.MiniMax.io/v1")),
        model=str(raw.get("model", "MiniMax-M3")),
        api_key=str(raw.get("api_key", "")),
        api_key_env=str(raw.get("api_key_env", "MINIMAX_API_KEY")),
        max_output_tokens=int(raw.get("max_output_tokens", 4096)),
    )


def _parse_compression(raw: dict | None) -> CompressionConfig:
    """解析 compression YAML 块。空块返回默认配置(启用 + 所有阈值默认)。"""
    if not raw:
        return CompressionConfig()
    cfg = CompressionConfig(
        enabled=bool(raw.get("enabled", True)),
        single_char_limit=int(raw.get("single_char_limit", 50_000)),
        aggregate_char_limit=int(raw.get("aggregate_char_limit", 200_000)),
        summary_output_reserve=int(raw.get("summary_output_reserve", 20_000)),
        auto_safety_margin=int(raw.get("auto_safety_margin", 13_000)),
        manual_safety_margin=int(raw.get("manual_safety_margin", 3_000)),
        preview_chars=int(raw.get("preview_chars", 2_000)),
        old_result_snip_chars=int(raw.get("old_result_snip_chars", 200)),
        keep_recent_turns=int(raw.get("keep_recent_turns", 10)),
        keep_recent_tokens=int(raw.get("keep_recent_tokens", 10_000)),
        keep_max_tokens=int(raw.get("keep_max_tokens", 40_000)),
        min_keep_turns=int(raw.get("min_keep_turns", 1)),
        min_summarize_prefix_tokens=int(
            raw.get("min_summarize_prefix_tokens", 2_000)
        ),
        recovery_file_limit=int(raw.get("recovery_file_limit", 5)),
        recovery_tokens_per_file=int(raw.get("recovery_tokens_per_file", 5_000)),
        recovery_skills_budget=int(raw.get("recovery_skills_budget", 25_000)),
        recovery_tokens_per_skill=int(raw.get("recovery_tokens_per_skill", 5_000)),
        max_summary_retries=int(raw.get("max_summary_retries", 3)),
        max_summary_failures=int(raw.get("max_summary_failures", 3)),
        max_force_compact_failures=int(raw.get("max_force_compact_failures", 2)),
        summary_provider=_parse_summary_provider(raw.get("summary_provider")),
    )
    return cfg


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
            if layer.compression:
                merged.compression = layer.compression

    if merged is None:
        raise ConfigError(
            "No config found. Copy .archcode/config.yaml.example to "
            ".archcode/config.yaml and set your API key."
        )
    return merged
