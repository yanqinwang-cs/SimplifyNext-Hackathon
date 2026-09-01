import hashlib
import inspect
import json
from pathlib import Path

import pytest
from pydantic import TypeAdapter

from experiments.steward_screen import sequential_live
from experiments.steward_screen.fresh_fixtures import HISTORICAL_SUITE_HASH, SUITE_VERSION, fresh_fixtures
from investigator.llm import ModelCallMetadata, ModelCallResult, ModelParseError
from investigator.roles import StewardDecision
from investigator.schema_fingerprint import schema_fingerprint


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_dry_run_makes_no_model_calls(monkeypatch, capsys):
    calls = []

    class UnexpectedClient:
        def __init__(self, *args, **kwargs):
            calls.append((args, kwargs))

    monkeypatch.setattr(sequential_live, "BedrockModelClient", UnexpectedClient)
    monkeypatch.setattr("sys.argv", ["sequential_live", "--models", "zai.glm-5", "--fixtures", "SEQ7", "--dry-run"])
    sequential_live.main()
    payload = json.loads(capsys.readouterr().out)
    assert calls == []
    assert payload["fixtures"] == ["SEQ7"]
    assert payload["aws_calls"] == 0


@pytest.mark.parametrize("fixture_id", ["SEQ7", "SEQ2"])
def test_dry_run_selects_requested_fixture(monkeypatch, capsys, fixture_id):
    monkeypatch.setattr("sys.argv", ["sequential_live", "--models", "zai.glm-5", "--fixtures", fixture_id, "--dry-run"])
    sequential_live.main()
    assert json.loads(capsys.readouterr().out)["fixtures"] == [fixture_id]


def test_unknown_fixture_is_rejected(monkeypatch):
    monkeypatch.setattr("sys.argv", ["sequential_live", "--models", "zai.glm-5", "--fixtures", "NOPE", "--dry-run"])
    with pytest.raises(SystemExit) as exc_info:
        sequential_live.main()
    assert exc_info.value.code == 2


def test_manifest_uses_current_sequential_v2_fingerprints():
    manifest = sequential_live.frozen_manifest()
    root = Path(sequential_live.__file__).parents[2]
    fixtures = fresh_fixtures()
    fixture_payload = json.dumps([f.model_dump(mode="json") for f in fixtures], sort_keys=True, separators=(",", ":")).encode()
    assert manifest["suite_version"] == SUITE_VERSION
    assert manifest["fixture_suite_hash"] == hashlib.sha256(fixture_payload).hexdigest()
    assert manifest["historical_fixture_suite_hash"] == HISTORICAL_SUITE_HASH
    assert manifest["schema_hash"] == schema_fingerprint(TypeAdapter(StewardDecision).json_schema())
    assert manifest["evaluator_hash"] == _sha(root / "experiments/steward_screen/trajectory.py")
    assert manifest["prompt_source_hash"] == _sha(root / "experiments/steward_screen/prompt.py")
    assert manifest["fixture_count"] == 8
    assert manifest["step_caps"] == {fixture.fixture_id: fixture.step_cap for fixture in fixtures}


def test_live_trace_records_each_call_metadata_and_raw_output(tmp_path, monkeypatch):
    class FakeClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def call(self, prompt, output_schema):
            return ModelCallResult(
                parsed=output_schema.model_validate({"operation": "keep_focus", "assessment": "ok", "reason": "ok"}),
                raw_output='{"operation":"keep_focus","assessment":"ok","reason":"ok"}',
                metadata=ModelCallMetadata(provider="fake", model="fake", latency_seconds=0.25, input_tokens=11, output_tokens=7, parse_success=True, finish_reason="end_turn"),
            )

    monkeypatch.setattr(sequential_live, "BedrockModelClient", FakeClient)
    sequential_live.run_live("zai.glm-5", ["SEQ7"], tmp_path)
    trace = json.loads((tmp_path / "raw_traces.jsonl").read_text().splitlines()[0])
    assert trace["fixture_id"] == "SEQ7"
    assert trace["calls"][0]["raw_output"]
    assert trace["calls"][0]["input_tokens"] == 11
    assert trace["calls"][0]["output_tokens"] == 7
    assert trace["calls"][0]["latency_seconds"] == 0.25
    assert trace["calls"][0]["stop_reason"] == "end_turn"
    assert (tmp_path / "manifest.json").exists()


def test_parse_failure_preserves_raw_output_in_call_trace():
    class FailingClient:
        def call(self, prompt, output_schema):
            raise ModelParseError("invalid response", raw_output="not-json")

    producer = sequential_live.LiveProducer(FailingClient())
    with pytest.raises(ModelParseError):
        producer("prompt")
    assert producer.calls == [{
        "prompt_hash": hashlib.sha256(b"prompt").hexdigest(),
        "raw_output": "not-json",
        "latency_seconds": None,
        "input_tokens": None,
        "output_tokens": None,
        "stop_reason": None,
        "parse_success": False,
        "error": "invalid response",
    }]


def test_live_runner_uses_sequential_apis_not_legacy_screen():
    source = inspect.getsource(sequential_live)
    assert "fresh_fixtures" in source
    assert "run_fixture" in source
    assert "all_scenarios" not in source
    assert "evaluate_result" not in source
