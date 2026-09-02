import pytest
import sys
import types
from pydantic import BaseModel

from experiments.gate1.runner import ExperimentRunner
from experiments.gate1.schemas import ExperimentInput
from investigator.llm.base import ModelCallMetadata, ModelParseError
from investigator.llm.base import normalize_json_text
from investigator.llm.bedrock import BedrockConfigurationError, BedrockModelClient, CredentialOverride, clear_credential_override, credential_status, set_credential_override
from investigator.llm.mock import MockModelClient


class StructuredOutput(BaseModel):
    label: str
    count: int


def test_mock_returns_predefined_structured_output_without_network() -> None:
    metadata = ModelCallMetadata(
        provider="mock", model="fixture", latency_seconds=0.01, parse_success=True,
        input_tokens=3, output_tokens=2, finish_reason="stop",
    )
    client = MockModelClient(StructuredOutput(label="ok", count=2), metadata)
    result = client.call("input", StructuredOutput)
    assert result.parsed == StructuredOutput(label="ok", count=2)
    assert result.metadata == metadata


def test_runner_passes_input_and_preserves_result_metadata() -> None:
    metadata = ModelCallMetadata(provider="mock", model="fixture", latency_seconds=0.2, parse_success=True)
    client = MockModelClient({"label": "ready", "count": 1}, metadata)
    result = ExperimentRunner(client).run(
        ExperimentInput(case_id="case-1", turn_number=2, current_case_state={"revision": 1}),
        StructuredOutput,
    )
    assert result.run.case_id == "case-1"
    assert result.run.turn_number == 2
    assert result.structured_result == StructuredOutput(label="ready", count=1)
    assert result.call_metadata == metadata
    assert client.calls[0][0]["case_id"] == "case-1"
    assert client.calls[0][1] is StructuredOutput


def test_mock_parse_failure_is_clear() -> None:
    client = MockModelClient({"label": "wrong", "count": "not-an-int"})
    with pytest.raises(ModelParseError, match="StructuredOutput"):
        client.call("input", StructuredOutput)


def test_missing_bedrock_configuration_is_clear(monkeypatch) -> None:
    monkeypatch.delenv("BEDROCK_MODEL_ID", raising=False)
    with pytest.raises(BedrockConfigurationError, match="BEDROCK_MODEL_ID"):
        BedrockModelClient()


@pytest.mark.parametrize("raw, expected", [
    ('{"answer": "ok"}', '{"answer": "ok"}'),
    ('```json\n{"answer": "ok"}\n```', '{"answer": "ok"}\n'),
    ('```\n {"answer": "ok"} \n```', ' {"answer": "ok"} \n'),
    ('  {"answer": "ok"}  ', '{"answer": "ok"}'),
])
def test_json_normalization_accepts_only_minimal_outer_fences(raw: str, expected: str) -> None:
    assert normalize_json_text(raw) == expected


@pytest.mark.parametrize("raw", [
    'Here is the JSON:\n{"answer": "ok"}',
    '{"answer": "ok"}\nThis is the answer.',
    '```json\n{"answer": }\n```',
])
def test_json_normalization_does_not_extract_or_repair(raw: str) -> None:
    import json

    with pytest.raises(json.JSONDecodeError):
        json.loads(normalize_json_text(raw))


def test_bedrock_adapter_parses_fenced_json_without_network() -> None:
    class BedrockOutput(BaseModel):
        answer: str

    class FakeBedrock:
        def converse(self, **kwargs):
            return {
                "output": {"message": {"content": [{"text": '```json\n{"answer": "4"}\n```'}]}},
                "usage": {"inputTokens": 5, "outputTokens": 2},
                "stopReason": "end_turn",
            }

    result = BedrockModelClient(model_id="test-model", region="us-east-1", client=FakeBedrock()).call(
        "What is 2 + 2?", BedrockOutput
    )
    assert result.parsed.answer == "4"
    assert result.raw_output == '```json\n{"answer": "4"}\n```'


def test_bedrock_native_tool_schema_uses_json_tagged_union() -> None:
    seen = {}

    class FakeBedrock:
        def converse(self, **kwargs):
            seen.update(kwargs)
            return {"output": {"message": {"content": [{"text": "done"}]}}, "usage": {}, "stopReason": "end_turn"}

    BedrockModelClient(model_id="test-model", client=FakeBedrock()).call_native(
        "run", [{"name": "RUN_INVESTIGATION", "description": "run", "inputSchema": {"type": "object", "properties": {}, "required": []}}]
    )
    tool_spec = seen["toolConfig"]["tools"][0]["toolSpec"]
    assert set(tool_spec["inputSchema"]) == {"json"}
    assert tool_spec["inputSchema"]["json"]["type"] == "object"


def test_bedrock_native_tool_schema_preserves_required_fields() -> None:
    schema = {"type": "object", "properties": {"source_id": {"type": "string"}}, "required": ["source_id"], "additionalProperties": False}
    tool = BedrockModelClient._tool_spec({"name": "READ_SOURCE", "inputSchema": schema})
    assert tool["inputSchema"] == {"json": schema}


def test_bedrock_native_tool_schema_preserves_optional_fields_and_avoids_double_wrap() -> None:
    schema = {"type": "object", "properties": {"note": {"type": "string"}}, "additionalProperties": False}
    assert BedrockModelClient._tool_spec({"name": "FULFIL_REQUEST", "inputSchema": schema})["inputSchema"] == {"json": schema}
    wrapped = {"json": schema}
    assert BedrockModelClient._tool_spec({"name": "FULFIL_REQUEST", "inputSchema": wrapped})["inputSchema"] == wrapped


def test_bedrock_override_precedence_invalidation_and_clear(monkeypatch) -> None:
    sessions = []

    class FakeClient:
        def __init__(self, label):
            self.label = label

        def converse(self, **kwargs):
            return {"output": {"message": {"content": [{"text": '{"label": "ok", "count": 4}'}]}}, "usage": {}, "stopReason": "stop"}

    class FakeSession:
        def __init__(self, **kwargs):
            sessions.append(kwargs)
            self.label = kwargs["aws_access_key_id"]

        def client(self, _service):
            return FakeClient(self.label)

    fake_boto3 = types.SimpleNamespace(
        Session=FakeSession,
        client=lambda _service, **kwargs: FakeClient("default-chain"),
    )
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)
    monkeypatch.setenv("BEDROCK_MODEL_ID", "offline-model")
    clear_credential_override()
    client = BedrockModelClient()
    set_credential_override(CredentialOverride("ACCESS_A", "SECRET_A", "TOKEN_A"))
    client.call("2 + 2", StructuredOutput)
    set_credential_override(CredentialOverride("ACCESS_B", "SECRET_B", "TOKEN_B"))
    client.call("2 + 2", StructuredOutput)
    clear_credential_override()
    client.call("2 + 2", StructuredOutput)
    assert [item["aws_access_key_id"] for item in sessions] == ["ACCESS_A", "ACCESS_B"]
    assert client.client.label == "default-chain"


def test_credential_status_is_non_secret_and_debug_flag(monkeypatch) -> None:
    set_credential_override(CredentialOverride("TEST_ACCESS_SECRET_123", "TEST_SECRET_SECRET_456", "TEST_TOKEN_SECRET_789"))
    try:
        monkeypatch.setenv("SIMPLIFYNEXT_DEBUG_CREDENTIALS", "1")
        status = credential_status()
        serialized = str(status)
        assert status["override_active"] is True
        assert all(secret not in serialized for secret in ("TEST_ACCESS_SECRET_123", "TEST_SECRET_SECRET_456", "TEST_TOKEN_SECRET_789"))
        monkeypatch.setenv("SIMPLIFYNEXT_DEBUG_CREDENTIALS", "0")
        assert not __import__("investigator.llm.bedrock", fromlist=["debug_credentials_enabled"]).debug_credentials_enabled()
    finally:
        clear_credential_override()
