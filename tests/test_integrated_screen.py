from __future__ import annotations

from copy import deepcopy

import pytest

from experiments.integrated_screen.environment import EvidenceRelease, Stage1Environment
from experiments.integrated_screen.fixtures import Stage1Fixture, all_fixtures, fixture_map
from experiments.integrated_screen.evaluate import TrajectoryRequirements, evaluate_trajectory
from experiments.integrated_screen.runner import _graph_delta, dry_run, live_review_context, manifest, run_trajectory
from experiments.integrated_screen.prompt import build_investigator_prompt
from investigator.cycle import CycleError, CycleStatus, InvestigatorCycleCoordinator
from investigator.cycle_prompt import build_investigator_cycle_prompt
from investigator.graph import EdgeRelation, GraphNode, GraphNodeType, GraphStatus
from investigator.llm import ModelCallMetadata, ModelParseError, MockModelClient
from investigator.roles import KeepFocusDecision
from investigator.roles import GraphInvestigationCoordinator, InvestigationFocus


def test_all_stage1_fixtures_validate_and_dry_run_is_offline(monkeypatch):
    assert [fixture.fixture_id for fixture in all_fixtures()] == ["C1", "C2", "C3", "C4"]
    monkeypatch.setattr("experiments.integrated_screen.runner.BedrockModelClient", lambda **_: pytest.fail("AWS client constructed in dry-run"))
    assert dry_run()["model_calls"] == 0


def test_fixture_requirements_are_typed():
    assert all(isinstance(fixture.requirements, TrajectoryRequirements) for fixture in all_fixtures())


def test_frozen_fixture_shapes_and_release_statements():
    c1, c2, c3, c4 = [fixture_map()[key] for key in ("C1", "C2", "C3", "C4")]
    assert set(c1.graph.nodes) == {"E1", "P1", "H1", "H2", "U1"}
    assert c1.focus.node_id == "U1" and c1.available_enquiries[0].kind.value == "review"
    assert c1.releases["A1"].evidence[0].id == "E2"
    assert c1.graph.edges["U1_TARGETS_P1"].relation is EdgeRelation.TARGETS
    assert c2.graph.edges["H1.1_SPECIALIZES_H1"].relation is EdgeRelation.SPECIALIZES
    assert c2.graph.nodes["H1.1"].node_type is GraphNodeType.HYPOTHESIS
    assert c2.available_enquiries[0].addressable_uncertainty_ids == ["U1"]
    assert c2.requirements.require_stop_unresolved is False
    assert c3.releases["A1"].evidence[0].id == "E2" and c3.releases["A2"].evidence[0].id == "E3"
    assert {action.action_id for action in c3.available_enquiries} == {"A1", "A2"}
    assert set(c4.releases) == {"A1", "A2", "A3"} and {node.id for node in c4.releases["A3"].evidence} == {"E4"}


def test_material_change_requirement_requires_a_release_anchor():
    with pytest.raises(ValueError, match="requires at least one required release"):
        TrajectoryRequirements(require_material_graph_change_after_release=True)


def test_environment_releases_evidence_and_refreshes_actions():
    fixture = fixture_map()["C4"]
    environment = Stage1Environment.for_fixture(fixture)
    assert [a.action_id for a in environment.current_available_enquiries()] == ["A1", "A2", "A3"]
    release = environment.execute_enquiry("A1")
    assert [node.id for node in release.evidence] == ["E2"]
    assert "A1" not in [a.action_id for a in environment.current_available_enquiries()]
    with pytest.raises(ValueError, match="already been completed"):
        environment.execute_enquiry("A1")


def test_each_c4_action_is_a_legal_first_enquiry_under_real_locality():
    for action_id, target in (("A1", "U1"), ("A2", "U1"), ("A3", "U2")):
        fixture = fixture_map()["C4"]
        coordinator = InvestigatorCycleCoordinator(deepcopy(fixture.graph), fixture.focus, available_enquiries=fixture.available_enquiries)
        observation = coordinator.observation()
        assert target in observation.local_graph.nodes
        coordinator.apply_turn({"graph_updates": [], "next_step": {"type": "request_enquiry", "reason": "This enquiry addresses the visible uncertainty.", "action_id": action_id, "target_uncertainty_id": target, "expected_information_value": "The release may change the explanation space."}})
        assert coordinator.cycle.status is CycleStatus.ENQUIRY_IN_FLIGHT


def test_trusted_ingestion_releases_evidence_and_recent_marker_is_consumed():
    fixture = fixture_map()["C1"]
    environment = Stage1Environment.for_fixture(fixture)
    coordinator = InvestigatorCycleCoordinator(deepcopy(fixture.graph), fixture.focus, available_enquiries=environment.current_available_enquiries(), max_turns_per_tenure=3)
    coordinator.apply_turn({"graph_updates": [], "next_step": {"type": "request_enquiry", "reason": "Need the source record.", "action_id": "A1", "target_uncertainty_id": "U1", "expected_information_value": "It may distinguish common preparation from independent work."}})
    release = environment.execute_enquiry("A1")
    coordinator.complete_enquiry_with_evidence("A1", release.evidence)
    coordinator.set_available_enquiries(environment.current_available_enquiries())
    assert coordinator.observation().recently_released_evidence_ids == ["E2"]
    assert "E2" in coordinator.observation().local_graph.nodes
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


def test_local_exhaustion_claim_does_not_override_trusted_material_frontier():
    fixture = fixture_map()["C1"]
    environment = Stage1Environment.for_fixture(fixture)
    coordinator = InvestigatorCycleCoordinator(deepcopy(fixture.graph), fixture.focus, available_enquiries=environment.current_available_enquiries())
    coordinator.cycle.handoff_reason = "LOCAL_EXHAUSTED"
    context = live_review_context(coordinator, environment)
    assert context.local_frontier_exhausted is False
    assert context.obvious_useful_region_remains is True


def test_non_material_action_remains_available_after_release():
    fixture = fixture_map()["C4"]
    fixture.releases["A1"].no_longer_materially_usable_action_ids = ["A2"]
    environment = Stage1Environment.for_fixture(fixture)
    environment.execute_enquiry("A1")
    # The release explicitly removes usefulness; no inference from release existence.
    assert [action.action_id for action in environment.current_available_enquiries()] == ["A2", "A3"]
    assert "A2" not in environment.materially_usable_action_ids()


def test_c3_both_valid_action_orders_have_expected_material_frontier():
    for order in (("A1", "A2"), ("A2", "A1")):
        environment = Stage1Environment.for_fixture(fixture_map()["C3"])
        first_release = environment.execute_enquiry(order[0])
        assert [node.id for node in first_release.evidence] == (["E2"] if order[0] == "A1" else ["E3"])
        if order == ("A1", "A2"):
            assert environment.materially_usable_action_ids() == ["A2"]
        else:
            assert [action.action_id for action in environment.current_available_enquiries()] == ["A1"]
            assert environment.materially_usable_action_ids() == []
        environment.execute_enquiry(order[1])
        assert environment.materially_usable_action_ids() == []


def test_c4_frontier_assessment_is_order_independent():
    for order in (("A1", "A2", "A3"), ("A3", "A1", "A2"), ("A2", "A3", "A1")):
        environment = Stage1Environment.for_fixture(fixture_map()["C4"])
        for index, action_id in enumerate(order):
            environment.execute_enquiry(action_id)
            assert environment.global_frontier_assessed() is (index == len(order) - 1)
        assert environment.materially_usable_action_ids() == []


def test_stop_evaluator_requires_exhaustion_and_passes_declared_mechanical_requirements():
    trusted_context = {"global_frontier_assessed": True, "local_frontier_exhausted": True, "available_action_ids": [], "materially_usable_action_ids": [], "obvious_useful_region_remains": False, "active_unresolved_ids": ["U1"]}
    base = {"termination": "STOP_UNRESOLVED", "completed_action_ids": ["A1"], "traces": [{"actor": "steward", "steward_decision": {"operation": "stop_unresolved"}, "steward_review_context": trusted_context, "materially_usable_action_ids_after": [], "environment_release": [{"id": "E2"}], "visible_released_evidence_ids": ["E2"]}]}
    result = evaluate_trajectory(base, TrajectoryRequirements(required_release_ids=["E2"], required_action_ids=["A1"], required_visible_evidence_ids=["E2"], require_stop_unresolved=True))
    assert result["outcome"] == "PASS"
    failed_context = {**trusted_context, "materially_usable_action_ids": ["A2"], "obvious_useful_region_remains": True}
    failed = evaluate_trajectory({**base, "traces": [{**base["traces"][0], "steward_review_context": failed_context}]}, TrajectoryRequirements(require_stop_unresolved=True))
    assert failed["outcome"] == "FAIL"


def test_stop_requires_trusted_global_assessment_from_actual_steward_step():
    def check(context):
        result = evaluate_trajectory({"termination": "STOP_UNRESOLVED", "traces": [{"actor": "steward", "steward_decision": {"operation": "stop_unresolved"}, "steward_review_context": context}]}, TrajectoryRequirements(require_stop_unresolved=True, require_trusted_exhaustion_for_stop=True))
        return result["outcome"]
    assert check({"global_frontier_assessed": False, "local_frontier_exhausted": True, "materially_usable_action_ids": [], "obvious_useful_region_remains": False}) == "FAIL"
    assert check({"global_frontier_assessed": True, "local_frontier_exhausted": True, "materially_usable_action_ids": ["A1"], "obvious_useful_region_remains": True}) == "FAIL"
    assert check({"global_frontier_assessed": True, "local_frontier_exhausted": True, "materially_usable_action_ids": [], "obvious_useful_region_remains": False}) == "PASS"


def test_forbidden_action_is_evaluated_by_execution_time_not_cumulative_state():
    def check(traces):
        return evaluate_trajectory({"termination": "STOP_UNRESOLVED", "completed_action_ids": ["A1"], "traces": traces}, TrajectoryRequirements(forbidden_actions_after_release={"E3": ["A1"]}))
    before = check([{"executed_action_id": "A1", "environment_release": []}, {"executed_action_id": None, "environment_release": [{"id": "E3"}], "materially_usable_action_ids_after": []}])
    after = check([{"executed_action_id": None, "environment_release": [{"id": "E3"}], "materially_usable_action_ids_after": []}, {"executed_action_id": "A1", "environment_release": []}])
    never = check([{"executed_action_id": None, "environment_release": [{"id": "E3"}], "materially_usable_action_ids_after": []}])
    assert before["outcome"] == never["outcome"] == "PASS"
    assert after["outcome"] == "FAIL"


def test_material_change_must_happen_after_release():
    requirements = TrajectoryRequirements(required_release_ids=["E2"], require_material_graph_change_after_release=True)
    release = {"actor": "environment", "environment_release": [{"id": "E2"}], "executed_action_id": "A1", "graph_fingerprint_before": "a", "graph_fingerprint_after": "b"}
    changed = {"actor": "investigator", "environment_release": [], "executed_action_id": None, "graph_fingerprint_before": "b", "graph_fingerprint_after": "c", "graph_delta": {}}
    unchanged = {"actor": "investigator", "environment_release": [], "executed_action_id": None, "graph_fingerprint_before": "b", "graph_fingerprint_after": "b", "graph_delta": {}}
    assert evaluate_trajectory({"termination": "STOP_UNRESOLVED", "traces": [release, changed]}, requirements)["outcome"] == "PASS"
    assert evaluate_trajectory({"termination": "STOP_UNRESOLVED", "traces": [release, unchanged]}, requirements)["outcome"] == "FAIL"
    assert evaluate_trajectory({"termination": "STOP_UNRESOLVED", "traces": [changed, release]}, requirements)["outcome"] == "FAIL"


def test_graph_delta_captures_nodes_edges_and_status_changes():
    before = {"nodes": {"H1": {"status": "active"}}, "edges": {}}
    after = {"nodes": {"H1": {"status": "archived"}, "E2": {"status": "active"}}, "edges": {"E2_SUPPORTS_H1": {}}}
    assert _graph_delta(before, after) == {"added_node_ids": ["E2"], "added_edge_ids": ["E2_SUPPORTS_H1"], "removed_edge_ids": [], "node_status_changes": [{"node_id": "H1", "before": "active", "after": "archived"}]}


def _coordinator_for_creation():
    fixture = fixture_map()["C1"]
    return GraphInvestigationCoordinator(deepcopy(fixture.graph), InvestigationFocus(node_id="H1"))


def test_creation_ids_are_allocated_and_same_turn_aliases_resolve():
    coordinator = _coordinator_for_creation()
    coordinator.apply_investigator_update({"operation": "add_proposition", "statement": "A bounded observation.", "derived_from_node_ids": ["P1"], "reason": "It is grounded in the existing proposition."})
    assert "P2" in coordinator.graph.nodes

    cycle = InvestigatorCycleCoordinator(deepcopy(fixture_map()["C1"].graph), fixture_map()["C1"].focus)
    cycle.apply_turn({"graph_updates": [{"operation": "add_proposition", "local_ref": "new_prop", "statement": "A bounded observation.", "derived_from_node_ids": ["P1"], "reason": "It is grounded in the existing proposition."}, {"operation": "add_support", "source_node_ref": "new_prop", "target_node_id": "P1", "reason": "The bounded observation is relevant."}], "next_step": {"type": "local_exhausted", "reason": "The local batch is complete."}})
    assert "P2_SUPPORTS_P1" in cycle.graph.edges


def test_same_turn_create_then_focus_succeeds_and_aliases_do_not_persist():
    fixture = fixture_map()["C1"]
    coordinator = InvestigatorCycleCoordinator(deepcopy(fixture.graph), fixture.focus, available_enquiries=fixture.available_enquiries)
    coordinator.apply_turn({"graph_updates": [{"operation": "add_proposition", "local_ref": "p", "statement": "A bounded observation.", "derived_from_node_ids": ["P1"], "reason": "It is grounded."}, {"operation": "move_focus", "focus_node_ref": "p", "reason": "The new observation is now the local focus."}], "next_step": {"type": "local_exhausted", "reason": "The local batch is complete."}})
    assert coordinator.focus.node_id == "P2"
    assert "local_ref" not in coordinator.graph.nodes["P2"].model_dump()


def test_allocator_uses_next_free_ids_and_failed_batch_does_not_consume_id():
    coordinator = _coordinator_for_creation()
    coordinator.apply_investigator_update({"operation": "add_proposition", "local_ref": "one", "statement": "p", "derived_from_node_ids": ["P1"], "reason": "grounded"})
    coordinator.apply_investigator_update({"operation": "add_proposition", "local_ref": "two", "statement": "p", "derived_from_node_ids": ["P1"], "reason": "grounded"})
    assert {"P2", "P3"} <= set(coordinator.graph.nodes)
    failed = InvestigatorCycleCoordinator(deepcopy(fixture_map()["C1"].graph), InvestigationFocus(node_id="U1"))
    with pytest.raises(Exception, match="Unknown local reference"):
        failed.apply_turn({"graph_updates": [{"operation": "add_proposition", "local_ref": "p", "statement": "p", "derived_from_node_ids": ["P1"], "reason": "grounded"}, {"operation": "add_support", "source_node_ref": "missing", "target_node_id": "H1", "reason": "bad"}], "next_step": {"type": "local_exhausted", "reason": "invalid"}})
    assert "P2" not in failed.graph.nodes
    failed.apply_turn({"graph_updates": [{"operation": "add_proposition", "local_ref": "p", "statement": "p", "derived_from_node_ids": ["P1"], "reason": "grounded"}], "next_step": {"type": "local_exhausted", "reason": "valid"}})
    assert "P2" in failed.graph.nodes


def test_hypothesis_uncertainty_allocators_skip_archived_ids():
    fixture = fixture_map()["C1"]
    graph = deepcopy(fixture.graph)
    graph.add_node(GraphNode(id="H3", node_type=GraphNodeType.HYPOTHESIS, statement="Archived hypothesis.", status=GraphStatus.ARCHIVED))
    graph.add_node(GraphNode(id="U2", node_type=GraphNodeType.UNCERTAINTY, statement="Archived uncertainty.", status=GraphStatus.ARCHIVED))
    coordinator = GraphInvestigationCoordinator(graph, InvestigationFocus(node_id="U1"))
    coordinator.apply_investigator_update({"operation": "add_hypothesis", "statement": "A new local possibility.", "reason": "It is a distinct local explanation."})
    coordinator.apply_investigator_update({"operation": "add_uncertainty", "statement": "A new unresolved question.", "target_node_id": "P1", "reason": "It identifies a remaining question."})
    assert "H4" in coordinator.graph.nodes and "U3" in coordinator.graph.nodes


def test_unknown_and_duplicate_local_aliases_fail_without_graph_mutation():
    coordinator = _coordinator_for_creation()
    with pytest.raises(ValueError, match="Unknown local reference"):
        coordinator.apply_investigator_update({"operation": "add_support", "source_node_ref": "missing", "target_node_id": "H1", "reason": "bad"})
    with pytest.raises(ValueError, match="Duplicate local reference"):
        coordinator = InvestigatorCycleCoordinator(deepcopy(fixture_map()["C1"].graph), fixture_map()["C1"].focus)
        coordinator.apply_turn({"graph_updates": [{"operation": "add_proposition", "local_ref": "p", "statement": "p", "derived_from_node_ids": ["P1"], "reason": "grounded"}, {"operation": "add_proposition", "local_ref": "p", "statement": "q", "derived_from_node_ids": ["P1"], "reason": "grounded"}], "next_step": {"type": "local_exhausted", "reason": "invalid"}})
    assert "P2" not in coordinator.graph.nodes


def test_prompt_clarifies_material_conflict_semantics():
    prompt = build_investigator_cycle_prompt(InvestigatorCycleCoordinator(deepcopy(fixture_map()["C1"].graph), fixture_map()["C1"].focus).observation())
    assert "CONFLICTS means material incompatibility" in prompt
    assert "reduces an observation's discriminating value" in prompt
    assert "Do not target an uncertainty with SUPPORTS or CONFLICTS" in prompt


def test_uncertainty_relation_endpoints_remain_strict():
    coordinator = _coordinator_for_creation()
    for operation in ("add_support", "add_conflict"):
        with pytest.raises(ValueError):
            coordinator.apply_investigator_update({"operation": operation, "source_node_id": "P1", "target_node_id": "U1", "reason": "This is not a valid uncertainty relation."})


def test_c2_tenure_retains_new_reasoning_and_connected_evidence_after_focus_move():
    fixture = fixture_map()["C2"]
    coordinator = InvestigatorCycleCoordinator(deepcopy(fixture.graph), fixture.focus, available_enquiries=fixture.available_enquiries, max_turns_per_tenure=3)
    coordinator.apply_turn({"graph_updates": [], "next_step": {"type": "request_enquiry", "action_id": "A1", "target_uncertainty_id": "U1", "expected_information_value": "It tests the named tutor explanation.", "reason": "The recorded session is the listed local check."}})
    coordinator.complete_enquiry_with_evidence("A1", fixture.releases["A1"].evidence)
    coordinator.apply_turn({"graph_updates": [{"operation": "add_proposition", "statement": "The recorded session did not contain the unfamiliar term.", "derived_from_node_ids": ["E2"], "reason": "The released record directly grounds this proposition."}, {"operation": "add_proposition", "statement": "The named tutor is not supported as the source of the term.", "derived_from_node_ids": ["E2"], "reason": "The same released record supports this narrower explanation."}, {"operation": "add_conflict", "source_node_id": "P2", "target_node_id": "H1.1", "reason": "The record is materially incompatible with the specific tutor-source hypothesis."}, {"operation": "move_focus", "focus_node_id": "U1", "reason": "The remaining uncertainty should be reconsidered."}], "next_step": {"type": "continue_local", "reason": "The retained local evidence supports another bounded step."}})
    observed_ids = set(coordinator.observation().local_graph.nodes)
    assert {"E2", "P2", "P3", "H1.1", "U1"} <= observed_ids
    assert "H2" not in observed_ids
    graph_before_observation = coordinator.graph.model_dump(mode="json")
    coordinator.observation()
    assert coordinator.graph.model_dump(mode="json") == graph_before_observation
    coordinator.apply_turn({"graph_updates": [], "next_step": {"type": "local_exhausted", "reason": "The local tenure is ready for Steward review."}})
    coordinator.apply_steward_decision({"operation": "keep_focus", "assessment": "retain", "reason": "The global review keeps the current uncertainty focus."}, coordinator.cycle.case_revision)
    assert "P2" not in coordinator.observation().local_graph.nodes


def test_runner_applies_non_stop_steward_decision_without_stop_context_argument():
    investigator = ScriptedClient(
        {"graph_updates": [], "next_step": {"type": "request_enquiry", "reason": "Need the source record.", "action_id": "A1", "target_uncertainty_id": "U1", "expected_information_value": "It tests the named tutor explanation."}},
        {"graph_updates": [], "next_step": {"type": "local_exhausted", "reason": "The local evidence step is complete."}},
    )
    steward = ScriptedClient(KeepFocusDecision(assessment="retain", reason="The current focus remains useful."))
    result = run_trajectory(fixture_map()["C2"], investigator, steward, max_steps=4)
    steward_traces = [trace for trace in result["traces"] if trace["actor"] == "steward"]
    assert steward_traces and steward_traces[0]["failure_category"] is None
    assert steward_traces[0]["steward_review_context"] is not None


def test_hidden_release_is_not_in_initial_investigator_observation():
    for fixture in all_fixtures():
        environment = Stage1Environment.for_fixture(fixture)
        coordinator = InvestigatorCycleCoordinator(deepcopy(fixture.graph), fixture.focus, available_enquiries=environment.current_available_enquiries())
        prompt = build_investigator_prompt(coordinator.observation())
        assert fixture.hidden_audit_truth not in prompt


def test_manifest_freezes_public_hidden_and_requirement_hashes_without_content_leakage():
    value = manifest()
    assert value["suite_version"] == "stage1-v2"
    assert value["fixture_count"] == 4
    assert value["hidden_environment_hash"] and value["evaluator_requirements_hash"]
    assert "hidden_audit_truth" not in value and "releases" not in value


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
