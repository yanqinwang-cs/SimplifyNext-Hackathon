import pytest

from investigator.graph import GraphNodeType, GraphScope, GraphScopeType
from investigator.models import AssessmentContext, AssessmentSubject, Source, SourceType, SubjectRelationship
from investigator.vnext import AssessmentRulePreset, AssessmentStatus, Confidence, ViolationDefinition, VNextRunInput
from investigator.vnext.runner import VNextInvestigationRunner
from investigator.vnext.semantic import (
    InvestigatorSemanticAssessment,
    SemanticItem,
    SemanticItemKind,
    SemanticSubjectAssessment,
    SemanticValidationError,
    SemanticViolationAssessment,
    compile_semantic_assessment,
)


def _input() -> VNextRunInput:
    subjects = {
        "subject_A": AssessmentSubject(subject_id="subject_A", display_name="Candidate A"),
        "subject_B": AssessmentSubject(subject_id="subject_B", display_name="Candidate B"),
    }
    sources = {
        "S1": Source(id="S1", name="A record", source_type=SourceType.DOCUMENT, content="Candidate A record"),
        "S2": Source(id="S2", name="B record", source_type=SourceType.DOCUMENT, content="Candidate B record"),
        "S3": Source(id="S3", name="Joint record", source_type=SourceType.DOCUMENT, content="Candidate A and Candidate B record"),
    }
    relationships = {
        "rel_AB": SubjectRelationship(relationship_id="rel_AB", subject_ids=["subject_A", "subject_B"], relationship_type="observed_link", source_ids=["S3"]),
    }
    return VNextRunInput(
        case_id="case-compiler",
        assessment_context=AssessmentContext(assessment_id="assessment-compiler"),
        subjects=subjects,
        subject_relationships=relationships,
        sources=sources,
        rule_preset=AssessmentRulePreset(preset_id="preset", violations=[ViolationDefinition(violation_id="V1", label="Rule", rule_text="Rule", prohibited_conduct="Conduct")]),
    )


def _assessment(*, invalid_support: str | None = None) -> InvestigatorSemanticAssessment:
    support_ref = invalid_support or "e_a"
    return InvestigatorSemanticAssessment(
        semantic_items=[
            SemanticItem(local_ref="e_a", kind=SemanticItemKind.EVIDENCE_STATEMENT, statement="A record says X.", about_subject_ids=["subject_A"], basis_source_ids=["S1"]),
            SemanticItem(local_ref="e_b", kind=SemanticItemKind.EVIDENCE_STATEMENT, statement="B record says Y.", about_subject_ids=["subject_B"], basis_source_ids=["S2"]),
            SemanticItem(local_ref="e_joint", kind=SemanticItemKind.EVIDENCE_STATEMENT, statement="A joint record says Z.", about_subject_ids=["subject_A", "subject_B"], basis_source_ids=["S3"]),
        ],
        alternative_explanations=[SemanticItem(local_ref="alt_a", kind=SemanticItemKind.HYPOTHESIS, statement="An alternative explanation for A.", about_subject_ids=["subject_A"], basis_source_ids=["S1"])],
        subject_assessments=[
            SemanticSubjectAssessment(subject_id="subject_A", violation_assessments=[SemanticViolationAssessment(violation_id="V1", status=AssessmentStatus.SUPPORTED, supporting_item_refs=[support_ref], limiting_item_refs=["e_joint"], alternative_item_refs=["alt_a"], reasoning_summary="A bounded semantic conclusion.", confidence=Confidence.MODERATE)], furthest_conclusion={"statement": "The record supports a bounded assessment.", "confidence": "moderate"}),
            SemanticSubjectAssessment(subject_id="subject_B", violation_assessments=[SemanticViolationAssessment(violation_id="V1", status=AssessmentStatus.NOT_CURRENTLY_SUPPORTED, reasoning_summary="The record does not currently support the assessment.", confidence=Confidence.LOW)], furthest_conclusion={"statement": "The record remains insufficient.", "confidence": "low"}),
        ],
    )


def test_valid_semantic_assessment_compiles_and_warden_applies() -> None:
    run_input = _input()
    compiled = compile_semantic_assessment(_assessment(), run_input)
    result = VNextInvestigationRunner(lambda _: compiled).run(run_input)
    assert result.subject_assessments[0].violation_assessments[0].status is AssessmentStatus.SUPPORTED
    assert all(node.node_type is not GraphNodeType.SOURCE for node in result.graph.nodes.values() if node.id.startswith("H"))
    assert any(edge.relation.value == "supports" for edge in result.graph.edges.values())


def test_private_source_cross_student_and_joint_private_stitching_are_rejected() -> None:
    run_input = _input()
    private_misuse = _assessment(invalid_support="e_bad").model_copy(update={
        "semantic_items": _assessment().semantic_items + [SemanticItem(local_ref="e_bad", kind=SemanticItemKind.EVIDENCE_STATEMENT, statement="Misapplied record.", about_subject_ids=["subject_A"], basis_source_ids=["S2"])],
    })
    with pytest.raises(SemanticValidationError, match="not permitted"):
        compile_semantic_assessment(private_misuse, run_input)
    joint_private = _assessment().model_copy(update={
        "semantic_items": [SemanticItem(local_ref="joint", kind=SemanticItemKind.PROPOSITION, statement="Joint conclusion.", about_subject_ids=["subject_A", "subject_B"], basis_source_ids=["S1", "S2"], basis_item_refs=["e_a", "e_b"])],
    })
    with pytest.raises(SemanticValidationError, match="relationship-valid joint source"):
        compile_semantic_assessment(joint_private, run_input)


def test_hypothesis_cannot_be_supporting_material() -> None:
    assessment = _assessment().model_copy(update={
        "subject_assessments": [_assessment().subject_assessments[0].model_copy(update={"violation_assessments": [_assessment().subject_assessments[0].violation_assessments[0].model_copy(update={"supporting_item_refs": ["alt_a"]})]}), _assessment().subject_assessments[1]],
    })
    with pytest.raises(SemanticValidationError, match="supporting material"):
        compile_semantic_assessment(assessment, _input())
