import json

import pytest
from pydantic import TypeAdapter

from experiments.stage2_long_case.fixture import CASE_ID, HIDDEN, fresh_fixtures, run_fixture
from experiments.stage2_long_case.prompt import build_prompt, build_steward_prompt
from experiments.stage2_long_case.runner import MAX_MODEL_CALLS, MAX_STEPS, manifest, run_trajectory
from investigator.cycle import InvestigatorCycleCoordinator, InvestigatorTurnResponse, LocalExhausted
from investigator.graph import CaseGraph, EdgeRelation, GraphEdge, GraphNode, GraphNodeType, make_edge_id
from investigator.roles.focus import InvestigationFocus, active_reasoning_view
from investigator.llm import ModelCallMetadata, ModelCallResult
from investigator.roles.coordinator import GraphInvestigationCoordinator
from investigator.roles.investigator import AddEvidenceCommand, AddPropositionCommand
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
    assert all(item.source_id.startswith("S") for item in fixture.evidence)
    assert set(fixture.graph.nodes) == {"U1"}
    assert not fixture.graph.edges
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
    assert len(value["public_source_id_to_filename"]) == 19
    assert set(value["hidden_hashes"]) == {"ground_truth.md", "fixture_audit.md", "action_release_map.md"}
    assert value["hidden_files_exposed_to_models"] is False


def test_add_evidence_is_source_grounded_and_independent_of_graph_locality():
    fixture = fresh_fixtures()[0]
    coordinator = InvestigatorCycleCoordinator(fixture.graph, fixture.focus, source_registry=fixture.source_registry)
    coordinator.cycle.tenure_turn_count = 0
    coordinator._new_nodes = set()
    coordinator.apply_turn({"graph_updates": [{"operation": "add_evidence", "local_ref": "eTimeline", "statement": "The timeline records three assessment-time event clusters.", "source_ids": ["S5"], "reason": "This atomic observation is directly stated by the timeline source."}], "next_step": {"type": "local_exhausted", "reason": "The source observation is ready for review."}})
    node = coordinator.graph.nodes["E1"]
    assert node.node_type is GraphNodeType.EVIDENCE
    assert node.metadata == {"source_ids": ["S5"], "source_filenames": ["deterministic_timeline.md"], "origin": "model_extracted"}
    assert node.statement != fixture.source_registry.get("S5").content


def test_add_evidence_rejects_unknown_or_hidden_sources_atomically():
    fixture = fresh_fixtures()[0]
    coordinator = InvestigatorCycleCoordinator(fixture.graph, fixture.focus, source_registry=fixture.source_registry)
    for source_id in ("S99", "ground_truth"):
        with pytest.raises(ValueError, match="source"):
            coordinator.apply_turn({"graph_updates": [{"operation": "add_evidence", "statement": "An observation.", "source_ids": [source_id], "reason": "Ground this observation."}], "next_step": {"type": "local_exhausted", "reason": "Stop."}})
    assert set(coordinator.graph.nodes) == {"U1"}


def test_same_turn_evidence_local_ref_and_atomic_rollback():
    fixture = fresh_fixtures()[0]
    graph_coordinator = GraphInvestigationCoordinator(fixture.graph, fixture.focus, fixture.source_registry)
    graph_coordinator.apply_investigator_update(AddEvidenceCommand(local_ref="e1", statement="A source records a session.", source_ids=["S1"], reason="Direct observation."), aliases={})
    aliases = {"e1": "E1"}
    graph_coordinator.apply_investigator_update(AddPropositionCommand(local_ref="p1", statement="The record contains a session.", derived_from_node_refs=["e1"], reason="Interpret the observation."), aliases=aliases)
    assert set(graph_coordinator.graph.nodes) == {"U1", "E1", "P1"}
    with pytest.raises(ValueError):
        graph_coordinator.apply_investigator_update({"operation": "add_evidence", "local_ref": "bad", "statement": "Bad.", "source_ids": ["S99"], "reason": "Invalid source."}, aliases={})
    assert "E2" not in graph_coordinator.graph.nodes


def test_source_registry_survives_graph_archive_and_can_be_reread():
    fixture = fresh_fixtures()[0]
    coordinator = InvestigatorCycleCoordinator(fixture.graph, fixture.focus, source_registry=fixture.source_registry)
    coordinator.apply_turn({"graph_updates": [{"operation": "add_evidence", "statement": "The record contains one observation.", "source_ids": ["S1"], "reason": "Direct observation."}], "next_step": {"type": "local_exhausted", "reason": "Review."}})
    original = fixture.source_registry.get("S1").content
    coordinator.graph.archive_node("E1", "No longer locally active.")
    assert fixture.source_registry.get("S1").content == original
    reread = GraphInvestigationCoordinator(coordinator.graph, coordinator.focus, fixture.source_registry)
    reread.apply_investigator_update({"operation": "add_evidence", "statement": "The same source records a distinct second observation.", "source_ids": ["S1"], "reason": "The source contains a materially different observation."})
    assert reread.graph.nodes["E2"].metadata["source_ids"] == ["S1"]


def test_active_reasoning_view_keeps_ancestry_and_bounded_competitor_but_not_distant_branch():
    nodes = {identifier: GraphNode(id=identifier, node_type=node_type, statement=identifier) for identifier, node_type in {"E1": GraphNodeType.EVIDENCE, "E2": GraphNodeType.EVIDENCE, "P1": GraphNodeType.PROPOSITION, "H1": GraphNodeType.HYPOTHESIS, "H2": GraphNodeType.HYPOTHESIS, "U1": GraphNodeType.UNCERTAINTY, "E20": GraphNodeType.EVIDENCE, "P20": GraphNodeType.PROPOSITION, "H20": GraphNodeType.HYPOTHESIS}.items()}
    def edge(source, relation, target):
        identifier = make_edge_id(source, relation, target)
        return identifier, GraphEdge(id=identifier, source_id=source, target_id=target, relation=relation)
    edges = dict([edge("U1", EdgeRelation.TARGETS, "H1"), edge("P1", EdgeRelation.SUPPORTS, "H1"), edge("P1", EdgeRelation.SUPPORTS, "H2"), edge("P1", EdgeRelation.DERIVED_FROM, "E1"), edge("P1", EdgeRelation.DERIVED_FROM, "E2"), edge("P20", EdgeRelation.SUPPORTS, "H20"), edge("P20", EdgeRelation.DERIVED_FROM, "E20")])
    graph = CaseGraph(case_id="branch", nodes=nodes, edges=edges)
    view = active_reasoning_view(graph, InvestigationFocus(node_id="U1"))
    assert {"U1", "H1", "P1", "E1", "E2", "H2"} <= set(view.nodes)
    assert not {"E20", "P20", "H20"} & set(view.nodes)


def test_active_reasoning_view_cap_and_tenure_retention_are_deterministic():
    nodes = {"U1": GraphNode(id="U1", node_type=GraphNodeType.UNCERTAINTY, statement="focus")}
    graph = CaseGraph(case_id="cap", nodes=nodes)
    view = active_reasoning_view(graph, InvestigationFocus(node_id="U1"), tenure_node_ids={"E99"}, max_nodes=1)
    assert set(view.nodes) == {"U1"}
