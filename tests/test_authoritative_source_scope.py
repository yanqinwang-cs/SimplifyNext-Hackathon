"""Focused consistency tests for authoritative source scopes."""

from __future__ import annotations

import json

import pytest

from investigator.graph import CaseGraph, GraphNode, GraphNodeType, GraphScope, GraphScopeType
from investigator.models import AssessmentContext, AssessmentSubject, Source, SourceType, SubjectRelationship
from investigator.reporting import build_input_snapshot, build_report_record
from investigator.state import CaseState
from investigator.vnext import (
    AssessmentRulePreset,
    AssessmentStatus,
    Confidence,
    FurthestJustifiedConclusion,
    GraphWarden,
    InvestigatorAssessment,
    InvestigatorProposal,
    SubjectAssessment,
    VNextInvestigationRunner,
    ViolationAssessment,
    ViolationDefinition,
    build_source_applicability,
    clean_reasoning_graph,
    run_input_from_case_state,
)
from investigator.vnext.model import build_prompt
from investigator.vnext.semantic import (
    EvidenceStatementItem,
    InvestigatorSemanticAssessment,
    SemanticSubjectAssessment,
    SemanticViolationAssessment,
    compile_semantic_assessment,
)
from investigator.vnext.warden import WardenValidationError


SUBJECTS = {
    "subject_A": AssessmentSubject(subject_id="subject_A", display_name="Candidate A"),
    "subject_B": AssessmentSubject(subject_id="subject_B", display_name="Candidate B"),
}
PRESET = AssessmentRulePreset(
    preset_id="scope-consistency",
    violations=[ViolationDefinition(violation_id="V1", label="Conduct", rule_text="A configured rule.", prohibited_conduct="Conduct")],
)


def _source(source_id: str, content: str, scope: GraphScope | None = None) -> Source:
    metadata = {"assessment_scope": scope.model_dump(mode="json")} if scope is not None else {}
    return Source(id=source_id, name=f"{source_id}.md", source_type=SourceType.DOCUMENT, content=content, metadata=metadata)


def _relationship() -> dict[str, SubjectRelationship]:
    return {
        "rel_A_B": SubjectRelationship(
            relationship_id="rel_A_B",
            subject_ids=["subject_A", "subject_B"],
            relationship_type="adjacent",
            source_ids=["S1"],
        )
    }


def _applicability(source: Source, relationships: dict[str, SubjectRelationship] | None = None):
    return build_source_applicability({source.id: source}, SUBJECTS, relationships or {})[source.id]


def _proposal(source_id: str, scope: GraphScope):
    return InvestigatorProposal.model_validate({
        "graph_updates": [{
            "operation": "add_evidence",
            "local_ref": "case_evidence",
            "statement": "The source records the relevant context.",
            "source_ids": [source_id],
            "scope": scope.model_dump(mode="json"),
            "reason": "Record the authoritative source scope.",
        }]
    })


def _warden(sources: dict[str, Source], relationships: dict[str, SubjectRelationship] | None = None) -> GraphWarden:
    relationships = relationships or {}
    return GraphWarden(
        clean_reasoning_graph("scope-consistency", sources),
        sources,
        subjects=SUBJECTS,
        subject_relationships=relationships,
        source_applicability=build_source_applicability(sources, SUBJECTS, relationships),
    )


def test_trusted_case_scope_overrides_multi_student_textual_classification() -> None:
    source = _source("S1", "Candidate A and Candidate B are named in this case-wide seating record.", GraphScope(scope_type=GraphScopeType.CASE))
    applicability = _applicability(source, _relationship())
    assert applicability.trusted_scope.scope_type is GraphScopeType.CASE
    assert applicability.classification.value == "case_shared"
    assert applicability.case_shared_allowed is True
    assert applicability.permitted_subject_ids == sorted(SUBJECTS)


def test_prompt_places_trusted_case_source_in_case_wide_inventory() -> None:
    source = _source("S1", "Candidate A and Candidate B are named in this case-wide seating record.", GraphScope(scope_type=GraphScopeType.CASE))
    state = CaseState(
        case_id="scope-prompt",
        title="Scope prompt",
        assessment_context=AssessmentContext(assessment_id="scope-prompt"),
        subjects=SUBJECTS,
        subject_relationships=_relationship(),
        sources={"S1": source},
    )
    run_input = run_input_from_case_state(state, PRESET)
    prompt = build_prompt(run_input)
    assert "CASE-WIDE" in prompt
    assert "S1" in prompt
    assert '"student_specific": {}' in prompt
    assert '"multi_student_candidate": []' in prompt


def test_trusted_case_scope_replays_live_failure_through_compiler_and_warden() -> None:
    source = _source("S1", "Candidate A and Candidate B are named in this case-wide seating record.", GraphScope(scope_type=GraphScopeType.CASE))
    relationships = _relationship()
    state = CaseState(
        case_id="scope-replay",
        title="Scope replay",
        assessment_context=AssessmentContext(assessment_id="scope-replay"),
        subjects=SUBJECTS,
        subject_relationships=relationships,
        sources={"S1": source},
    )
    run_input = run_input_from_case_state(state, PRESET)
    semantic = InvestigatorSemanticAssessment(
        semantic_items=[EvidenceStatementItem(
            local_ref="case_evidence",
            kind="evidence_statement",
            statement="The trusted case-wide record describes the configured seating context.",
            basis_source_ids=["S1"],
        )],
        subject_assessments=[SemanticSubjectAssessment(
            subject_id=subject_id,
            violation_assessments=[SemanticViolationAssessment(
                violation_id="V1",
                status=AssessmentStatus.NOT_CURRENTLY_SUPPORTED,
                limiting_item_refs=["case_evidence"],
                reasoning_summary="The record provides context but does not establish prohibited conduct.",
                confidence=Confidence.LOW,
            )],
            furthest_conclusion=FurthestJustifiedConclusion(statement="No supported finding is currently justified.", confidence=Confidence.LOW),
        ) for subject_id in SUBJECTS],
    )
    compiled = compile_semantic_assessment(semantic, run_input)
    evidence = next(item for item in compiled.proposal.graph_updates if getattr(item, "local_ref", None) == "case_evidence")
    assert evidence.scope.scope_type is GraphScopeType.CASE
    result = VNextInvestigationRunner(lambda _: compiled).run(run_input)
    snapshot = build_input_snapshot(state, PRESET, "scope-replay-run")
    report = build_report_record(snapshot, result, completed_at="offline")
    assert [student["subject_id"] for student in report["students"]] == sorted(SUBJECTS)


def test_untrusted_multi_student_mentions_cannot_become_case_scope() -> None:
    source = _source("S1", "Candidate A and Candidate B are mentioned, but no trusted scope is supplied.")
    applicability = _applicability(source)
    assert applicability.trusted_scope is None
    assert applicability.case_shared_allowed is False
    assert applicability.permitted_subject_ids == []
    with pytest.raises(WardenValidationError, match="widened to CASE"):
        _warden({"S1": source}).apply(_proposal("S1", GraphScope(scope_type=GraphScopeType.CASE)))


def test_trusted_subject_scope_remains_private_and_cannot_widen() -> None:
    source = _source("S1", "Candidate A and Candidate B are both mentioned in a private record.", GraphScope(scope_type=GraphScopeType.SUBJECT, subject_id="subject_A"))
    applicability = _applicability(source)
    assert applicability.classification.value == "student_specific"
    assert applicability.permitted_subject_ids == ["subject_A"]
    with pytest.raises(WardenValidationError, match="widened to CASE"):
        _warden({"S1": source}).apply(_proposal("S1", GraphScope(scope_type=GraphScopeType.CASE)))
    with pytest.raises(WardenValidationError, match="student 'subject_B'"):
        _warden({"S1": source}).apply(_proposal("S1", GraphScope(scope_type=GraphScopeType.SUBJECT, subject_id="subject_B")))


def test_trusted_relationship_scope_is_accepted_only_as_relationship_scope() -> None:
    relationships = _relationship()
    source = _source("S1", "Candidate A and Candidate B are named in a trusted relationship record.", GraphScope(scope_type=GraphScopeType.RELATIONSHIP, relationship_id="rel_A_B"))
    applicability = _applicability(source, relationships)
    assert applicability.classification.value == "multi_student_candidate"
    relationship_scope = GraphScope(scope_type=GraphScopeType.RELATIONSHIP, relationship_id="rel_A_B")
    _warden({"S1": source}, relationships).apply(_proposal("S1", relationship_scope))
    with pytest.raises(WardenValidationError, match="widened to CASE"):
        _warden({"S1": source}, relationships).apply(_proposal("S1", GraphScope(scope_type=GraphScopeType.CASE)))
