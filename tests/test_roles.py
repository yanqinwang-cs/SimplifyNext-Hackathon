import pytest
from pydantic import TypeAdapter, ValidationError

from investigator.graph import CaseGraph, EdgeRelation, EdgeStrength, GraphEdge, GraphNode, GraphNodeType, GraphStatus
from investigator.roles import GraphInvestigationCoordinator, InvestigationFocus, InvestigatorOperation, InvestigatorUpdate, StewardDecision, StewardReviewContext
from investigator.steward.features import direct_evidence_profile, region_health, tunnel_vision_indicators


def graph() -> CaseGraph:
    nodes = {identifier: GraphNode(id=identifier, node_type=kind, statement=identifier) for identifier, kind in {
        "E1": GraphNodeType.EVIDENCE, "P1": GraphNodeType.PROPOSITION, "P2": GraphNodeType.PROPOSITION,
        "H1": GraphNodeType.HYPOTHESIS, "H1.1": GraphNodeType.HYPOTHESIS, "H2": GraphNodeType.HYPOTHESIS,
        "U1": GraphNodeType.UNCERTAINTY, "U2": GraphNodeType.UNCERTAINTY,
    }.items()}
    def edge(source: str, relation: EdgeRelation, target: str) -> GraphEdge:
        return GraphEdge(id=f"{source}_{relation.value.upper()}_{target}", source_id=source, target_id=target, relation=relation)
    edges = {edge.id: edge for edge in [
        edge("H1.1", EdgeRelation.SPECIALIZES, "H1"), edge("H1", EdgeRelation.DEPENDS_ON, "P1"),
        edge("H2", EdgeRelation.DEPENDS_ON, "P1"), GraphEdge(id="E1_SUPPORTS_P1", source_id="E1", target_id="P1", relation=EdgeRelation.SUPPORTS, strength=EdgeStrength.DIRECT),
        edge("U1", EdgeRelation.TARGETS, "H1"), edge("U2", EdgeRelation.TARGETS, "H2"),
    ]}
    return CaseGraph(case_id="synthetic", nodes=nodes, edges=edges)


def decision(payload: dict):
    return TypeAdapter(StewardDecision).validate_python(payload)


def exhausted_stop(ids=("U1",)) -> dict:
    return {"assessment": "frontier exhausted", "operation": "stop_unresolved", "reason": "No useful frontier remains", "reopening_conditions": "new evidence", "important_unresolved_ids": list(ids)}


def trusted_context(**overrides) -> StewardReviewContext:
    values = {"global_frontier_assessed": True, "local_frontier_exhausted": True, "active_unresolved_ids": ["U1"]}
    values.update(overrides)
    return StewardReviewContext(**values)


def test_investigator_operations_are_narrow_and_local() -> None:
    coordinator = GraphInvestigationCoordinator(graph(), InvestigationFocus(node_id="H1"))
    with pytest.raises(ValidationError):
        InvestigatorUpdate(operation="add_node", node=GraphNode(id="P3", node_type="proposition", statement="x"))
    with pytest.raises(ValidationError):
        InvestigatorUpdate(operation="add_proposition", node=GraphNode(id="E3", node_type="evidence", statement="x"))
    for identifier, kind in [("P3", GraphNodeType.PROPOSITION), ("H3", GraphNodeType.HYPOTHESIS), ("U3", GraphNodeType.UNCERTAINTY)]:
        coordinator.apply_investigator_update(InvestigatorUpdate(operation=f"add_{kind.value}", node=GraphNode(id=identifier, node_type=kind, statement=identifier)))
    with pytest.raises(ValueError, match="outside"):
        coordinator.apply_investigator_update(InvestigatorUpdate(operation="move_focus", focus_node_id="H2"))


def test_investigator_edge_relations_and_specialization_are_explicit() -> None:
    with pytest.raises(ValidationError):
        InvestigatorUpdate(operation="add_support", edge=GraphEdge(id="H1_DEPENDS_ON_P1", source_id="H1", target_id="P1", relation=EdgeRelation.DEPENDS_ON))
    coordinator = GraphInvestigationCoordinator(graph(), InvestigationFocus(node_id="H1"))
    coordinator.apply_investigator_update(InvestigatorUpdate(operation="add_hypothesis", node=GraphNode(id="H1.2", node_type="hypothesis", statement="child")))
    coordinator.apply_investigator_update(InvestigatorUpdate(operation="add_specialization", edge=GraphEdge(id="H1.2_SPECIALIZES_H1", source_id="H1.2", target_id="H1", relation=EdgeRelation.SPECIALIZES)))
    assert coordinator.graph.ancestors("H1.2")[0].id == "H1"


def test_steward_branches_forbid_irrelevant_and_tool_fields() -> None:
    valid = [
        {"assessment": "same", "reason": "r", "operation": "keep_focus"},
        {"assessment": "shift", "reason": "r", "operation": "shift_focus", "destination_node_id": "P1"},
        {"assessment": "generalize", "reason": "r", "operation": "generalize", "target_node_id": "H1.1"},
        {"assessment": "archive", "reason": "r", "operation": "archive", "target_node_id": "P2"},
        {"assessment": "reactivate", "reason": "r", "operation": "reactivate", "target_node_id": "P2"},
        exhausted_stop(),
    ]
    for payload in valid:
        decision(payload)
        with pytest.raises(ValidationError):
            decision({**payload, "tool_id": "T1"})
    with pytest.raises(ValidationError):
        decision({"assessment": "x", "reason": "r", "operation": "keep_focus", "destination_node_id": "P1"})
    with pytest.raises(ValidationError):
        decision({**exhausted_stop(), "review_context": {}})
    coordinator = GraphInvestigationCoordinator(graph(), InvestigationFocus(node_id="H1"))
    with pytest.raises(ValueError):
        coordinator.review_with_steward(decision({"assessment": "same", "reason": "r", "operation": "keep_focus"}), review_context=trusted_context())


def test_archive_current_focus_requires_explicit_active_redirect() -> None:
    coordinator = GraphInvestigationCoordinator(graph(), InvestigationFocus(node_id="H1"))
    missing_destination = decision({"assessment": "x", "reason": "r", "operation": "archive", "target_node_id": "H1"})
    with pytest.raises(ValueError):
        coordinator.review_with_steward(missing_destination)
    with pytest.raises(ValueError):
        coordinator.review_with_steward(decision({"assessment": "x", "reason": "r", "operation": "archive", "target_node_id": "H1", "destination_node_id": "missing"}))
    coordinator.graph.archive_node("P1")
    with pytest.raises(ValueError):
        coordinator.review_with_steward(decision({"assessment": "x", "reason": "r", "operation": "archive", "target_node_id": "H1", "destination_node_id": "P1"}))
    coordinator.review_with_steward(decision({"assessment": "x", "reason": "r", "operation": "archive", "target_node_id": "H1", "destination_node_id": "P2"}))
    assert coordinator.focus.node_id == "P2" and coordinator.graph.nodes["H1"].status is GraphStatus.ARCHIVED
    coordinator.review_with_steward(decision({"assessment": "x", "reason": "r", "operation": "archive", "target_node_id": "H2"}))
    assert coordinator.focus.node_id == "P2"


def test_stop_unresolved_requires_structural_exhaustion() -> None:
    coordinator = GraphInvestigationCoordinator(graph(), InvestigationFocus(node_id="H1"))
    base = exhausted_stop()
    for context in [trusted_context(global_frontier_assessed=False), trusted_context(neglected_candidate_node_ids=["H2"]), trusted_context(obvious_useful_region_remains=True), trusted_context(available_action_ids=["A1"], materially_usable_action_ids=["A1"]), trusted_context(local_frontier_exhausted=False)]:
        with pytest.raises((ValidationError, ValueError)):
            coordinator.review_with_steward(decision(base), review_context=context)
    with pytest.raises(ValueError):
        coordinator.review_with_steward(decision(base))
    coordinator.review_with_steward(decision(base), review_context=trusted_context())
    assert coordinator.stopped
    with pytest.raises(ValueError):
        coordinator.review_with_steward(decision({"assessment": "x", "reason": "r", "operation": "archive", "target_node_id": "P1"}))


def test_stop_rejects_invalid_uncertainties_and_features_are_structural() -> None:
    coordinator = GraphInvestigationCoordinator(graph(), InvestigationFocus(node_id="H1"))
    for identifier in ["missing", "P1"]:
        with pytest.raises(ValueError):
            coordinator.review_with_steward(decision(exhausted_stop((identifier,))), review_context=trusted_context(active_unresolved_ids=[identifier]))
    coordinator.graph.archive_node("U1")
    with pytest.raises(ValueError):
        coordinator.review_with_steward(decision(exhausted_stop()), review_context=trusted_context())
    coordinator = GraphInvestigationCoordinator(graph(), InvestigationFocus(node_id="H1"))
    with pytest.raises(ValueError):
        coordinator.review_with_steward(decision(exhausted_stop()), review_context=trusted_context(active_unresolved_ids=[]))
    focus = InvestigationFocus(node_id="H1").moved_to("H1.1", ["H1.1"]).moved_to("H2", ["H2"]).moved_to("H1", ["H1"])
    indicators = tunnel_vision_indicators(graph(), focus)
    assert indicators.same_root_steps == 1 and indicators.current_specialization_depth == 0
    assert region_health(graph(), InvestigationFocus(node_id="P1")).shared_dependency_count == 1
    assert region_health(graph(), InvestigationFocus(node_id="P2")).shared_dependency_count == 0
    direct = direct_evidence_profile(graph(), {"P1"})
    assert direct.direct_count == 1 and direct.unique_evidence_ids == ["E1"]
    assert direct_evidence_profile(graph(), {"H1"}).direct_count == 0
    conflict_graph = graph()
    conflict_graph.add_node(GraphNode(id="E2", node_type="evidence", statement="E2", metadata={"source_id": "s2"}))
    conflict_graph.add_edge(GraphEdge(id="E2_CONFLICTS_P1", source_id="E2", target_id="P1", relation=EdgeRelation.CONFLICTS, strength=EdgeStrength.DIRECT))
    conflict_graph.add_edge(GraphEdge(id="P1_CONFLICTS_H1", source_id="P1", target_id="H1", relation=EdgeRelation.CONFLICTS, strength=EdgeStrength.DIRECT))
    conflict = direct_evidence_profile(conflict_graph, {"P1", "H1"}, EdgeRelation.CONFLICTS)
    assert conflict.direct_count == 1 and conflict.unique_evidence_ids == ["E2"]
