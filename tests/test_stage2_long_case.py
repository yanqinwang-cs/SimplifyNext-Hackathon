import json

import pytest
from pydantic import TypeAdapter

from experiments.stage2_long_case.fixture import CASE_ID, HIDDEN, fresh_fixtures, run_fixture
from experiments.stage2_long_case.prompt import build_prompt, build_steward_prompt
from experiments.stage2_long_case.runner import MAX_MODEL_CALLS, MAX_STEPS, manifest, run_trajectory
from investigator.cycle import InvestigatorTurnResponse, LocalExhausted
from investigator.llm import ModelCallMetadata, ModelCallResult
from investigator.roles.steward import StewardDecision


class OfflineClient:
    def __init__(self, investigator=False):
        self.investigator = investigator
        self.calls = []

    def call(self, prompt, schema):
        self.calls.append((prompt, schema))
        if self.investigator:
            parsed = InvestigatorTurnResponse(graph_updates=[], next_step=LocalExhausted(reason="The visible graph is ready for global review."))
        else:
            parsed = TypeAdapter(StewardDecision).validate_python({"operation": "handoff_to_human", "assessment": "Neutral human review is now appropriate.", "reason": "The complete static source universe has been assessed.", "important_unresolved_ids": ["U1"], "reopening_conditions": "Reopen if material new evidence becomes available.", "handoff_summary": "The graph and source record are ready for human review."})
        return ModelCallResult(parsed=parsed, metadata=ModelCallMetadata(provider="offline", model="offline", latency_seconds=0.01, parse_success=True, input_tokens=10, output_tokens=5, finish_reason="stop"), raw_output=parsed.model_dump(mode="json"))


def test_public_loader_has_19_sources_and_never_loads_hidden():
    fixture = fresh_fixtures()[0]
    assert len(fixture.evidence) == 19
    assert all("hidden" not in item.filename for item in fixture.evidence)
    assert HIDDEN.exists()


def test_prompts_include_exact_public_sources_and_no_hidden_text():
    fixture = fresh_fixtures()[0]
    observation = __import__("investigator.cycle", fromlist=["InvestigatorCycleCoordinator"]).InvestigatorCycleCoordinator(fixture.graph, fixture.focus).observation()
    prompt = build_prompt(observation, fixture)
    steward = build_steward_prompt(__import__("investigator.cycle", fromlist=["InvestigatorCycleCoordinator"]).InvestigatorCycleCoordinator(fixture.graph, fixture.focus).steward_snapshot(), __import__("experiments.stage2_long_case.runner", fromlist=["_context"])._context(__import__("investigator.cycle", fromlist=["InvestigatorCycleCoordinator"]).InvestigatorCycleCoordinator(fixture.graph, fixture.focus)), fixture)
    hidden = "\n".join(path.read_text(encoding="utf-8") for path in HIDDEN.glob("*.md"))
    assert all(item.content in prompt and item.content in steward for item in fixture.evidence)
    assert hidden not in prompt and hidden not in steward


def test_offline_trajectory_calls_investigator_then_steward_and_handoffs():
    fixture = fresh_fixtures()[0]
    investigator, steward = OfflineClient(True), OfflineClient(False)
    result = run_trajectory(fixture, investigator, steward)
    assert result["termination_reason"] == "HANDOFF_TO_HUMAN"
    assert result["model_calls"] == 2
    assert [trace["actor"] for trace in result["traces"]] == ["investigator", "steward"]
    assert all(trace["raw_model_output"] is not None for trace in result["traces"])
    assert result["model_usage"]["Investigator"]["input_tokens"] == 10


def test_unknown_fixture_is_rejected():
    with pytest.raises(ValueError, match="Unknown fixture"):
        run_fixture(type("Unknown", (), {"case_id": "missing"})())


def test_manifest_records_mapping_budgets_and_hidden_hashes():
    fixture = fresh_fixtures()[0]
    value = manifest(fixture, "abc", "us.anthropic.claude-opus-4-5-20251101-v1:0")
    assert value["suite_version"] == "stage2a-long-case-v1"
    assert value["max_model_calls"] == MAX_MODEL_CALLS == 24
    assert value["max_orchestration_steps"] == MAX_STEPS == 60
    assert len(value["public_filename_to_evidence_id"]) == 19
    assert set(value["hidden_hashes"]) == {"ground_truth.md", "fixture_audit.md", "action_release_map.md"}
    assert value["hidden_files_exposed_to_models"] is False
