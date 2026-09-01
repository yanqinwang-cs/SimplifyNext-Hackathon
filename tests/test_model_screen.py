from pathlib import Path
import json

import pytest

from experiments.model_screen.cases import CASES, render_case
from experiments.model_screen.run import run_case
from experiments.model_screen.schemas import CaseResult, RunSummary
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
    second_output_dir = run("test-model-2", None, tmp_path, client=FakeClient([model_result() for _ in CASES]))
    assert second_output_dir != output_dir
    assert (output_dir / "run_summary.json").is_file()
    assert (second_output_dir / "run_summary.json").is_file()


def test_api_error_is_recorded_without_retry() -> None:
    client = FakeClient([RuntimeError("Bedrock unavailable")])
    result = run_case(client, "test-model", "case_04")
    assert len(client.calls) == 1
    assert result.parse_success is False
    assert result.error_message == "Bedrock unavailable"


def test_offline_reparse_repairs_failed_result_and_is_idempotent(tmp_path: Path) -> None:
    from scripts.reparse_model_screen_results import reparse_results

    directory = tmp_path / "screen_run"
    directory.mkdir()
    raw = '```json\n{"hypotheses": [{"statement": "A", "justification": "E1", "uncertainty": "U1"}, {"statement": "B", "justification": "E2", "uncertainty": "U2"}]}\n```'
    failed = CaseResult(
        case_id="case_01", model_id="model", case_input="case", prompt="prompt",
        raw_model_output=raw, parse_success=False, error_message="fenced output",
    )
    (directory / "case_01.json").write_text(failed.model_dump_json(indent=2) + "\n", encoding="utf-8")
    (directory / "run_summary.json").write_text(RunSummary(
        model_id="model", result_directory=str(directory), case_ids=["case_01"],
        successful_cases=0, failed_cases=1,
    ).model_dump_json(indent=2) + "\n", encoding="utf-8")

    summary = reparse_results(directory)
    repaired = json.loads((directory / "case_01.json").read_text(encoding="utf-8"))
    assert summary.successful_cases == 1
    assert summary.failed_cases == 0
    assert repaired["parse_success"] is True
    assert repaired["error_message"] is None
    assert repaired["raw_model_output"] == raw
    first_repaired = (directory / "case_01.json").read_bytes()

    summary_again = reparse_results(directory)
    assert summary_again.successful_cases == 1
    assert summary_again.failed_cases == 0
    assert (directory / "case_01.json").read_bytes() == first_repaired
