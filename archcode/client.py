from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import AsyncIterator

from openai import AsyncOpenAI

from archcode.config import ProviderConfig


class LLMError(Exception):
    pass


class AuthenticationError(LLMError):
    pass


@dataclass
class StreamEnd:
    text: str = ""


@dataclass
class TextDelta:
    text: str


StreamEvent = TextDelta | StreamEnd


class LLMClient(ABC):
    @abstractmethod
    async def stream(
        self,
        messages: list[dict[str, str]],
        max_tokens: int,
    ) -> AsyncIterator[StreamEvent]:
        ...


class OpenAICompatClient(LLMClient):
    """OpenAI 兼容 Chat Completions API（适用于大多数国内中转 / vLLM / Ollama）。"""

    def __init__(self, config: ProviderConfig) -> None:
        api_key = config.resolve_api_key()
        if not api_key:
            raise AuthenticationError(
                "API key not set. Configure api_key in .archcode/config.yaml "
                "or set OPENAI_API_KEY environment variable."
            )
        self._model = config.model
        self._max_tokens = config.max_output_tokens
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=config.base_url.rstrip("/"),
        )

    async def stream(
        self,
        messages: list[dict[str, str]],
        max_tokens: int,
    ) -> AsyncIterator[StreamEvent]:
        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=messages,  # type: ignore[arg-type]
                max_tokens=max_tokens or self._max_tokens,
                stream=True,
            )
        except Exception as e:
            err = str(e).lower()
            if "auth" in err or "api key" in err or "401" in err:
                raise AuthenticationError(str(e)) from e
            raise LLMError(str(e)) from e

        full_text: list[str] = []
        async for chunk in response:
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if delta:
                full_text.append(delta)
                yield TextDelta(text=delta)

        yield StreamEnd(text="".join(full_text))


def create_client(config: ProviderConfig) -> LLMClient:
    if config.protocol in ("openai-compat", "openai"):
        return OpenAICompatClient(config)
    raise LLMError(
        f"Protocol '{config.protocol}' not implemented yet. "
        "Start with openai-compat; add anthropic client when you're ready."
    )
