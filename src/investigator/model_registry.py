"""Canonical logical-model to provider-invocation configuration."""

from pydantic import BaseModel


class ModelSpec(BaseModel):
    name: str
    invocation_id: str
    anthropic_model_id: str | None = None
    region: str = "us-east-1"

    def provider_model_id(self, provider: str) -> str:
        if provider == "bedrock":
            return self.invocation_id
        if provider == "anthropic" and self.anthropic_model_id:
            return self.anthropic_model_id
        raise ValueError(f"Model {self.name!r} has no {provider} provider mapping")


MODEL_REGISTRY = {
    name: ModelSpec(name=name, invocation_id=name)
    for name in (
        "zai.glm-5", "deepseek.v3.2", "moonshot.kimi-k2-thinking",
        "qwen.qwen3-next-80b-a3b", "openai.gpt-oss-120b-1:0",
        "amazon.nova-2-lite-v1:0", "zai.glm-4.7-flash",
    )
}
MODEL_REGISTRY.update({
    name: ModelSpec(name=name, invocation_id=invocation_id, anthropic_model_id=anthropic_model_id)
    for name, (invocation_id, anthropic_model_id) in {
        "anthropic.claude-haiku-4-5": ("us.anthropic.claude-haiku-4-5-20251001-v1:0", "claude-haiku-4-5-20251001"),
        "anthropic.claude-sonnet-4-5": ("us.anthropic.claude-sonnet-4-5-20250929-v1:0", "claude-sonnet-4-5-20250929"),
        "anthropic.claude-opus-4-5": ("us.anthropic.claude-opus-4-5-20251101-v1:0", "claude-opus-4-5-20251101"),
    }.items()
})
