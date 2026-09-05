import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import RLock
from time import perf_counter
from typing import Any

from botocore.config import Config
from botocore.exceptions import ReadTimeoutError
from pydantic import BaseModel

from investigator.llm.base import (
    MessageInput,
    ModelCallMetadata,
    ModelCallResult,
    ModelNativeCall,
    ModelTextBlock,
    ModelToolUse,
    ModelParseError,
    normalize_json_text,
    parse_model_output,
)


class BedrockConfigurationError(ValueError):
    """Raised when the Bedrock adapter lacks required environment configuration."""


BEDROCK_CONNECT_TIMEOUT_SECONDS = 10
BEDROCK_READ_TIMEOUT_SECONDS = 300


def bedrock_transport_config() -> Config:
    """Use one bounded transport policy for every Bedrock credential path.

    ``max_attempts=1`` means Botocore does not issue a second inference
    request after an ambiguous response timeout.  A read timeout is therefore
    terminal for this application run and must be handled by the operator.
    """
    return Config(
        connect_timeout=BEDROCK_CONNECT_TIMEOUT_SECONDS,
        read_timeout=BEDROCK_READ_TIMEOUT_SECONDS,
        retries={"mode": "standard", "total_max_attempts": 1},
    )


def is_provider_timeout(exc: BaseException) -> bool:
    return isinstance(exc, ReadTimeoutError)


def failure_category(exc: BaseException) -> str:
    return "PROVIDER_TIMEOUT" if is_provider_timeout(exc) else type(exc).__name__


def safe_failure_message(exc: BaseException) -> str:
    if is_provider_timeout(exc):
        return "Assessment could not be completed because the model provider did not return a response in time. No assessment result was produced."
    return redact_sensitive_text(str(exc))


@dataclass(frozen=True)
class CredentialOverride:
    aws_access_key_id: str
    aws_secret_access_key: str
    aws_session_token: str
    region_name: str | None = None


_override_lock = RLock()
_credential_override: CredentialOverride | None = None
_credential_generation = 0
_credential_updated_at: datetime | None = None


def debug_credentials_enabled() -> bool:
    return os.getenv("SIMPLIFYNEXT_DEBUG_CREDENTIALS") == "1"


def set_credential_override(override: CredentialOverride) -> None:
    global _credential_override, _credential_generation, _credential_updated_at
    with _override_lock:
        _credential_override = override
        _credential_generation += 1
        _credential_updated_at = datetime.now(timezone.utc)


def clear_credential_override() -> None:
    global _credential_override, _credential_generation, _credential_updated_at
    with _override_lock:
        _credential_override = None
        _credential_generation += 1
        _credential_updated_at = datetime.now(timezone.utc)


def credential_status() -> dict[str, object]:
    with _override_lock:
        return {
            "override_active": _credential_override is not None,
            "credential_source": "ui_override" if _credential_override is not None else "default_aws_credential_chain",
            "last_updated_at": _credential_updated_at.isoformat() if _credential_updated_at else None,
            "region": (_credential_override.region_name if _credential_override else os.getenv("AWS_REGION", "us-east-1")),
        }


def _credential_snapshot() -> tuple[CredentialOverride | None, int]:
    with _override_lock:
        return _credential_override, _credential_generation


def redact_sensitive_text(value: str) -> str:
    """Remove credential values and common credential-shaped fragments from safe diagnostics."""
    override, _ = _credential_snapshot()
    result = value
    secrets = [
        os.getenv("AWS_ACCESS_KEY_ID"),
        os.getenv("AWS_SECRET_ACCESS_KEY"),
        os.getenv("AWS_SESSION_TOKEN"),
    ]
    if override:
        secrets.extend((override.aws_access_key_id, override.aws_secret_access_key, override.aws_session_token))
    for secret in secrets:
        if secret:
            result = result.replace(secret, "[REDACTED]")
    return re.sub(r"(?i)(access[_ -]?key|secret|session[_ -]?token|security[_ -]?token|authorization|credential)(?:\s*[:=]\s*)[^\s,;]+", r"\1=[REDACTED]", result)


class BedrockModelClient:
    """Small adapter for one JSON structured call through Bedrock Converse."""

    def __init__(self, model_id: str | None = None, region: str | None = None, client: Any = None) -> None:
        self.model_id = model_id or os.getenv("BEDROCK_MODEL_ID")
        self.region = region or os.getenv("AWS_REGION", "us-east-1")
        if not self.model_id:
            raise BedrockConfigurationError(
                "Bedrock model configuration is missing; set BEDROCK_MODEL_ID"
            )
        self.client = client
        self._injected_client = client is not None
        self._client_generation: int | None = None

    def call(self, input_data: MessageInput, output_schema: type[BaseModel]) -> ModelCallResult:
        messages = self._messages(input_data)
        started = perf_counter()
        response = self._client_for_call().converse(
            modelId=self.model_id,
            messages=messages,
            inferenceConfig={"temperature": 0},
        )
        raw_text = response["output"]["message"]["content"][0]["text"]
        metadata_response = response.get("usage", {})
        stop_reason = response.get("stopReason")
        try:
            parsed = parse_model_output(json.loads(normalize_json_text(raw_text)), output_schema)
        except (json.JSONDecodeError, ModelParseError) as exc:
            raise ModelParseError(
                f"Bedrock returned invalid structured output for {output_schema.__name__}: {exc}",
                raw_output=raw_text,
            ) from exc
        metadata = ModelCallMetadata(
            provider="bedrock",
            model=self.model_id,
            input_tokens=metadata_response.get("inputTokens"),
            output_tokens=metadata_response.get("outputTokens"),
            latency_seconds=perf_counter() - started,
            parse_success=True,
            finish_reason=stop_reason,
        )
        return ModelCallResult(parsed=parsed, metadata=metadata, raw_output=raw_text)

    def call_native(self, input_data: MessageInput, tools: list[dict[str, Any]]) -> ModelNativeCall:
        """Use Bedrock Converse native text/toolUse blocks; no custom response schema."""
        started = perf_counter()
        request: dict[str, Any] = {"modelId": self.model_id, "messages": self._native_messages(input_data), "inferenceConfig": {"temperature": 0}}
        if tools:
            request["toolConfig"] = {"tools": [{"toolSpec": self._tool_spec(tool)} for tool in tools]}
        response = self._client_for_call().converse(**request)
        blocks = response.get("output", {}).get("message", {}).get("content", [])
        text_blocks: list[ModelTextBlock] = []
        tool_uses: list[ModelToolUse] = []
        for block in blocks:
            if isinstance(block.get("text"), str):
                text_blocks.append(ModelTextBlock(text=block["text"]))
            if isinstance(block.get("toolUse"), dict):
                use = block["toolUse"]
                tool_uses.append(ModelToolUse(call_id=str(use.get("toolUseId", "")), name=str(use.get("name", "")), arguments=use.get("input", {})))
        usage = response.get("usage", {})
        metadata = ModelCallMetadata(provider="bedrock", model=self.model_id, input_tokens=usage.get("inputTokens"), output_tokens=usage.get("outputTokens"), latency_seconds=perf_counter() - started, parse_success=True, finish_reason=response.get("stopReason"))
        return ModelNativeCall(text_blocks=text_blocks, tool_uses=tool_uses, metadata=metadata, raw_output=response)

    def _client_for_call(self) -> Any:
        if self._injected_client:
            return self.client
        override, generation = _credential_snapshot()
        if self._client_generation != generation:
            try:
                import boto3
            except ImportError as exc:
                raise BedrockConfigurationError("boto3 is required for BedrockModelClient") from exc
            if override is None:
                self.client = boto3.client("bedrock-runtime", region_name=self.region, config=bedrock_transport_config())
            else:
                session = boto3.Session(
                    aws_access_key_id=override.aws_access_key_id,
                    aws_secret_access_key=override.aws_secret_access_key,
                    aws_session_token=override.aws_session_token,
                    region_name=override.region_name or self.region,
                )
                self.client = session.client(
                    "bedrock-runtime",
                    config=bedrock_transport_config(),
                )
            self._client_generation = generation
        return self.client

    @staticmethod
    def _messages(input_data: MessageInput) -> list[dict[str, Any]]:
        if isinstance(input_data, str):
            return [{"role": "user", "content": [{"text": input_data}]}]
        return [dict(message) for message in input_data]

    @staticmethod
    def _native_messages(input_data: MessageInput) -> list[dict[str, Any]]:
        if isinstance(input_data, str):
            return [{"role": "user", "content": [{"text": input_data}]}]
        result: list[dict[str, Any]] = []
        for message in input_data:
            if "content" in message:
                result.append(dict(message))
                continue
            role = str(message.get("role", "user"))
            content: list[dict[str, Any]] = []
            if message.get("text"):
                content.append({"text": str(message["text"])})
            if role == "assistant":
                for use in message.get("tool_uses", []):
                    content.append({"toolUse": {"toolUseId": use["call_id"], "name": use["name"], "input": use["arguments"]}})
            if role == "tool":
                content.append({"toolResult": {"toolUseId": message.get("call_id", ""), "content": [{"text": json.dumps(message.get("result", {}), default=str)}]}})
            result.append({"role": "assistant" if role == "assistant" else "user", "content": content or [{"text": ""}]})
        return result

    @staticmethod
    def _tool_spec(tool: dict[str, Any]) -> dict[str, Any]:
        """Translate the provider-neutral tool shape to Converse's tagged schema union."""
        spec = dict(tool)
        schema = spec.get("inputSchema", {})
        if not isinstance(schema, dict) or "json" not in schema:
            spec["inputSchema"] = {"json": schema}
        return spec
