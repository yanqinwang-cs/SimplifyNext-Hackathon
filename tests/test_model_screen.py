from pathlib import Path

import pytest

from experiments.model_screen.cases import CASES, render_case
from experiments.model_screen.run import run_case
from experiments.model_screen.schemas import HypothesisResponse
from investigator.llm.base import ModelCallMetadata, ModelCallResult


class FakeClient:
    def __init__(self, results: list[ModelCallResult | Exception]) -> None:
        self.results = iter(results)
        self.calls = []

    def call(self, input_data, output_schema):
        self.calls.append((input_data, output_schema))
        result = next(self.results)
        if isinstance(result, Exception):
            raise result
        return result


def model_result() -> ModelCallResult:
    return ModelCallResult(
        parsed=HypothesisResponse(hypotheses=[
            {"statement": "A", "justification": "E1", "uncertainty": "U1"},
            {"statement": "B", "justification": "E2", "uncertainty": "U2"},
        ]),
        metadata=ModelCallMetadata(provider="fake", model="model", latency_seconds=0.1, parse_success=True),
        raw_output='{"hypotheses": []}',
    )


def test_cases_are_model_visible_without_hidden_labels() -> None:
    assert len(CASES) == 5
    rendered = render_case(CASES[0])
    assert "CASE 01" in rendered
    assert "correct answer" not in rendered.lower()
    assert "red herring" not in rendered.lower()


def test_output_schema_requires_two_to_four_hypotheses() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        HypothesisResponse(hypotheses=[{"statement": "A", "justification": "E1", "uncertainty": "U1"}])


def test_run_case_calls_once_and_preserves_output() -> None:
    client = FakeClient([model_result()])
    result = run_case(client, "model", "case_01")
    assert len(client.calls) == 1
    assert client.calls[0][1] is HypothesisResponse
    assert result.parse_success is True
    assert result.raw_model_output == '{"hypotheses": []}'
    assert result.metadata.provider == "fake"


def test_run_case_records_failure_without_retry() -> None:
    client = FakeClient([ValueError("malformed response")])
    result = run_case(client, "model", "case_02")
    assert len(client.calls) == 1
    assert result.parse_success is False
    assert result.error_message == "malformed response"


def test_all_case_execution_creates_result_files_and_does_not_overwrite(tmp_path: Path) -> None:
    from experiments.model_screen.run import run

    client = FakeClient([model_result() for _ in CASES])
    output_dir = run("test-model", None, tmp_path, client=client)
    assert len(client.calls) == 5
    assert sorted(path.name for path in output_dir.glob("case_*.json")) == [
        f"case_0{i}.json" for i in range(1, 6)
    ]
    assert (output_dir / "run_summary.json").is_file()
    with pytest.raises(FileExistsError):
        run("test-model", None, tmp_path, client=FakeClient([model_result() for _ in CASES]))


def test_api_error_is_recorded_without_retry() -> None:
    client = FakeClient([RuntimeError("Bedrock unavailable")])
    result = run_case(client, "test-model", "case_04")
    assert len(client.calls) == 1
    assert result.parse_success is False
    assert result.error_message == "Bedrock unavailable"
