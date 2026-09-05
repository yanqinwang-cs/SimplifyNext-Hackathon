import pytest
from pydantic import ValidationError

from investigator.graph import CaseGraph, EdgeRelation, GraphEdge, GraphNode, GraphNodeType, GraphScope, GraphScopeType, GraphStatus, make_edge_id, node_scope
from investigator.llm import ModelCallMetadata, ModelCallResult
from investigator.models import AssessmentSubject, Source, SourceType, SubjectRelationship
from investigator.services.evidence_requests import HumanEvidenceWorkflow
from investigator.state import CaseRepository
from investigator.vnext import AssessmentRulePreset, AssessmentStatus, Confidence, FurthestJustifiedConclusion, GraphWarden, InvestigatorAssessment, InvestigatorProposal, SubjectAssessment, VNextInvestigationRunner, VNextRunInput, ViolationAssessment, ViolationDefinition
from investigator.vnext.warden import WardenValidationError
from investigator.vnext.model import build_prompt


def source(source_id: str = "S1") -> Source:
    return Source(
        id=source_id,
        name=source_id,
        source_type=SourceType.DOCUMENT,
        content="subject_A subject_B Candidate A Candidate B record",
        metadata={"assessment_scope": {"scope_type": "case"}},
    )


def subjects() -> dict[str, AssessmentSubject]:
    return {key: AssessmentSubject(subject_id=key, display_name=key) for key in ("A", "B", "C", "D")}


def relationships() -> dict[str, SubjectRelationship]:
    return {
        "AB": SubjectRelationship(relationship_id="AB", subject_ids=["A", "B"], relationship_type="observed_communication"),
        "CD": SubjectRelationship(relationship_id="CD", subject_ids=["C", "D"], relationship_type="observed_communication"),
    }


def scope(kind: GraphScopeType, *, subject: str | None = None, relationship: str | None = None) -> GraphScope:
    return GraphScope(scope_type=kind, subject_id=subject, relationship_id=relationship)


def graph_with_scopes() -> CaseGraph:
    nodes = {
        key: GraphNode(id=node_id, semantic_key=key, node_type=node_type, statement=statement, metadata={"assessment_scope": node_scope_value.model_dump(mode="json")})
        for key, node_id, node_type, statement, node_scope_value in [
            ("E_CASE", "E1", GraphNodeType.EVIDENCE, "case", scope(GraphScopeType.CASE)),
            ("E_A", "E2", GraphNodeType.EVIDENCE, "A", scope(GraphScopeType.SUBJECT, subject="A")),
            ("E_B", "E3", GraphNodeType.EVIDENCE, "B", scope(GraphScopeType.SUBJECT, subject="B")),
            ("E_AB", "E4", GraphNodeType.EVIDENCE, "AB", scope(GraphScopeType.RELATIONSHIP, relationship="AB")),
            ("E_CD", "E5", GraphNodeType.EVIDENCE, "CD", scope(GraphScopeType.RELATIONSHIP, relationship="CD")),
            ("H_A", "H1", GraphNodeType.HYPOTHESIS, "A hypothesis", scope(GraphScopeType.SUBJECT, subject="A")),
            ("H_B", "H2", GraphNodeType.HYPOTHESIS, "B hypothesis", scope(GraphScopeType.SUBJECT, subject="B")),
            ("H_AB", "H3", GraphNodeType.HYPOTHESIS, "AB hypothesis", scope(GraphScopeType.RELATIONSHIP, relationship="AB")),
            ("H_CD", "H4", GraphNodeType.HYPOTHESIS, "CD hypothesis", scope(GraphScopeType.RELATIONSHIP, relationship="CD")),
        ]
    }
    return CaseGraph(case_id="case-01", nodes={node.id: node for node in nodes.values()}, edges={})


def proposal(updates: list[dict]) -> InvestigatorProposal:
    return InvestigatorProposal.model_validate({"graph_updates": updates})


def warden(graph: CaseGraph, *, context: bool = True) -> GraphWarden:
    return GraphWarden(graph, {"S1": source()}, subjects=subjects() if context else {}, subject_relationships=relationships() if context else {})


def relation(operation: str, source_id: str, target_id: str) -> dict:
    return {"operation": operation, "source_node_id": source_id, "target_node_id": target_id, "reason": "test relation"}


@pytest.mark.parametrize("value", [
    scope(GraphScopeType.CASE),
    scope(GraphScopeType.SUBJECT, subject="A"),
    scope(GraphScopeType.RELATIONSHIP, relationship="AB"),
])
def test_graph_scope_valid_forms(value: GraphScope) -> None:
    assert GraphScope.model_validate(value.model_dump()) == value


@pytest.mark.parametrize("payload", [
    {"scope_type": "case", "subject_id": "A"},
    {"scope_type": "subject"},
    {"scope_type": "subject", "subject_id": "A", "relationship_id": "AB"},
    {"scope_type": "relationship"},
    {"scope_type": "relationship", "relationship_id": "AB", "subject_id": "A"},
])
def test_graph_scope_rejects_invalid_identity_combinations(payload: dict) -> None:
    with pytest.raises(ValidationError):
        GraphScope.model_validate(payload)


@pytest.mark.parametrize("operation", ["add_evidence", "add_proposition", "add_hypothesis", "add_uncertainty"])
def test_multi_subject_node_creation_requires_scope(operation: str) -> None:
    fields = {"operation": operation, "local_ref": "node_ref", "statement": "node", "reason": "create"}
    if operation == "add_evidence":
        fields["source_ids"] = ["S1"]
    elif operation == "add_proposition":
        fields["derived_from_node_ids"] = ["E_A"]
    elif operation == "add_uncertainty":
        fields["target_node_id"] = "E2"
    with pytest.raises(WardenValidationError) as caught:
        warden(graph_with_scopes()).apply(proposal([fields]))
    assert caught.value.issues[0].error_code == "MISSING_SCOPE"


def test_known_and_unknown_scope_identities_are_deterministic() -> None:
    valid = proposal([{"operation": "add_evidence", "local_ref": "a_new", "statement": "A", "source_ids": ["S1"], "scope": scope(GraphScopeType.SUBJECT, subject="A").model_dump(), "reason": "create"}])
    result = warden(CaseGraph(case_id="case-01", nodes={"S0": GraphNode(id="S0", node_type=GraphNodeType.SOURCE, statement="source")}, edges={})).apply(valid)
    assert node_scope(result.graph.nodes[result.local_ref_resolution["a_new"]].metadata).subject_id == "A"
    invalid = proposal([{"operation": "add_evidence", "local_ref": "x", "statement": "X", "source_ids": ["S1"], "scope": scope(GraphScopeType.SUBJECT, subject="X").model_dump(), "reason": "create"}])
    with pytest.raises(WardenValidationError) as caught:
        warden(CaseGraph(case_id="case-01", nodes={"S0": GraphNode(id="S0", node_type=GraphNodeType.SOURCE, statement="source")}, edges={})).apply(invalid)
    assert caught.value.issues[0].error_code == "INVALID_SCOPE"


def test_unknown_relationship_scope_is_rejected() -> None:
    invalid = proposal([{"operation": "add_hypothesis", "local_ref": "h_new", "statement": "new", "scope": scope(GraphScopeType.RELATIONSHIP, relationship="missing").model_dump(), "reason": "create"}])
    with pytest.raises(WardenValidationError) as caught:
        warden(CaseGraph(case_id="case-01", nodes={"S0": GraphNode(id="S0", node_type=GraphNodeType.SOURCE, statement="source")}, edges={})).apply(invalid)
    assert caught.value.issues[0].error_code == "INVALID_SCOPE"


def test_same_turn_private_subject_edge_is_rejected_before_apply() -> None:
    updates = [
        {"operation": "add_evidence", "local_ref": "a_obs", "statement": "A", "source_ids": ["S1"], "scope": scope(GraphScopeType.SUBJECT, subject="A").model_dump(), "reason": "A"},
        {"operation": "add_hypothesis", "local_ref": "b_hyp", "statement": "B", "scope": scope(GraphScopeType.SUBJECT, subject="B").model_dump(), "reason": "B"},
        relation("add_support", "a_obs", "b_hyp"),
    ]
    with pytest.raises(WardenValidationError) as caught:
        warden(CaseGraph(case_id="case-01", nodes={"S0": GraphNode(id="S0", node_type=GraphNodeType.SOURCE, statement="source")}, edges={})).apply(proposal(updates))
    issue = next(issue for issue in caught.value.issues if issue.error_code == "INCOMPATIBLE_SCOPE")
    assert issue.source == "a_obs" and issue.target == "b_hyp"
    assert issue.source_scope["subject_id"] == "A" and issue.target_scope["subject_id"] == "B"


@pytest.mark.parametrize("source_id,target_id,allowed", [("E2", "H1", True), ("E2", "H2", False), ("E2", "H3", False), ("E3", "H3", False), ("E4", "H1", True), ("E4", "H2", True), ("E4", "H4", False), ("E1", "H1", True), ("E1", "H3", True)])
def test_scope_compatibility_matrix_for_support(source_id: str, target_id: str, allowed: bool) -> None:
    update = proposal([relation("add_support", source_id, target_id)])
    if allowed:
        assert warden(graph_with_scopes()).apply(update).graph.edges
    else:
        with pytest.raises(WardenValidationError) as caught:
            warden(graph_with_scopes()).apply(update)
        assert any(issue.error_code == "INCOMPATIBLE_SCOPE" for issue in caught.value.issues)


def test_relationship_to_relationship_scope_requires_same_relationship() -> None:
    with pytest.raises(WardenValidationError) as caught:
        warden(graph_with_scopes()).apply(proposal([relation("add_support", "E4", "H4")]))
    assert any(issue.error_code == "INCOMPATIBLE_SCOPE" for issue in caught.value.issues)
    assert warden(graph_with_scopes()).apply(proposal([relation("add_support", "E4", "H3")])).graph.edges


def test_subject_assessment_references_enforce_scope_after_resolution() -> None:
    graph_proposal = proposal([
        {"operation": "add_evidence", "local_ref": "a_obs", "statement": "A", "source_ids": ["S1"], "scope": scope(GraphScopeType.SUBJECT, subject="A").model_dump(), "reason": "A"},
        {"operation": "add_evidence", "local_ref": "b_obs", "statement": "B", "source_ids": ["S1"], "scope": scope(GraphScopeType.SUBJECT, subject="B").model_dump(), "reason": "B"},
        {"operation": "add_evidence", "local_ref": "ab_obs", "statement": "AB", "source_ids": ["S1"], "scope": GraphScope(scope_type=GraphScopeType.RELATIONSHIP, relationship_ref="R1").model_dump(), "reason": "AB"},
    ])
    rule = AssessmentRulePreset(preset_id="p", violations=[ViolationDefinition(violation_id="V1", label="V1", rule_text="r", prohibited_conduct="c")])
    def result_for(subject_id: str, refs: list[str]) -> InvestigatorAssessment:
        assessments = [SubjectAssessment(subject_id=item, violation_assessments=[ViolationAssessment(violation_id="V1", status=AssessmentStatus.SUPPORTED if item == subject_id else AssessmentStatus.NOT_CURRENTLY_SUPPORTED, supporting_node_ids=refs if item == subject_id else [], reasoning_summary="r", confidence=Confidence.HIGH)], furthest_conclusion=FurthestJustifiedConclusion(statement="c", confidence=Confidence.HIGH)) for item in ("A", "B")]
        return InvestigatorAssessment(proposal=graph_proposal, subject_assessments=assessments)
    run = VNextRunInput(case_id="case-01", sources={"S1": source()}, subjects={key: AssessmentSubject(subject_id=key, display_name=key) for key in ("A", "B")}, subject_relationships={"AB": SubjectRelationship(relationship_id="AB", subject_ids=["A", "B"], relationship_type="communication", source_ids=["S1"])}, rule_preset=rule)
    valid = VNextInvestigationRunner(lambda _: result_for("A", ["a_obs", "ab_obs"])).run(run)
    assert len(valid.subject_assessments[0].violation_assessments[0].supporting_node_ids) == 2
    with pytest.raises(Exception, match="cannot reference graph node"):
        VNextInvestigationRunner(lambda _: result_for("A", ["b_obs"])).run(run)


def test_scope_round_trip_and_single_subject_compatibility() -> None:
    graph = graph_with_scopes()
    round_trip = CaseGraph.model_validate(graph.model_dump(mode="json"))
    assert node_scope(round_trip.nodes["E4"].metadata).relationship_id == "AB"
    legacy = proposal([{"operation": "add_evidence", "local_ref": "legacy", "statement": "legacy", "source_ids": ["S1"], "reason": "legacy"}])
    result = GraphWarden(CaseGraph(case_id="case-01", nodes={"S0": GraphNode(id="S0", node_type=GraphNodeType.SOURCE, statement="source")}, edges={}), {"S1": source()}).apply(legacy)
    assert node_scope(result.graph.nodes[result.local_ref_resolution["legacy"]].metadata).subject_id == "case_subject"


def test_prompt_requires_explicit_scope_and_relationship_discipline() -> None:
    rule = AssessmentRulePreset(preset_id="p", violations=[ViolationDefinition(violation_id="V1", label="V1", rule_text="r", prohibited_conduct="c")])
    run = VNextRunInput(case_id="case-01", sources={"S1": source()}, subjects=subjects(), subject_relationships={key: value.model_copy(update={"source_ids": ["S1"]}) for key, value in relationships().items()}, rule_preset=rule)
    prompt = build_prompt(run)
    assert "every semantic item must explicitly list" in prompt.lower()
    assert "do not combine separately restricted student evidence" in prompt.lower()
