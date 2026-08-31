import pytest
from pydantic import BaseModel

from experiments.gate1.runner import ExperimentRunner
from experiments.gate1.schemas import ExperimentInput
from investigator.llm.base import ModelCallMetadata, ModelParseError
from investigator.llm.bedrock import BedrockConfigurationError, BedrockModelClient
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

