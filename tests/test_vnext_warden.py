from copy import deepcopy

import pytest

from investigator.graph import CaseGraph, EdgeRelation, GraphEdge, GraphNode, GraphNodeType, make_edge_id
from investigator.models.source import Source, SourceType
from investigator.vnext import GraphWarden, InvestigatorProposal, WardenValidationError


def source_registry() -> dict[str, Source]:
    return {"S1": Source(id="S1", name="record.txt", source_type=SourceType.DOCUMENT, content="Source body.")}


def graph() -> CaseGraph:
    return CaseGraph(
        case_id="case-01",
        nodes={
            "E1": GraphNode(id="E1", node_type=GraphNodeType.EVIDENCE, statement="Existing observation."),
            "P1": GraphNode(id="P1", node_type=GraphNodeType.PROPOSITION, statement="Existing proposition."),
            "H1": GraphNode(id="H1", node_type=GraphNodeType.HYPOTHESIS, statement="Existing hypothesis."),
            "H2": GraphNode(id="H2", node_type=GraphNodeType.HYPOTHESIS, statement="Second existing hypothesis."),
            "U1": GraphNode(id="U1", node_type=GraphNodeType.UNCERTAINTY, statement="Existing uncertainty."),
        },
        edges={
            make_edge_id("E1", EdgeRelation.SUPPORTS, "P1"): GraphEdge(id=make_edge_id("E1", EdgeRelation.SUPPORTS, "P1"), source_id="E1", target_id="P1", relation=EdgeRelation.SUPPORTS),
        },
    )


def proposal(updates: list[dict]) -> InvestigatorProposal:
    return InvestigatorProposal.model_validate({"graph_updates": updates})


def test_empty_proposal_is_a_no_op() -> None:
    canonical = graph()
    result = GraphWarden(canonical, source_registry()).apply(proposal([]))
    assert result.created_node_ids == []
    assert result.graph.model_dump(mode="json") == canonical.model_dump(mode="json")


def test_valid_evidence_requires_registered_source_and_preserves_provenance() -> None:
    result = GraphWarden(graph(), source_registry()).apply(proposal([
        {"operation": "add_evidence", "statement": "Source says X.", "source_ids": ["S1"], "reason": "Record the observation."},
    ]))
    created = result.graph.nodes[result.created_node_ids[0]]
    assert created.node_type is GraphNodeType.EVIDENCE
    assert created.metadata["source_ids"] == ["S1"]


def test_unknown_source_rejects_without_mutating_canonical_graph() -> None:
    canonical = graph()
    before = canonical.model_dump(mode="json")
    with pytest.raises(WardenValidationError, match="unknown raw source ID"):
        GraphWarden(canonical, source_registry()).apply(proposal([
            {"operation": "add_evidence", "statement": "Invented provenance.", "source_ids": ["S999"], "reason": "This must fail."},
        ]))
    assert canonical.model_dump(mode="json") == before


def test_valid_multi_operation_batch_resolves_local_ref() -> None:
    result = GraphWarden(graph(), source_registry()).apply(proposal([
        {"operation": "add_evidence", "local_ref": "obs", "statement": "Source says X.", "source_ids": ["S1"], "reason": "Record the observation."},
        {"operation": "add_proposition", "local_ref": "inference", "statement": "X may matter.", "derived_from_node_ids": ["obs"], "reason": "State the bounded inference."},
        {"operation": "add_uncertainty", "statement": "Whether X is complete.", "target_node_id": "inference", "reason": "Preserve the unresolved question."},
        {"operation": "add_support", "source_node_id": "obs", "target_node_id": "inference", "reason": "The observation supports the inference."},
    ]))
    evidence_id = result.local_ref_resolution["obs"]
    proposition_id = result.local_ref_resolution["inference"]
    assert evidence_id.startswith("node_") and proposition_id.startswith("node_")
    assert "obs" not in result.graph.nodes and "inference" not in result.graph.nodes
    assert result.graph.edges[f"{proposition_id}_DERIVED_FROM_{evidence_id}"]


def test_invalid_mid_batch_reference_rolls_back_all_updates() -> None:
    canonical = graph()
    before = canonical.model_dump(mode="json")
    with pytest.raises(WardenValidationError):
        GraphWarden(canonical, source_registry()).apply(proposal([
            {"operation": "add_hypothesis", "statement": "New explanation.", "reason": "Add a candidate."},
            {"operation": "add_proposition", "statement": "Unsupported inference.", "derived_from_node_ids": ["not_real"], "reason": "This must fail."},
            {"operation": "add_uncertainty", "statement": "Another question.", "target_node_id": "H1", "reason": "This must not commit."},
        ]))
    assert canonical.model_dump(mode="json") == before


def test_invalid_relation_endpoint_rolls_back() -> None:
    canonical = graph()
    before = canonical.model_dump(mode="json")
    with pytest.raises(WardenValidationError):
        GraphWarden(canonical, source_registry()).apply(proposal([
            {"operation": "add_support", "source_node_id": "P1", "target_node_id": "P1", "reason": "Illegal self relation."},
        ]))
    assert canonical.model_dump(mode="json") == before


def test_specialization_cycle_rolls_back() -> None:
    canonical = graph()
    canonical.add_edge(GraphEdge(id=make_edge_id("H1", EdgeRelation.SPECIALIZES, "H2"), source_id="H1", target_id="H2", relation=EdgeRelation.SPECIALIZES))
    before = canonical.model_dump(mode="json")
    with pytest.raises(WardenValidationError):
        GraphWarden(canonical, source_registry()).apply(proposal([
            {"operation": "add_specialization", "child_hypothesis_id": "H2", "parent_hypothesis_id": "H1", "reason": "This would create a cycle."},
        ]))
    assert canonical.model_dump(mode="json") == before


def test_warden_preserves_investigator_statement_and_proposal() -> None:
    exact = "The record supports only this carefully bounded statement."
    submitted = proposal([
        {"operation": "add_proposition", "statement": exact, "derived_from_node_ids": ["E1"], "reason": "Preserve the exact inference."},
    ])
    submitted_before = submitted.model_dump(mode="json")
    result = GraphWarden(graph(), source_registry()).apply(submitted)
    proposition = next(node for node in result.graph.nodes.values() if node.node_type is GraphNodeType.PROPOSITION and node.statement == exact)
    assert proposition.statement == exact
    assert submitted.model_dump(mode="json") == submitted_before


def test_graph_changes_only_when_applied_through_warden() -> None:
    canonical = graph()
    proposal_data = proposal([
        {"operation": "add_hypothesis", "statement": "A proposed explanation.", "reason": "Preserve an alternative."},
    ])
    before = deepcopy(canonical).model_dump(mode="json")
    assert canonical.model_dump(mode="json") == before
    result = GraphWarden(canonical, source_registry()).apply(proposal_data)
    assert result.graph.model_dump(mode="json") != before
    assert canonical.model_dump(mode="json") == before


def test_warden_requires_typed_proposal() -> None:
    with pytest.raises(TypeError, match="InvestigatorProposal"):
        GraphWarden(graph(), source_registry()).apply({"graph_updates": []})  # type: ignore[arg-type]
