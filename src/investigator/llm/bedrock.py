import json
import os
from time import perf_counter
from typing import Any

from pydantic import BaseModel

from investigator.llm.base import (
    MessageInput,
    ModelCallMetadata,
    ModelCallResult,
    ModelParseError,
    parse_model_output,
)


class BedrockConfigurationError(ValueError):
    """Raised when the Bedrock adapter lacks required environment configuration."""


class BedrockModelClient:
    """Small adapter for one JSON structured call through Bedrock Converse."""

    def __init__(self, model_id: str | None = None, region: str | None = None, client: Any = None) -> None:
        self.model_id = model_id or os.getenv("BEDROCK_MODEL_ID")
        self.region = region or os.getenv("AWS_REGION", "us-east-1")
        if not self.model_id:
            raise BedrockConfigurationError(
                "Bedrock model configuration is missing; set BEDROCK_MODEL_ID"
            )
        if client is None:
            try:
                import boto3
            except ImportError as exc:
                raise BedrockConfigurationError(
                    "boto3 is required for BedrockModelClient"
                ) from exc
            client = boto3.client("bedrock-runtime", region_name=self.region)
        self.client = client

    def call(self, input_data: MessageInput, output_schema: type[BaseModel]) -> ModelCallResult:
        messages = self._messages(input_data)
        started = perf_counter()
        response = self.client.converse(
            modelId=self.model_id,
            messages=messages,
            inferenceConfig={"temperature": 0},
        )
        raw_text = response["output"]["message"]["content"][0]["text"]
        metadata_response = response.get("usage", {})
        stop_reason = response.get("stopReason")
        try:
            parsed = parse_model_output(json.loads(raw_text), output_schema)
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

    @staticmethod
    def _messages(input_data: MessageInput) -> list[dict[str, Any]]:
        if isinstance(input_data, str):
            return [{"role": "user", "content": [{"text": input_data}]}]
        return [dict(message) for message in input_data]
