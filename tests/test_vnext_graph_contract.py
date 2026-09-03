import pytest

from investigator.graph import CaseGraph, EdgeRelation, GraphNode, GraphNodeType, GraphStatus, OperationSpecRegistry
from investigator.models.source import Source, SourceType
from investigator.vnext import InvestigatorProposal
from investigator.vnext.warden import GraphWarden, WardenValidationError


def _source() -> dict[str, Source]:
    return {"S1": Source(id="S1", name="record", source_type=SourceType.DOCUMENT, content="record")}


def _graph() -> CaseGraph:
    return CaseGraph(case_id="case-01", nodes={"E1": GraphNode(id="E1", node_type=GraphNodeType.EVIDENCE, statement="evidence"), "P1": GraphNode(id="P1", node_type=GraphNodeType.PROPOSITION, statement="proposition"), "H1": GraphNode(id="H1", node_type=GraphNodeType.HYPOTHESIS, statement="hypothesis")}, edges={})


def test_supported_operation_reference_rules_are_the_single_contract() -> None:
    expected = {
        "add_proposition": {"derived_from_node_ids": {"evidence", "proposition"}},
        "add_support": {"source_node_id": {"evidence", "proposition"}, "target_node_id": {"proposition", "hypothesis"}},
        "add_conflict": {"source_node_id": {"evidence", "proposition"}, "target_node_id": {"proposition", "hypothesis"}},
        "add_derivation": {"derived_proposition_id": {"proposition"}, "source_node_id": {"evidence", "proposition"}},
        "add_uncertainty": {"target_node_id": {"evidence", "proposition", "hypothesis"}},
        "add_specialization": {"child_hypothesis_id": {"hypothesis"}, "parent_hypothesis_id": {"hypothesis"}},
    }
    for operation, fields in expected.items():
        contract = OperationSpecRegistry.contract(operation)
        assert {item.field for item in contract.references} == set(fields)
        for field, types in fields.items():
            assert {item.value for item in contract.reference(field).allowed_types} == types


def test_same_turn_local_refs_are_type_aware_and_use_exact_contract() -> None:
    proposal = InvestigatorProposal.model_validate({
        "graph_updates": [
            {"operation": "add_evidence", "local_ref": "e1", "statement": "e", "source_ids": ["S1"], "reason": "record"},
            {"operation": "add_hypothesis", "local_ref": "h1", "statement": "h", "reason": "consider"},
            {"operation": "add_proposition", "local_ref": "p1", "statement": "valid", "derived_from_node_ids": ["e1"], "reason": "derive"},
            {"operation": "add_proposition", "local_ref": "p2", "statement": "invalid", "derived_from_node_ids": ["h1"], "reason": "derive"},
        ]
    })
    with pytest.raises(WardenValidationError) as caught:
        GraphWarden(CaseGraph(case_id="case-01", nodes={"S0": GraphNode(id="S0", node_type=GraphNodeType.SOURCE, statement="source")}, edges={}), _source()).apply(proposal)
    issues = caught.value.issues
    assert len(issues) == 1
    assert issues[0].reference == "h1"
    assert issues[0].actual_type == "hypothesis"
    assert set(issues[0].allowed_types) == {"evidence", "proposition"}
    assert issues[0].field == "add_proposition.derived_from_node_ids[0]"


def test_missing_node_issue_exposes_proposition_construction_contract_without_semantic_choice() -> None:
    proposal = InvestigatorProposal.model_validate({
        "graph_updates": [
            {"operation": "add_hypothesis", "local_ref": "h_device_possession", "statement": "device", "reason": "model hypothesis"},
            {"operation": "add_derivation", "derived_proposition_id": "p_unauthorized_device_violation", "source_node_id": "h_device_possession", "reason": "Connect the device explanation."},
        ]
    })
    with pytest.raises(WardenValidationError) as caught:
        GraphWarden(_graph(), _source()).apply(proposal)
    issues = caught.value.issues
    missing = next(issue for issue in issues if issue.reference == "p_unauthorized_device_violation")
    illegal_source = next(issue for issue in issues if issue.reference == "h_device_possession")
    assert missing.error_code == "UNRESOLVED_REFERENCE"
    assert missing.field == "add_derivation.derived_proposition_id"
    assert missing.construction_operation == "add_proposition"
    assert set(missing.construction_allowed_types) == {"evidence", "proposition"}
    assert missing.known_illegal_refs["h_device_possession"] == "hypothesis"
    assert illegal_source.error_code == "INVALID_REFERENCE_TYPE"
    assert "Choose" not in missing.required_action


def test_independent_preflight_issues_are_aggregated() -> None:
    proposal = InvestigatorProposal.model_validate({
        "graph_updates": [
            {"operation": "add_hypothesis", "local_ref": "h_bad_source", "statement": "h", "reason": "model hypothesis"},
            {"operation": "add_support", "source_node_id": "h_bad_source", "target_node_id": "P1", "reason": "invalid source"},
            {"operation": "add_derivation", "derived_proposition_id": "P1", "source_node_id": "E99", "reason": "missing source"},
        ]
    })
    with pytest.raises(WardenValidationError) as caught:
        GraphWarden(_graph(), _source()).apply(proposal)
    assert {(issue.operation_index, issue.reference) for issue in caught.value.issues} == {(1, "h_bad_source"), (2, "E99")}


def test_unknown_reference_does_not_emit_an_additional_unknown_type_issue() -> None:
    proposal = InvestigatorProposal.model_validate({
        "graph_updates": [{"operation": "add_derivation", "derived_proposition_id": "p_never_created", "source_node_id": "E1", "reason": "missing proposition"}]
    })
    with pytest.raises(WardenValidationError) as caught:
        GraphWarden(_graph(), _source()).apply(proposal)
    assert [(issue.reference, issue.error_code) for issue in caught.value.issues] == [("p_never_created", "UNRESOLVED_REFERENCE")]


def test_status_diagnostic_uses_real_graph_status_and_active_requirement() -> None:
    graph = _graph()
    graph.nodes["E1"].status = GraphStatus.ARCHIVED
    proposal = InvestigatorProposal.model_validate({
        "graph_updates": [{"operation": "add_derivation", "derived_proposition_id": "P1", "source_node_id": "E1", "reason": "use record"}]
    })
    with pytest.raises(WardenValidationError) as caught:
        GraphWarden(graph, _source()).apply(proposal)
    issue = caught.value.issues[0]
    assert issue.error_code == "INVALID_REFERENCE_STATUS"
    assert issue.actual_type == "evidence"
    assert issue.actual_status == "archived"
    assert issue.allowed_statuses == ["active"]


def test_exact_repaired_proposal_shape_reports_self_derivation_without_name_error() -> None:
    proposal = InvestigatorProposal.model_validate({
        "graph_updates": [
            {"operation": "add_evidence", "local_ref": "invig_observation_glasses", "statement": "observation", "source_ids": ["S1"], "reason": "record observation"},
            {"operation": "add_evidence", "local_ref": "device_exam_result", "statement": "device result", "source_ids": ["S1"], "reason": "record result"},
            {"operation": "add_hypothesis", "local_ref": "h_device_possession", "statement": "device explanation", "reason": "consider explanation"},
            {"operation": "add_proposition", "local_ref": "p_unauthorized_device_violation", "statement": "device proposition", "derived_from_node_ids": ["invig_observation_glasses", "device_exam_result"], "reason": "derive proposition"},
            {"operation": "add_derivation", "derived_proposition_id": "p_unauthorized_device_violation", "source_node_id": "p_unauthorized_device_violation", "reason": "connect proposition"},
        ]
    })

    with pytest.raises(WardenValidationError) as caught:
        GraphWarden(CaseGraph(case_id="case-01", nodes={"S0": GraphNode(id="S0", node_type=GraphNodeType.SOURCE, statement="source")}, edges={}), _source()).apply(proposal)

    issues = caught.value.issues
    assert [(issue.operation_index, issue.error_code) for issue in issues] == [(4, "SELF_DERIVATION")]
    assert issues[0].field == "add_derivation"
    assert "derived_from_node_ids" in issues[0].required_action


def test_repaired_proposal_without_self_derivation_applies_its_legal_basis() -> None:
    proposal = InvestigatorProposal.model_validate({
        "graph_updates": [
            {"operation": "add_evidence", "local_ref": "observation", "statement": "observation", "source_ids": ["S1"], "reason": "record observation"},
            {"operation": "add_proposition", "local_ref": "p1", "statement": "proposition", "derived_from_node_ids": ["observation"], "reason": "derive proposition"},
        ]
    })
    result = GraphWarden(CaseGraph(case_id="case-01", nodes={"S0": GraphNode(id="S0", node_type=GraphNodeType.SOURCE, statement="source")}, edges={}), _source()).apply(proposal)
    proposition_id = result.local_ref_resolution["p1"]
    assert result.graph.outgoing(proposition_id, relation=EdgeRelation.DERIVED_FROM)


def test_self_derivation_is_aggregated_with_an_unrelated_reference_issue() -> None:
    proposal = InvestigatorProposal.model_validate({
        "graph_updates": [
            {"operation": "add_derivation", "derived_proposition_id": "P1", "source_node_id": "P1", "reason": "self derivation"},
            {"operation": "add_support", "source_node_id": "E99", "target_node_id": "P1", "reason": "missing source"},
        ]
    })
    with pytest.raises(WardenValidationError) as caught:
        GraphWarden(_graph(), _source()).apply(proposal)
    assert {(issue.operation_index, issue.error_code) for issue in caught.value.issues} == {
        (0, "SELF_DERIVATION"),
        (1, "UNRESOLVED_REFERENCE"),
    }


def test_distinct_proposition_derivation_remains_legal() -> None:
    proposal = InvestigatorProposal.model_validate({
        "graph_updates": [
            {"operation": "add_evidence", "local_ref": "e1", "statement": "first evidence", "source_ids": ["S1"], "reason": "record first"},
            {"operation": "add_proposition", "local_ref": "p1", "statement": "first proposition", "derived_from_node_ids": ["e1"], "reason": "derive first"},
            {"operation": "add_evidence", "local_ref": "e2", "statement": "second evidence", "source_ids": ["S1"], "reason": "record second"},
            {"operation": "add_proposition", "local_ref": "p2", "statement": "second proposition", "derived_from_node_ids": ["e2"], "reason": "derive second"},
            {"operation": "add_derivation", "source_node_id": "p1", "derived_proposition_id": "p2", "reason": "connect distinct propositions"},
        ]
    })
    result = GraphWarden(CaseGraph(case_id="case-01", nodes={"S0": GraphNode(id="S0", node_type=GraphNodeType.SOURCE, statement="source")}, edges={}), _source()).apply(proposal)
    assert result.graph.outgoing(result.local_ref_resolution["p2"], relation=EdgeRelation.DERIVED_FROM)
