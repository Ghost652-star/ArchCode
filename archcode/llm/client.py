from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any, AsyncIterator

from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

from archcode.config import ProviderConfig
from archcode.conversation.manager import ConversationManager
from archcode.llm.events import (
    StreamEnd,
    StreamEvent,
    TextDelta,
    ThinkingComplete,
    ThinkingDelta,
    ToolCallComplete,
    ToolCallDelta,
    ToolCallStart,
)
from archcode.llm.serializer import (
    build_anthropic_messages,
    build_chat_completion_messages,
    build_openai_input,
)


class LLMError(Exception):
    pass


class AuthenticationError(LLMError):
    pass


class RateLimitError(LLMError):
    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class NetworkError(LLMError):
    pass


# 各家 provider 描述「prompt 超出窗口」错误的常见文案(全小写匹配)
_PROMPT_TOO_LONG_PATTERNS = (
    "prompt is too long",
    "context length exceeded",
    "context length maximum",
    "maximum context length",
    "reduce the length of the messages",
    "reduce the length",
    "too many tokens",
    "input is too long",
    "request too large",
)


def is_prompt_too_long_error(error: Exception) -> bool:
    """嗅探 LLMError 文本,识别 prompt 超出窗口的错误。

    客户端没有专门的子类,只能按常见错误文案 pattern-match。
    任何子类(AuthenticationError / RateLimitError / NetworkError)的「太长」类
    描述也会被命中 —— 这些不应该走 force-compact 路径,而是各自单独处理。
    因此调用方需要先判断 ``isinstance(error, LLMError)`` 且非鉴权/限流/网络错误,
    再调用此函数。
    """
    msg = str(error).lower()
    return any(pat in msg for pat in _PROMPT_TOO_LONG_PATTERNS)


class LLMClient(ABC):
    """统一 LLM 接口。上层只消费 StreamEvent，不碰各家协议细节。"""

    protocol: str = ""  # 子类各自赋值："anthropic" / "openai" / "openai-compat"

    @abstractmethod
    async def stream(
        self,
        conversation: ConversationManager,
        system: str = "",
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        ...

    def set_max_output_tokens(self, tokens: int) -> None:
        pass

    @property
    def context_window(self) -> int:
        """模型上下文窗口大小(单位:token)。

        子类按模型表返回。基类默认 200K(Sonnet 兜底值)。
        压缩模块据此计算触发阈值,不持有独立副本,避免和模型不同步。
        """
        return 200_000


class AnthropicClient(LLMClient):
    protocol = "anthropic"

    # 模型名 → context_window 的映射表。
    # 没列出的模型默认 200K(Sonnet 全家兜底)。
    _CONTEXT_WINDOWS = {
        "claude-opus-4": 200_000,
        "claude-opus-4-1": 200_000,
        "claude-sonnet-4": 200_000,
        "claude-sonnet-4-5": 200_000,
        "claude-haiku-4-5": 200_000,
        "claude-3-5-sonnet": 200_000,
        "claude-3-5-haiku": 200_000,
        "claude-3-opus": 200_000,
        "claude-3-sonnet": 200_000,
        "claude-3-haiku": 200_000,
    }

    def __init__(self, config: ProviderConfig) -> None:
        self.model = config.model
        self.thinking = config.thinking
        self.max_output_tokens = config.max_output_tokens or 4096
        api_key = config.resolve_api_key()
        if not api_key:
            raise AuthenticationError(
                "Anthropic API key not found. "
                "Set it in .archcode/config.yaml or via ANTHROPIC_API_KEY."
            )
        self._client = AsyncAnthropic(
            api_key=api_key,
            base_url=config.base_url.rstrip("/"),
        )

    def set_max_output_tokens(self, tokens: int) -> None:
        self.max_output_tokens = tokens

    @property
    def context_window(self) -> int:
        # 精确匹配失败时,按前缀匹配(如 claude-sonnet-4-20250501 → claude-sonnet-4)
        if self.model in self._CONTEXT_WINDOWS:
            return self._CONTEXT_WINDOWS[self.model]
        for prefix, size in self._CONTEXT_WINDOWS.items():
            if self.model.startswith(prefix):
                return size
        return 200_000

    async def stream(
        self,
        conversation: ConversationManager,
        system: str = "",
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        import anthropic as _anthropic

        messages = build_anthropic_messages(conversation.get_messages())
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_output_tokens,
            "messages": messages,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = tools
        if self.thinking:
            kwargs["thinking"] = {
                "type": "enabled",
                "budget_tokens": max(self.max_output_tokens - 1, 1024),
            }

        current_tool_name = ""
        current_tool_id = ""
        json_accum = ""
        in_thinking = False
        thinking_accum = ""
        thinking_signature = ""

        try:
            async with self._client.messages.stream(**kwargs) as stream:
                async for event in stream:
                    if event.type == "content_block_start":
                        block = event.content_block
                        if block.type == "thinking":
                            in_thinking = True
                            thinking_accum = ""
                            thinking_signature = ""
                        elif block.type == "tool_use":
                            current_tool_name = block.name
                            current_tool_id = block.id
                            json_accum = ""
                            yield ToolCallStart(
                                tool_name=current_tool_name,
                                tool_id=current_tool_id,
                            )
                    elif event.type == "content_block_delta":
                        delta = event.delta
                        if delta.type == "text_delta":
                            yield TextDelta(text=delta.text)
                        elif delta.type == "thinking_delta":
                            thinking_accum += delta.thinking
                            yield ThinkingDelta(text=delta.thinking)
                        elif delta.type == "signature_delta":
                            thinking_signature = delta.signature
                        elif delta.type == "input_json_delta":
                            json_accum += delta.partial_json
                            yield ToolCallDelta(text=delta.partial_json)
                    elif event.type == "content_block_stop":
                        if in_thinking:
                            yield ThinkingComplete(
                                thinking=thinking_accum,
                                signature=thinking_signature,
                            )
                            in_thinking = False
                        if current_tool_name:
                            try:
                                args = json.loads(json_accum) if json_accum else {}
                            except json.JSONDecodeError:
                                args = {}
                            yield ToolCallComplete(
                                tool_id=current_tool_id,
                                tool_name=current_tool_name,
                                arguments=args,
                            )
                            current_tool_name = ""
                            current_tool_id = ""
                            json_accum = ""

                final = await stream.get_final_message()
                usage = final.usage
                yield StreamEnd(
                    stop_reason=final.stop_reason or "end_turn",
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    cache_read=getattr(usage, "cache_read_input_tokens", 0) or 0,
                    cache_creation=getattr(usage, "cache_creation_input_tokens", 0) or 0,
                )
        except _anthropic.AuthenticationError as e:
            raise AuthenticationError(f"Invalid API key: {e}") from e
        except _anthropic.RateLimitError as e:
            retry = e.response.headers.get("retry-after") if e.response else None
            raise RateLimitError(
                f"Rate limited. {f'Retry after {retry}s.' if retry else 'Please wait.'}",
                retry_after=float(retry) if retry else None,
            ) from e
        except _anthropic.APIConnectionError as e:
            raise NetworkError(f"Network error: {e}") from e
        except _anthropic.APIStatusError as e:
            raise LLMError(f"API error ({e.status_code}): {e.message}") from e


class OpenAIClient(LLMClient):
    """OpenAI Responses API（/responses）。"""

    protocol = "openai"

    _CONTEXT_WINDOWS = {
        "gpt-4.1": 1_047_576,
        "gpt-4.1-mini": 1_047_576,
        "gpt-4.1-nano": 1_047_576,
        "gpt-4o": 128_000,
        "gpt-4o-mini": 128_000,
        "o3": 200_000,
        "o3-mini": 200_000,
        "o4-mini": 200_000,
        "gpt-5": 400_000,
        "gpt-5-mini": 400_000,
    }

    def __init__(self, config: ProviderConfig) -> None:
        self.model = config.model
        self.max_output_tokens = config.max_output_tokens or 4096
        api_key = config.resolve_api_key()
        if not api_key:
            raise AuthenticationError(
                "OpenAI API key not found. "
                "Set it in .archcode/config.yaml or via OPENAI_API_KEY."
            )
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=config.base_url.rstrip("/"),
        )

    def set_max_output_tokens(self, tokens: int) -> None:
        self.max_output_tokens = tokens

    @property
    def context_window(self) -> int:
        if self.model in self._CONTEXT_WINDOWS:
            return self._CONTEXT_WINDOWS[self.model]
        for prefix, size in self._CONTEXT_WINDOWS.items():
            if self.model.startswith(prefix):
                return size
        return 128_000  # GPT 兜底

    async def stream(
        self,
        conversation: ConversationManager,
        system: str = "",
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        import openai as _openai

        input_messages = build_openai_input(conversation.get_messages())
        kwargs: dict[str, Any] = {
            "model": self.model,
            "input": input_messages,
            "stream": True,
        }
        if system:
            kwargs["instructions"] = system
        if tools:
            kwargs["tools"] = tools

        current_tool_name = ""
        current_call_id = ""
        json_accum = ""

        try:
            response_stream = await self._client.responses.create(**kwargs)
            async for event in response_stream:
                if event.type == "response.output_text.delta":
                    yield TextDelta(text=event.delta)
                elif event.type == "response.function_call_arguments.delta":
                    if not current_tool_name:
                        current_tool_name = getattr(event, "name", "") or ""
                        current_call_id = getattr(event, "call_id", "") or ""
                        if current_tool_name:
                            yield ToolCallStart(
                                tool_name=current_tool_name,
                                tool_id=current_call_id,
                            )
                    json_accum += event.delta
                    yield ToolCallDelta(text=event.delta)
                elif event.type == "response.function_call_arguments.done":
                    if not current_tool_name:
                        current_tool_name = getattr(event, "name", "") or ""
                        current_call_id = getattr(event, "call_id", "") or ""
                    try:
                        args = json.loads(json_accum) if json_accum else {}
                    except json.JSONDecodeError:
                        args = {}
                    yield ToolCallComplete(
                        tool_id=current_call_id,
                        tool_name=current_tool_name,
                        arguments=args,
                    )
                    current_tool_name = ""
                    current_call_id = ""
                    json_accum = ""
                elif event.type == "response.output_item.added":
                    item = getattr(event, "item", None)
                    if item and getattr(item, "type", "") == "function_call":
                        current_tool_name = getattr(item, "name", "")
                        current_call_id = getattr(item, "call_id", "")
                        json_accum = ""
                        yield ToolCallStart(
                            tool_name=current_tool_name,
                            tool_id=current_call_id,
                        )
                elif event.type == "response.completed":
                    resp = getattr(event, "response", None)
                    usage = getattr(resp, "usage", None) if resp else None
                    details = getattr(usage, "input_tokens_details", None)
                    cache_read = getattr(details, "cached_tokens", 0) or 0
                    input_tokens = getattr(usage, "input_tokens", 0) or 0
                    yield StreamEnd(
                        stop_reason="end_turn",
                        input_tokens=max(input_tokens - cache_read, 0),
                        output_tokens=getattr(usage, "output_tokens", 0) or 0,
                        cache_read=cache_read,
                        cache_creation=0,
                    )
        except _openai.AuthenticationError as e:
            raise AuthenticationError(f"Invalid API key: {e}") from e
        except _openai.RateLimitError as e:
            retry = None
            if hasattr(e, "response") and e.response is not None:
                retry = e.response.headers.get("retry-after")
            raise RateLimitError(
                f"Rate limited. {f'Retry after {retry}s.' if retry else 'Please wait.'}",
                retry_after=float(retry) if retry else None,
            ) from e
        except _openai.APIConnectionError as e:
            raise NetworkError(f"Network error: {e}") from e
        except _openai.APIStatusError as e:
            raise LLMError(f"API error ({e.status_code}): {e.message}") from e


class OpenAICompatClient(LLMClient):
    """Chat Completions（/chat/completions），兼容中转 / vLLM / Ollama 等。"""

    protocol = "openai-compat"

    _CONTEXT_WINDOWS = {
        # MiniMax / 国产
        "MiniMax-M3": 200_000,
        "MiniMax-M2": 200_000,
        "MiniMax-M1": 200_000,
        "MiniMax": 200_000,  # 通用兜底:任何 "MiniMax-..." 都按 200K
        # Qwen
        "qwen3-max": 256_000,
        "qwen-plus": 128_000,
        "qwen-turbo": 128_000,
        # DeepSeek
        "deepseek-v3": 64_000,
        "deepseek-r1": 64_000,
        # Llama 本地
        "llama-3.3-70b": 128_000,
    }

    def __init__(self, config: ProviderConfig) -> None:
        self.model = config.model
        self.max_output_tokens = config.max_output_tokens or 4096
        api_key = config.resolve_api_key()
        if not api_key:
            raise AuthenticationError(
                "API key not found. "
                "Set it in .archcode/config.yaml or via OPENAI_API_KEY."
            )
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=config.base_url.rstrip("/"),
        )

    def set_max_output_tokens(self, tokens: int) -> None:
        self.max_output_tokens = tokens

    @property
    def context_window(self) -> int:
        if self.model in self._CONTEXT_WINDOWS:
            return self._CONTEXT_WINDOWS[self.model]
        for prefix, size in self._CONTEXT_WINDOWS.items():
            if self.model.startswith(prefix):
                return size
        return 32_000  # 中转模型兜底(常见 vLLM 默认)

    @staticmethod
    def _convert_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        converted: list[dict[str, Any]] = []
        for t in tools:
            converted.append(
                {
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t.get("description", ""),
                        "parameters": t.get("parameters", t.get("input_schema", {})),
                    },
                }
            )
        return converted

    async def stream(
        self,
        conversation: ConversationManager,
        system: str = "",
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        import openai as _openai

        messages = build_chat_completion_messages(conversation.get_messages())
        if system:
            messages = [{"role": "system", "content": system}] + messages

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": self.max_output_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            kwargs["tools"] = self._convert_tools(tools)

        active_calls: dict[int, dict[str, str]] = {}
        saw_usage_end = False

        try:
            response = await self._client.chat.completions.create(**kwargs)
            async for chunk in response:
                if not chunk.choices:
                    if chunk.usage:
                        details = getattr(chunk.usage, "prompt_tokens_details", None)
                        cache_read = getattr(details, "cached_tokens", 0) or 0
                        prompt_tokens = chunk.usage.prompt_tokens or 0
                        yield StreamEnd(
                            stop_reason="end_turn",
                            input_tokens=max(prompt_tokens - cache_read, 0),
                            output_tokens=chunk.usage.completion_tokens or 0,
                            cache_read=cache_read,
                            cache_creation=0,
                        )
                        saw_usage_end = True
                    continue

                choice = chunk.choices[0]
                delta = choice.delta

                if delta and delta.content:
                    yield TextDelta(text=delta.content)

                if delta and delta.tool_calls:
                    for tc in delta.tool_calls:
                        idx = tc.index
                        if idx not in active_calls:
                            active_calls[idx] = {"id": "", "name": "", "args": ""}
                        call = active_calls[idx]
                        if tc.id:
                            call["id"] = tc.id
                        if tc.function and tc.function.name:
                            call["name"] = tc.function.name
                            yield ToolCallStart(
                                tool_name=call["name"],
                                tool_id=call["id"],
                            )
                        if tc.function and tc.function.arguments:
                            call["args"] += tc.function.arguments
                            yield ToolCallDelta(text=tc.function.arguments)

                if choice.finish_reason in ("tool_calls", "stop"):
                    if choice.finish_reason == "tool_calls":
                        for _idx, call in sorted(active_calls.items()):
                            try:
                                args = json.loads(call["args"]) if call["args"] else {}
                            except json.JSONDecodeError:
                                args = {}
                            yield ToolCallComplete(
                                tool_id=call["id"],
                                tool_name=call["name"],
                                arguments=args,
                            )
                        active_calls.clear()

            if not saw_usage_end:
                yield StreamEnd(stop_reason="end_turn")
        except _openai.AuthenticationError as e:
            raise AuthenticationError(f"Invalid API key: {e}") from e
        except _openai.RateLimitError as e:
            retry = None
            if hasattr(e, "response") and e.response is not None:
                retry = e.response.headers.get("retry-after")
            raise RateLimitError(
                f"Rate limited. {f'Retry after {retry}s.' if retry else 'Please wait.'}",
                retry_after=float(retry) if retry else None,
            ) from e
        except _openai.APIConnectionError as e:
            raise NetworkError(f"Network error: {e}") from e
        except _openai.APIStatusError as e:
            raise LLMError(f"API error ({e.status_code}): {e.message}") from e


def create_client(config: ProviderConfig) -> LLMClient:
    if config.protocol == "anthropic":
        return AnthropicClient(config)
    if config.protocol == "openai":
        return OpenAIClient(config)
    if config.protocol in ("openai-compat",):
        return OpenAICompatClient(config)
    raise LLMError(f"Unknown protocol: {config.protocol}")
