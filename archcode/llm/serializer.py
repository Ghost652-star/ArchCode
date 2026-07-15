from __future__ import annotations

import json
from typing import Any

from archcode.conversation.models import Message
from archcode.tools.base import Tool


def build_anthropic_messages(messages: list[Message]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for message in messages:
        if message.tool_uses or message.thinking_blocks:
            content: list[dict[str, Any]] = []
            for thinking_block in message.thinking_blocks:
                block: dict[str, Any] = {
                    "type": "thinking",
                    "thinking": thinking_block.thinking,
                }
                if thinking_block.signature:
                    block["signature"] = thinking_block.signature
                content.append(block)
            if message.content:
                content.append({"type": "text", "text": message.content})
            for tool_use in message.tool_uses:
                content.append(
                    {
                        "type": "tool_use",
                        "id": tool_use.tool_use_id,
                        "name": tool_use.tool_name,
                        "input": tool_use.arguments,
                    }
                )
            if not content:
                content.append({"type": "text", "text": ""})
            result.append({"role": "assistant", "content": content})
        elif message.tool_results:
            result.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_result.tool_use_id,
                            "content": tool_result.content,
                            "is_error": tool_result.is_error,
                        }
                        for tool_result in message.tool_results
                    ],
                }
            )
        elif (
            message.role == "user"
            and result
            and result[-1]["role"] == "user"
            and isinstance(result[-1]["content"], str)
        ):
            result[-1]["content"] = result[-1]["content"] + "\n" + message.content
        else:
            result.append({"role": message.role, "content": message.content})
    return result


def build_openai_input(messages: list[Message]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for message in messages:
        if message.tool_uses:
            if message.content:
                result.append({"role": "assistant", "content": message.content})
            for tool_use in message.tool_uses:
                result.append(
                    {
                        "type": "function_call",
                        "name": tool_use.tool_name,
                        "call_id": tool_use.tool_use_id,
                        "arguments": json.dumps(tool_use.arguments),
                    }
                )
        elif message.tool_results:
            for tool_result in message.tool_results:
                result.append(
                    {
                        "type": "function_call_output",
                        "call_id": tool_result.tool_use_id,
                        "output": tool_result.content,
                    }
                )
        else:
            result.append({"role": message.role, "content": message.content})
    return result


def build_chat_completion_messages(messages: list[Message]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for message in messages:
        if message.tool_uses:
            result.append(
                {
                    "role": "assistant",
                    "content": message.content or None,
                    "tool_calls": [
                        {
                            "id": tool_use.tool_use_id,
                            "type": "function",
                            "function": {
                                "name": tool_use.tool_name,
                                "arguments": json.dumps(tool_use.arguments),
                            },
                        }
                        for tool_use in message.tool_uses
                    ],
                }
            )
        elif message.tool_results:
            for tool_result in message.tool_results:
                result.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_result.tool_use_id,
                        "content": tool_result.content,
                    }
                )
        else:
            result.append({"role": message.role, "content": message.content})
    return result


def build_anthropic_tools(tools: list[Tool]) -> list[dict[str, Any]]:
    """Tool 列表 → Anthropic API 的 tools 参数格式。

    输出:[{name, description, input_schema}]
    input_schema 来自 Pydantic model_json_schema(),去掉 title。
    """
    return [tool.get_schema() for tool in tools]


def build_openai_tools(tools: list[Tool]) -> list[dict[str, Any]]:
    """Tool 列表 → OpenAI Responses API / Chat Completions 通用格式。

    输出:[{type: function, name, description, parameters}]
    OpenAICompatClient 内部的 _convert_tools 会再包一层 function 嵌套,
    转成 Chat Completions 格式。
    """
    schemas: list[dict[str, Any]] = []
    for tool in tools:
        parameters = tool.params_model.model_json_schema()
        parameters.pop("title", None)
        schemas.append(
            {
                "type": "function",
                "name": tool.name,
                "description": tool.description,
                "parameters": parameters,
            }
        )
    return schemas
