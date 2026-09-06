"""Single provider resolver for the production vNext model path."""
from __future__ import annotations

import os
from typing import Any

from investigator.model_registry import ModelSpec
from investigator.llm.anthropic import AnthropicConfigurationError, AnthropicModelClient
from investigator.llm.bedrock import BedrockModelClient


class ModelProviderConfigurationError(ValueError):
    """The selected provider is unsupported or incompletely configured."""


def configured_provider(provider: str | None = None) -> str:
    selected = (provider or os.getenv("CASELENS_MODEL_PROVIDER", "anthropic")).strip().lower()
    if selected not in {"anthropic", "bedrock"}:
        raise ModelProviderConfigurationError("CASELENS_MODEL_PROVIDER must be 'anthropic' or 'bedrock'")
    return selected


def create_model_client(spec: ModelSpec, *, provider: str | None = None, client: Any | None = None):
    selected = configured_provider(provider)
    if selected == "anthropic":
        if client is None and not os.getenv("ANTHROPIC_API_KEY"):
            raise AnthropicConfigurationError("Anthropic API configuration is missing; set ANTHROPIC_API_KEY")
        return AnthropicModelClient(model_id=spec.provider_model_id("anthropic"), client=client)
    return BedrockModelClient(model_id=spec.provider_model_id("bedrock"), region=spec.region, client=client)
