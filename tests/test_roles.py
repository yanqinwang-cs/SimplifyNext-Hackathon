import pytest
from pydantic import TypeAdapter, ValidationError

from investigator.graph import CaseGraph, EdgeRelation, EdgeStrength, GraphEdge, GraphNode, GraphNodeType, GraphStatus
from investigator.cycle import InvestigatorCycleCoordinator
from investigator.roles import AddHypothesisCommand, AddSpecializationCommand, GraphInvestigationCoordinator, InvestigationFocus, INVESTIGATOR_UPDATE_ADAPTER, InvestigatorUpdateResponse, StewardDecision, StewardReviewContext
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
    return {"assessment": "frontier exhausted", "operation": "handoff_to_human", "reason": "No useful frontier remains", "reopening_conditions": "new evidence", "important_unresolved_ids": list(ids), "handoff_summary": "Return the case to a human decision-maker."}


def trusted_context(**overrides) -> StewardReviewContext:
    values = {"global_frontier_assessed": True, "local_frontier_exhausted": True, "active_unresolved_ids": ["U1"]}
    values.update(overrides)
    return StewardReviewContext(**values)


def test_investigator_operations_are_narrow_and_local() -> None:
    coordinator = GraphInvestigationCoordinator(graph(), InvestigationFocus(node_id="H1"))
    with pytest.raises(ValidationError):
        INVESTIGATOR_UPDATE_ADAPTER.validate_python({"operation": "add_node", "node": GraphNode(id="P3", node_type="proposition", statement="x")})
    with pytest.raises(ValidationError):
        INVESTIGATOR_UPDATE_ADAPTER.validate_python({"operation": "add_proposition", "node": GraphNode(id="E3", node_type="evidence", statement="x")})
    for identifier, kind in [("P3", GraphNodeType.PROPOSITION), ("H3", GraphNodeType.HYPOTHESIS), ("U3", GraphNodeType.UNCERTAINTY)]:
        if kind is GraphNodeType.PROPOSITION:
            update = {"operation": "add_proposition", "node_id": identifier, "statement": identifier, "derived_from_node_ids": ["P1"], "reason": "local proposition"}
        elif kind is GraphNodeType.HYPOTHESIS:
            update = AddHypothesisCommand(node_id=identifier, statement=identifier, reason="local hypothesis")
        else:
            update = {"operation": "add_uncertainty", "node_id": identifier, "statement": identifier, "target_node_id": "H1", "reason": "local uncertainty"}
        coordinator.apply_investigator_update(update)
    with pytest.raises(ValueError, match="outside"):
        coordinator.apply_investigator_update({"operation": "move_focus", "focus_node_id": "H2", "reason": "move focus"})


def test_investigator_edge_relations_and_specialization_are_explicit() -> None:
    with pytest.raises(ValueError):
        GraphInvestigationCoordinator(graph(), InvestigationFocus(node_id="H1")).apply_investigator_update({"operation": "add_support", "source_node_id": "H1", "target_node_id": "P1", "strength": "direct", "reason": "support"})
    coordinator = GraphInvestigationCoordinator(graph(), InvestigationFocus(node_id="H1"))
    coordinator.apply_investigator_update(AddHypothesisCommand(node_id="H1.2", statement="child", reason="specific child"))
    coordinator.apply_investigator_update(AddSpecializationCommand(child_hypothesis_id="H1.2", parent_hypothesis_id="H1", reason="specific child"))
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


def test_investigator_union_exposes_only_eight_narrow_operations() -> None:
    schema = InvestigatorUpdateResponse.model_json_schema()
    serialized = str(schema)
    for operation in ("add_proposition", "add_hypothesis", "add_uncertainty", "add_support", "add_conflict", "add_derivation", "add_specialization", "move_focus"):
        assert operation in serialized
    for forbidden in ("node_type", "status", "metadata", "relation", "edge_id", "edge_status"):
        assert forbidden not in serialized
    for operation in ("add_node", "add_evidence", "add_dependency", "add_depends_on"):
        with pytest.raises(ValidationError):
            INVESTIGATOR_UPDATE_ADAPTER.validate_python({"operation": operation, "reason": "invalid"})


def test_investigator_compound_mutations_are_atomic_and_directional() -> None:
    source_graph = graph()
    source_graph.add_node(GraphNode(id="E2", node_type=GraphNodeType.EVIDENCE, statement="E2"))
    source_graph.add_edge(GraphEdge(id="E2_SUPPORTS_P1", source_id="E2", target_id="P1", relation=EdgeRelation.SUPPORTS))
    coordinator = GraphInvestigationCoordinator(source_graph, InvestigationFocus(node_id="P1"))
    coordinator.apply_investigator_update({"operation": "add_proposition", "node_id": "P3", "statement": "shared observation", "derived_from_node_ids": ["E1", "E2"], "reason": "two-source observation"})
    assert coordinator.graph.nodes["E1"].node_type is GraphNodeType.EVIDENCE
    assert coordinator.graph.nodes["E2"].node_type is GraphNodeType.EVIDENCE
    assert coordinator.graph.nodes["P3"].node_type is GraphNodeType.PROPOSITION
    assert {edge.id for edge in coordinator.graph.outgoing("P3", EdgeRelation.DERIVED_FROM)} == {"P3_DERIVED_FROM_E1", "P3_DERIVED_FROM_E2"}
    coordinator.apply_investigator_update({"operation": "add_support", "source_node_id": "P3", "target_node_id": "H1", "strength": "direct", "reason": "shared observation supports a hypothesis"})
    assert "P3_SUPPORTS_H1" in coordinator.graph.edges

    before = set(coordinator.graph.nodes)
    with pytest.raises(ValueError):
        coordinator.apply_investigator_update({"operation": "add_proposition", "node_id": "P4", "statement": "partial", "derived_from_node_ids": ["E1", "P999"], "reason": "invalid source"})
    assert set(coordinator.graph.nodes) == before and "P4" not in coordinator.graph.nodes


def test_uncertainty_targets_active_local_proposition_or_hypothesis() -> None:
    coordinator = GraphInvestigationCoordinator(graph(), InvestigationFocus(node_id="H1"))
    coordinator.apply_investigator_update({"operation": "add_uncertainty", "node_id": "U3", "statement": "open question", "target_node_id": "H1", "reason": "it matters"})
    edge = coordinator.graph.get_edge("U3_TARGETS_H1")
    assert edge.source_id == "U3" and edge.target_id == "H1" and edge.relation is EdgeRelation.TARGETS
    with pytest.raises(ValueError):
        coordinator.apply_investigator_update({"operation": "add_uncertainty", "node_id": "U4", "statement": "bad target", "target_node_id": "E1", "reason": "invalid target"})


def test_all_eight_investigator_branches_validate_without_graph_metadata_fields() -> None:
    payloads = [
        {"operation": "add_proposition", "node_id": "P3", "statement": "p", "derived_from_node_ids": ["E1"], "reason": "r"},
        {"operation": "add_hypothesis", "node_id": "H3", "statement": "h", "reason": "r"},
        {"operation": "add_uncertainty", "node_id": "U3", "statement": "u", "target_node_id": "H1", "reason": "r"},
        {"operation": "add_support", "source_node_id": "E1", "target_node_id": "H1", "strength": "direct", "reason": "r"},
        {"operation": "add_conflict", "source_node_id": "E1", "target_node_id": "H1", "strength": None, "reason": "r"},
        {"operation": "add_derivation", "derived_proposition_id": "P1", "source_node_id": "E1", "reason": "r"},
        {"operation": "add_specialization", "child_hypothesis_id": "H1.1", "parent_hypothesis_id": "H1", "reason": "r"},
        {"operation": "move_focus", "focus_node_id": "P1", "reason": "r"},
    ]
    for payload in payloads:
        parsed = INVESTIGATOR_UPDATE_ADAPTER.validate_python(payload)
        assert parsed.operation == payload["operation"]
        for forbidden in ("node_type", "status", "metadata", "relation", "edge_id", "edge_status"):
            with pytest.raises(ValidationError):
                INVESTIGATOR_UPDATE_ADAPTER.validate_python({**payload, forbidden: "injected"})


def test_investigator_ids_and_reasons_are_strict() -> None:
    base = {"operation": "add_hypothesis", "node_id": "H3", "statement": "h", "reason": "r"}
    for value in ("P3", "U3", "H3:U1"):
        with pytest.raises(ValidationError):
            INVESTIGATOR_UPDATE_ADAPTER.validate_python({**base, "node_id": value})
    for reason in ("", "   ", "REPLACE_WITH_REASON"):
        with pytest.raises(ValidationError):
            INVESTIGATOR_UPDATE_ADAPTER.validate_python({**base, "reason": reason})
    with pytest.raises(ValidationError):
        INVESTIGATOR_UPDATE_ADAPTER.validate_python({"operation": "add_proposition", "node_id": "P3", "statement": "p", "derived_from_node_ids": ["E1", "E1"], "reason": "r"})


def test_uncertainty_ids_are_global_and_targets_are_explicit() -> None:
    for target in ("E1", "P1", "H1"):
        update = INVESTIGATOR_UPDATE_ADAPTER.validate_python({"operation": "add_uncertainty", "node_id": "U3", "statement": "An unresolved question.", "target_node_id": target, "reason": "The target makes the subject explicit."})
        assert update.target_node_id == target
    for identifier in ("H1:U1", "P1:U1", "E1:U1"):
        with pytest.raises(ValidationError):
            INVESTIGATOR_UPDATE_ADAPTER.validate_python({"operation": "add_uncertainty", "node_id": identifier, "statement": "q", "target_node_id": "H1", "reason": "r"})
    with pytest.raises(ValidationError):
        INVESTIGATOR_UPDATE_ADAPTER.validate_python({"operation": "add_uncertainty", "node_id": "U4", "statement": "q", "target_node_id": "U1", "reason": "r"})


def test_uncertainty_target_evidence_is_created_atomically() -> None:
    from experiments.investigator_screen.fixtures import all_fixtures
    fixture = all_fixtures()[0]
    coordinator = InvestigatorCycleCoordinator(fixture.observation.local_graph, fixture.observation.current_focus)
    coordinator.apply_turn({"graph_updates": [{"operation": "add_uncertainty", "node_id": "U2", "statement": "Whether E1 needs clarification.", "target_node_id": "E1", "reason": "The evidence leaves a bounded question."}], "next_step": {"type": "local_exhausted", "reason": "No further local work remains."}})
    assert coordinator.graph.edges["U2_TARGETS_E1"].relation.value == "targets"
