from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from investigator.models import AssessmentContext, AssessmentSubject, Source, SourceType, SubjectRelationship
from investigator.state import CaseState
from investigator.vnext import AssessmentRulePreset, VNextRunInput, ViolationDefinition
from investigator.vnext.model import build_prompt


def source(source_id: str = "S1") -> Source:
    return Source(id=source_id, name="record", source_type=SourceType.DOCUMENT, content="record")


def preset() -> AssessmentRulePreset:
    return AssessmentRulePreset(
        preset_id="preset",
        violations=[ViolationDefinition(violation_id="V1", label="Rule", rule_text="Rule", prohibited_conduct="Conduct")],
    )


def identity_parts() -> tuple[AssessmentContext, dict[str, AssessmentSubject], dict[str, SubjectRelationship]]:
    context = AssessmentContext(assessment_id="assessment-1", assessment_type="exam")
    subjects = {
        "subject_A": AssessmentSubject(subject_id="subject_A", display_name="Candidate A"),
        "subject_B": AssessmentSubject(subject_id="subject_B", display_name="Candidate B"),
    }
    relationships = {
        "rel_AB": SubjectRelationship(
            relationship_id="rel_AB", subject_ids=["subject_A", "subject_B"],
            relationship_type="observed_communication", source_ids=["S1"],
        )
    }
    return context, subjects, relationships


def test_assessment_context_accepts_minimal_and_optional_metadata() -> None:
    context = AssessmentContext(assessment_id="assessment-1", metadata={"room": "R1"})
    assert context.assessment_id == "assessment-1"
    assert context.metadata == {"room": "R1"}


def test_assessment_context_rejects_reversed_times() -> None:
    with pytest.raises(ValidationError, match="end_time"):
        AssessmentContext(
            assessment_id="assessment-1",
            start_time=datetime(2026, 1, 2, tzinfo=timezone.utc),
            end_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )


@pytest.mark.parametrize("payload", [{"subject_id": "", "display_name": "A"}, {"subject_id": "A", "display_name": ""}])
def test_assessment_subject_requires_nonempty_identity_fields(payload: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        AssessmentSubject(**payload)


def test_subject_relationship_requires_distinct_subjects_and_sources() -> None:
    with pytest.raises(ValidationError, match="subject_ids"):
        SubjectRelationship(relationship_id="r", subject_ids=["A", "A"], relationship_type="link")
    with pytest.raises(ValidationError, match="source_ids"):
        SubjectRelationship(relationship_id="r", subject_ids=["A", "B"], relationship_type="link", source_ids=["S1", "S1"])
    with pytest.raises(ValidationError):
        SubjectRelationship(relationship_id="r", subject_ids=["A"], relationship_type="link")


def test_case_state_validates_identity_mappings_and_references() -> None:
    context, subjects, relationships = identity_parts()
    state = CaseState(case_id="case-1", title="Case", assessment_context=context, subjects=subjects, subject_relationships=relationships, sources={"S1": source()})
    assert state.subjects["subject_A"].display_name == "Candidate A"
    with pytest.raises(ValidationError, match="Subject mapping keys"):
        CaseState(case_id="case-1", title="Case", subjects={"wrong": subjects["subject_A"]})
    with pytest.raises(ValidationError, match="relationship mapping keys"):
        CaseState(case_id="case-1", title="Case", subjects=subjects, subject_relationships={"wrong": relationships["rel_AB"]}, sources={"S1": source()})
    bad_subject = relationships["rel_AB"].model_copy(update={"subject_ids": ["subject_A", "missing"]})
    with pytest.raises(ValidationError, match="Unknown subject"):
        CaseState(case_id="case-1", title="Case", subjects=subjects, subject_relationships={"rel_AB": bad_subject}, sources={"S1": source()})
    bad_source = relationships["rel_AB"].model_copy(update={"source_ids": ["S99"]})
    with pytest.raises(ValidationError, match="Unknown source"):
        CaseState(case_id="case-1", title="Case", subjects=subjects, subject_relationships={"rel_AB": bad_source}, sources={"S1": source()})


def test_legacy_case_state_without_identity_fields_still_loads() -> None:
    state = CaseState.model_validate({"case_id": "case-1", "title": "Legacy", "sources": {}})
    assert state.assessment_context is None
    assert state.subjects == {}
    assert state.subject_relationships == {}


def test_vnext_run_input_copies_identity_structure_and_validates_standalone() -> None:
    context, subjects, relationships = identity_parts()
    state = CaseState(case_id="case-1", title="Case", assessment_context=context, subjects=subjects, subject_relationships=relationships, sources={"S1": source()})
    run_input = VNextRunInput.from_case_state(state, preset())
    assert run_input.assessment_context == context
    assert run_input.subjects == subjects
    assert run_input.subject_relationships == relationships
    with pytest.raises(ValidationError, match="Unknown subject"):
        VNextRunInput(case_id="case-1", subjects=subjects, subject_relationships={"rel": relationships["rel_AB"].model_copy(update={"relationship_id": "rel", "subject_ids": ["missing", "subject_B"]})}, sources={"S1": source()}, rule_preset=preset())


def test_prompt_contains_structured_identity_context() -> None:
    context, subjects, relationships = identity_parts()
    run_input = VNextRunInput(case_id="case-1", assessment_context=context, subjects=subjects, subject_relationships=relationships, sources={"S1": source()}, rule_preset=preset())
    prompt = build_prompt(run_input)
    assert "ASSESSMENT CONTEXT" in prompt
    assert "assessment-1" in prompt
    assert "subject_A" in prompt and "subject_B" in prompt
    assert "rel_AB" in prompt and "observed_communication" in prompt
    assert "subject_id is the authoritative identity key" in prompt
    assert "not automatically prohibited collaboration" in prompt
