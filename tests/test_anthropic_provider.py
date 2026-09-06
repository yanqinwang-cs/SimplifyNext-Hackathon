import json
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from investigator.llm.anthropic import AnthropicConfigurationError, AnthropicModelClient
from investigator.llm.base import ModelParseError
from investigator.llm.bedrock import redact_sensitive_text
from investigator.llm.factory import ModelProviderConfigurationError, create_model_client
from investigator.model_registry import MODEL_REGISTRY


class Answer(BaseModel):
    answer: str


class FakeMessages:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


class FakeClient:
    def __init__(self, response):
        self.messages = FakeMessages(response)


def response(*blocks, stop_reason="end_turn"):
    return SimpleNamespace(
        model="claude-opus-4-5-20251101",
        content=list(blocks),
        usage=SimpleNamespace(input_tokens=11, output_tokens=7),
        stop_reason=stop_reason,
    )


def test_structured_call_extracts_system_and_preserves_metadata_without_network():
    fake = FakeClient(response(SimpleNamespace(type="text", text='```json\n{"answer":"4"}\n```')))
    client = AnthropicModelClient(model_id="claude-opus-4-5-20251101", client=fake)
    result = client.call([{"role": "system", "text": "Return JSON."}, {"role": "user", "text": "2 + 2"}], Answer)
    assert result.parsed.answer == "4"
    assert result.metadata.provider == "anthropic"
    assert result.metadata.model == "claude-opus-4-5-20251101"
    assert result.metadata.input_tokens == 11 and result.metadata.output_tokens == 7
    assert result.metadata.finish_reason == "end_turn"
    assert fake.messages.calls[0]["system"] == "Return JSON."
    assert fake.messages.calls[0]["temperature"] == 0
    assert json.dumps(result.raw_output)


def test_native_call_translates_tools_and_tool_results():
    fake = FakeClient(response(SimpleNamespace(type="text", text="Reading source."), SimpleNamespace(type="tool_use", id="t1", name="READ_SOURCE", input={"sourceHandle": "S1"})))
    client = AnthropicModelClient(model_id="claude-opus-4-5-20251101", client=fake)
    result = client.call_native(
        [{"role": "system", "text": "Read only."}, {"role": "user", "text": "Read S1."}, {"role": "assistant", "text": "", "tool_uses": [{"call_id": "t1", "name": "READ_SOURCE", "arguments": {"sourceHandle": "S1"}}]}, {"role": "tool", "call_id": "t1", "result": {"ok": True}}],
        [{"name": "READ_SOURCE", "description": "read", "inputSchema": {"type": "object"}}],
    )
    assert result.tool_uses[0].call_id == "t1"
    assert fake.messages.calls[0]["tools"][0]["input_schema"] == {"type": "object"}
    assert fake.messages.calls[0]["messages"][-1]["content"][0]["type"] == "tool_result"


def test_parse_failure_preserves_raw_text_and_does_not_repair():
    raw = '{"wrong": "field"}'
    fake = FakeClient(response(SimpleNamespace(type="text", text=raw)))
    with pytest.raises(ModelParseError) as caught:
        AnthropicModelClient(model_id="claude-opus-4-5-20251101", client=fake).call("return JSON", Answer)
    assert caught.value.raw_output == raw


def test_configuration_and_provider_selection_are_explicit(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(AnthropicConfigurationError, match="ANTHROPIC_API_KEY"):
        create_model_client(MODEL_REGISTRY["anthropic.claude-opus-4-5"])
    monkeypatch.setenv("CASELENS_MODEL_PROVIDER", "unsupported")
    with pytest.raises(ModelProviderConfigurationError, match="anthropic.*bedrock"):
        create_model_client(MODEL_REGISTRY["anthropic.claude-opus-4-5"], client=object())


def test_direct_and_bedrock_ids_are_distinct_and_secret_is_redacted():
    spec = MODEL_REGISTRY["anthropic.claude-opus-4-5"]
    assert spec.provider_model_id("anthropic") == "claude-opus-4-5-20251101"
    assert spec.provider_model_id("bedrock") == "us.anthropic.claude-opus-4-5-20251101-v1:0"
    bedrock = create_model_client(spec, provider="bedrock", client=object())
    assert bedrock.model_id == spec.invocation_id
    assert "sk-ant-secret" not in redact_sensitive_text("key=sk-ant-secret")
