import json
import sys
from pathlib import Path

import pytest

from experiments.investigator_screen import live
from investigator.llm import ModelCallMetadata, ModelCallResult


VALID_PAYLOAD = {"graph_updates": [], "next_step": {"type": "local_exhausted", "reason": "No useful local work remains."}}


def test_dry_run_makes_zero_model_calls(monkeypatch, capsys):
    calls = []

    class UnexpectedClient:
        def __init__(self, *args, **kwargs):
            calls.append((args, kwargs))

    monkeypatch.setattr(live, "BedrockModelClient", UnexpectedClient)
    monkeypatch.setattr(sys, "argv", ["live", "--models", "zai.glm-5", "--fixtures", "INV1", "--dry-run"])
    live.main()
    output = json.loads(capsys.readouterr().out)
    assert calls == []
    assert output["selected_fixtures"] == ["INV1"]
    assert output["aws_calls"] == 0


def test_unknown_fixture_and_model_are_rejected(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["live", "--models", "zai.glm-5", "--fixtures", "INV99", "--dry-run"])
    with pytest.raises(SystemExit) as fixture_error:
        live.main()
    assert fixture_error.value.code == 2

    monkeypatch.setattr(sys, "argv", ["live", "--models", "not-registered", "--fixtures", "INV1", "--dry-run"])
    with pytest.raises(SystemExit) as model_error:
        live.main()
    assert model_error.value.code == 2


def test_mocked_valid_call_evaluates_and_writes_artifacts(tmp_path, monkeypatch, capsys):
    class FakeClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def call(self, prompt, output_schema):
            return ModelCallResult(
                parsed=output_schema.model_validate(VALID_PAYLOAD),
                raw_output=json.dumps(VALID_PAYLOAD),
                metadata=ModelCallMetadata(provider="fake", model="fake", latency_seconds=0.2, input_tokens=10, output_tokens=8, parse_success=True, finish_reason="end_turn"),
            )

    monkeypatch.setattr(live, "BedrockModelClient", FakeClient)
    monkeypatch.setattr(sys, "argv", ["live", "--models", "zai.glm-5", "--fixtures", "INV12", "--output-dir", str(tmp_path)])
    live.main()
    stdout = capsys.readouterr().out
    assert "INV12: PASS" in stdout
    assert "graph_updates" not in stdout
    manifest_path = next(tmp_path.rglob("manifest.json"))
    trace_path = next(tmp_path.rglob("raw_traces.jsonl"))
    assert manifest_path.is_absolute() and manifest_path.exists()
    assert trace_path.is_absolute() and trace_path.exists()
    trace = json.loads(trace_path.read_text().splitlines()[0])
    assert trace["raw_output"] == json.dumps(VALID_PAYLOAD)
    assert trace["schema_valid"] is True
    assert trace["production_applied"] is True
    assert trace["semantic_pass"] is True
    assert trace["input_tokens"] == 10
    assert trace["output_tokens"] == 8
    assert json.loads(manifest_path.read_text())["selected_fixture_ids"] == ["INV12"]


def test_live_runner_uses_production_prompt_and_response_path():
    source = Path(live.__file__).read_text()
    assert "build_investigator_cycle_prompt" in source
    assert "InvestigatorTurnResponse" in source
    assert "evaluate_payload" in source
