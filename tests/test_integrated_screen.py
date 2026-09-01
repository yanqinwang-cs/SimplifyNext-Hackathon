from __future__ import annotations

from copy import deepcopy

import pytest

from experiments.integrated_screen.environment import Stage1Environment
from experiments.integrated_screen.fixtures import all_fixtures, fixture_map
from experiments.integrated_screen.runner import dry_run, live_review_context, run_trajectory
from experiments.integrated_screen.prompt import build_investigator_prompt
from investigator.cycle import CycleError, CycleStatus, InvestigatorCycleCoordinator
from investigator.llm import ModelCallMetadata, ModelParseError, MockModelClient
from investigator.roles import KeepFocusDecision


def test_all_stage1_fixtures_validate_and_dry_run_is_offline(monkeypatch):
    assert [fixture.fixture_id for fixture in all_fixtures()] == ["C1", "C2", "C3", "C4"]
    monkeypatch.setattr("experiments.integrated_screen.runner.BedrockModelClient", lambda **_: pytest.fail("AWS client constructed in dry-run"))
    assert dry_run()["model_calls"] == 0


def test_environment_releases_evidence_and_refreshes_actions():
    fixture = fixture_map()["C4"]
    environment = Stage1Environment.for_fixture(fixture)
    assert [a.action_id for a in environment.current_available_enquiries()] == ["A1", "A2"]
    release = environment.execute_enquiry("A1")
    assert [node.id for node in release.evidence] == ["E2"]
    assert "A1" not in [a.action_id for a in environment.current_available_enquiries()]
    with pytest.raises(ValueError, match="already been completed"):
        environment.execute_enquiry("A1")


def test_trusted_ingestion_releases_evidence_and_recent_marker_is_consumed():
    fixture = fixture_map()["C1"]
    environment = Stage1Environment.for_fixture(fixture)
    coordinator = InvestigatorCycleCoordinator(deepcopy(fixture.graph), fixture.focus, available_enquiries=environment.current_available_enquiries(), max_turns_per_tenure=3)
    coordinator.apply_turn({"graph_updates": [], "next_step": {"type": "request_enquiry", "reason": "Need the source record.", "action_id": "A1", "target_uncertainty_id": "U1", "expected_information_value": "It may distinguish common preparation from independent work."}})
    release = environment.execute_enquiry("A1")
    coordinator.complete_enquiry_with_evidence("A1", release.evidence)
    coordinator.set_available_enquiries(environment.current_available_enquiries())
    assert coordinator.observation().recently_released_evidence_ids == ["E2"]
    coordinator.apply_turn({"graph_updates": [], "next_step": {"type": "local_exhausted", "reason": "The released evidence is now visible for review."}})
    assert coordinator.observation().recently_released_evidence_ids == []


def test_evidence_cannot_be_ingested_without_enquiry_or_reexecuted():
    fixture = fixture_map()["C1"]
    environment = Stage1Environment.for_fixture(fixture)
    coordinator = InvestigatorCycleCoordinator(deepcopy(fixture.graph), fixture.focus, available_enquiries=environment.current_available_enquiries())
    with pytest.raises(CycleError, match="requires an enquiry in flight"):
        coordinator.ingest_environment_evidence(list(fixture.releases["A1"].evidence))


def test_live_context_reflects_current_frontier_without_neglected_heuristic():
    fixture = fixture_map()["C1"]
    environment = Stage1Environment.for_fixture(fixture)
    coordinator = InvestigatorCycleCoordinator(deepcopy(fixture.graph), fixture.focus, available_enquiries=environment.current_available_enquiries())
    context = live_review_context(coordinator, environment)
    assert context.available_action_ids == ["A1"]
    assert context.neglected_candidate_node_ids == []
    assert context.materially_usable_action_ids == ["A1"]


def test_hidden_release_is_not_in_initial_investigator_observation():
    fixture = fixture_map()["C1"]
    environment = Stage1Environment.for_fixture(fixture)
    coordinator = InvestigatorCycleCoordinator(deepcopy(fixture.graph), fixture.focus, available_enquiries=environment.current_available_enquiries())
    prompt = build_investigator_prompt(coordinator.observation())
    assert "commonly distributed practice solution" not in prompt


class ScriptedClient:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def call(self, input_data, output_schema):
        self.calls.append((input_data, output_schema))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return MockModelClient(response, ModelCallMetadata(provider="mock", model="scripted", latency_seconds=0.01, parse_success=True)).call(input_data, output_schema)


def test_runner_records_environment_and_model_trace_and_preserves_failure():
    investigator = ScriptedClient({"graph_updates": [], "next_step": {"type": "request_enquiry", "reason": "Need the source record.", "action_id": "A1", "target_uncertainty_id": "U1", "expected_information_value": "It may resolve the source question."}})
    steward = ScriptedClient(ModelParseError("bad steward", raw_output="{not-json"))
    result = run_trajectory(fixture_map()["C1"], investigator, steward, max_investigator_turns_per_tenure=1)
    assert result["termination"] == "FAIL / STEWARD_SCHEMA"
    assert result["completed_action_ids"] == ["A1"]
    assert any(trace["actor"] == "environment" and trace["environment_release"] for trace in result["traces"])
    failed = result["traces"][-1]
    assert failed["raw_model_output"] == "bad steward" or failed["raw_model_output"] == "{not-json"
    assert len(investigator.calls) == 1
    assert len(steward.calls) == 1


def test_runner_does_not_allow_evidence_creation_by_investigator():
    investigator = ScriptedClient({"graph_updates": [{"operation": "add_proposition", "node_id": "E9", "statement": "invented", "derived_from_node_ids": ["E1"], "reason": "This should fail the evidence ID contract."}], "next_step": {"type": "local_exhausted", "reason": "Stop after invalid update."}})
    steward = ScriptedClient(KeepFocusDecision(assessment="keep", reason="keep"))
    result = run_trajectory(fixture_map()["C1"], investigator, steward)
    assert result["termination"] == "FAIL / INVESTIGATOR_SCHEMA" or result["termination"] == "FAIL / INVESTIGATOR_APPLY"
