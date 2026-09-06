"""Direct Anthropic Messages API adapter for bounded production calls."""
from __future__ import annotations

import json
import os
from time import perf_counter
from typing import Any, Mapping, Sequence

from investigator.llm.base import (
    MessageInput, ModelCallMetadata, ModelCallResult, ModelClient, ModelNativeCall,
    ModelParseError, ModelTextBlock, ModelToolUse, parse_model_output,
)

ANTHROPIC_CONNECT_TIMEOUT_SECONDS = 10
ANTHROPIC_READ_TIMEOUT_SECONDS = 300


class AnthropicConfigurationError(ValueError):
    """Required direct-Anthropic configuration is absent or invalid."""


def _positive_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise AnthropicConfigurationError(f"{name} must be a positive integer") from exc
    if parsed <= 0:
        raise AnthropicConfigurationError(f"{name} must be a positive integer")
    return parsed


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _text_from_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    pieces: list[str] = []
    for block in content or []:
        if _field(block, "type") == "text":
            pieces.append(str(_field(block, "text", "")))
    return "".join(pieces)


def _safe_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _safe_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_value(item) for item in value]
    if hasattr(value, "model_dump"):
        return _safe_value(value.model_dump(mode="json"))
    if hasattr(value, "dict"):
        return _safe_value(value.dict())
    return str(value)


class AnthropicModelClient(ModelClient):
    """One structured or native tool-use call through Anthropic's Messages API."""

    def __init__(self, model_id: str | None = None, *, client: Any | None = None, api_key: str | None = None) -> None:
        self.model_id = model_id or os.getenv("ANTHROPIC_MODEL_ID")
        if not self.model_id:
            raise AnthropicConfigurationError("Anthropic model configuration is missing; set ANTHROPIC_MODEL_ID")
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        if client is None and not self.api_key:
            raise AnthropicConfigurationError("Anthropic API configuration is missing; set ANTHROPIC_API_KEY")
        self.client = client
        self.investigator_max_tokens = _positive_env("ANTHROPIC_INVESTIGATOR_MAX_TOKENS", 24000)
        self.help_max_tokens = _positive_env("ANTHROPIC_HELP_MAX_TOKENS", 4096)

    def _client_for_call(self) -> Any:
        if self.client is not None:
            return self.client
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - dependency is declared, guard is useful for source checkouts
            raise AnthropicConfigurationError("The anthropic package is required for direct Anthropic calls") from exc
        self.client = anthropic.Anthropic(
            api_key=self.api_key,
            timeout=ANTHROPIC_READ_TIMEOUT_SECONDS,
            max_retries=0,
        )
        return self.client

    @staticmethod
    def _messages(input_data: MessageInput) -> tuple[str | None, list[dict[str, Any]]]:
        if isinstance(input_data, str):
            return None, [{"role": "user", "content": input_data}]
        system: list[str] = []
        messages: list[dict[str, Any]] = []
        for item in input_data:
            role = str(item.get("role", "user"))
            content = item.get("content", item.get("text", ""))
            if role == "system":
                system.append(_text_from_content(content))
            elif role in {"user", "assistant"}:
                messages.append({"role": role, "content": content if isinstance(content, (str, list)) else str(content)})
        return ("\n\n".join(system) or None), messages

    @staticmethod
    def _native_messages(input_data: MessageInput) -> tuple[str | None, list[dict[str, Any]]]:
        if isinstance(input_data, str):
            return None, [{"role": "user", "content": input_data}]
        system: list[str] = []
        messages: list[dict[str, Any]] = []
        for item in input_data:
            role = str(item.get("role", "user"))
            if role == "system":
                system.append(_text_from_content(item.get("content", item.get("text", ""))))
                continue
            if role == "tool":
                result = item.get("result", "")
                messages.append({"role": "user", "content": [{
                    "type": "tool_result", "tool_use_id": item.get("call_id", ""),
                    "content": result if isinstance(result, str) else json.dumps(result, sort_keys=True),
                }]})
                continue
            content: list[dict[str, Any]] = []
            text = item.get("text")
            if text:
                content.append({"type": "text", "text": str(text)})
            for use in item.get("tool_uses", []) or []:
                content.append({"type": "tool_use", "id": use.get("call_id", ""), "name": use.get("name", ""), "input": use.get("arguments", {})})
            if not content:
                raw = item.get("content", "")
                content = raw if isinstance(raw, list) else [{"type": "text", "text": str(raw)}]
            messages.append({"role": role if role in {"user", "assistant"} else "user", "content": content})
        return ("\n\n".join(system) or None), messages

    def _metadata(self, response: Any, started: float) -> ModelCallMetadata:
        usage = _field(response, "usage", {}) or {}
        return ModelCallMetadata(
            provider="anthropic", model=str(_field(response, "model", self.model_id)),
            input_tokens=_field(usage, "input_tokens"), output_tokens=_field(usage, "output_tokens"),
            latency_seconds=perf_counter() - started, parse_success=True,
            finish_reason=_field(response, "stop_reason"),
        )

    def call(self, input_data: MessageInput, output_schema: type[Any]) -> ModelCallResult:
        system, messages = self._messages(input_data)
        started = perf_counter()
        kwargs: dict[str, Any] = {"model": self.model_id, "max_tokens": self.investigator_max_tokens, "temperature": 0, "messages": messages}
        if system:
            kwargs["system"] = system
        response = self._client_for_call().messages.create(**kwargs)
        raw_text = _text_from_content(_field(response, "content", []))
        try:
            parsed = parse_model_output(raw_text, output_schema)
        except ModelParseError as exc:
            raise ModelParseError("Anthropic returned invalid structured output", raw_output=raw_text) from exc
        return ModelCallResult(parsed=parsed, metadata=self._metadata(response, started), raw_output=_safe_value(response))

    def call_native(self, input_data: MessageInput, tools: Sequence[Mapping[str, Any]]) -> ModelNativeCall:
        system, messages = self._native_messages(input_data)
        started = perf_counter()
        translated = [{"name": tool["name"], "description": tool.get("description", ""), "input_schema": tool["inputSchema"]} for tool in tools]
        kwargs: dict[str, Any] = {"model": self.model_id, "max_tokens": self.help_max_tokens, "temperature": 0, "messages": messages, "tools": translated}
        if system:
            kwargs["system"] = system
        response = self._client_for_call().messages.create(**kwargs)
        text_blocks: list[ModelTextBlock] = []
        tool_uses: list[ModelToolUse] = []
        for block in _field(response, "content", []) or []:
            kind = _field(block, "type")
            if kind == "text":
                text_blocks.append(ModelTextBlock(text=str(_field(block, "text", ""))))
            elif kind == "tool_use":
                tool_uses.append(ModelToolUse(call_id=str(_field(block, "id", "")), name=str(_field(block, "name", "")), arguments=dict(_field(block, "input", {}) or {})))
        return ModelNativeCall(text_blocks=text_blocks, tool_uses=tool_uses, metadata=self._metadata(response, started), raw_output=_safe_value(response))
