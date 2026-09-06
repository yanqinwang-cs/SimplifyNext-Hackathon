from investigator.models import AssessmentContext, AssessmentSubject, Source, SourceType, SubjectRelationship
from investigator.vnext import AssessmentRulePreset, VNextRunInput, ViolationDefinition
from investigator.vnext.model import build_prompt


def _run_input() -> VNextRunInput:
    return VNextRunInput(
        case_id="prompt-calibration",
        assessment_context=AssessmentContext(assessment_id="assessment-1"),
        subjects={
            "subject_A": AssessmentSubject(subject_id="subject_A", display_name="Candidate A"),
            "subject_B": AssessmentSubject(subject_id="subject_B", display_name="Candidate B"),
        },
        subject_relationships={
            "rel_AB": SubjectRelationship(
                relationship_id="rel_AB",
                subject_ids=["subject_A", "subject_B"],
                relationship_type="observed_link",
                source_ids=["S1"],
            )
        },
        sources={
            "S1": Source(
                id="S1",
                name="record.md",
                source_type=SourceType.DOCUMENT,
                content="Candidate A and Candidate B record",
            )
        },
        rule_preset=AssessmentRulePreset(
            preset_id="preset",
            violations=[
                ViolationDefinition(
                    violation_id="V1",
                    label="Rule",
                    rule_text="Rule",
                    prohibited_conduct="Conduct",
                )
            ],
        ),
    )


def test_prompt_uses_abductive_cumulative_calibration() -> None:
    prompt = build_prompt(_run_input())
    assert "which explanation better accounts for the total observed pattern" in prompt
    assert "Evaluate evidence cumulatively" in prompt
    assert "does not need to prove the violation independently" in prompt
    assert "not conclusively proven" in prompt
    assert "PARTIALLY_SUPPORTED: the violation receives meaningful affirmative support" in prompt
    assert "NOT_CURRENTLY_SUPPORTED: there is little meaningful affirmative evidence" in prompt
    assert "Compare alternatives rather than merely listing them" in prompt
    assert "Confidence is confidence that the selected assessment status accurately characterizes" in prompt


def test_prompt_keeps_structural_scope_and_relationship_boundaries() -> None:
    prompt = build_prompt(_run_input())
    assert "Sticky source scope remains authoritative" in prompt
    assert "One student's private source cannot become another student's evidence" in prompt
    assert "private A plus private B cannot become one joint proposition" in prompt
    assert "Relationship participation never propagates guilt" in prompt
    assert "No new relationship may be inferred merely from similarity" in prompt
    assert "Final institutional judgment remains human" in prompt


def test_prompt_allows_relationship_hypotheses_without_joint_evidence_basis() -> None:
    prompt = build_prompt(_run_input())
    assert "A multi-student hypothesis may concern an existing exact relationship context without evidential support" in prompt
    assert "Multi-student evidence statements and propositions must obey current source and scope rules" in prompt
    assert "requires a genuinely admitted relationship- or case-scoped basis" in prompt
    assert "is not subject to the evidence-statement/proposition source-basis requirement" in prompt
    assert "multi-student semantic item is permitted only when one admitted source" not in prompt.lower()
    assert "every multi-student" not in prompt.lower()


def test_prompt_removes_categorical_circumstantial_cautions() -> None:
    prompt = build_prompt(_run_input()).lower()
    for stale in (
        "similarity does not establish copying or collaboration",
        "similarity does not establish collaboration",
        "association does not establish prohibited collaboration",
        "opportunity does not establish use",
        "anomaly does not establish misconduct",
    ):
        assert stale not in prompt


def test_calibration_examples_remain_qualitative_and_non_deterministic() -> None:
    scenarios = {
        "proximity only": "not_currently_supported",
        "proximity + sightline + directed looking + lip movement": "partially_supported",
        "looking + immediate answer change + corresponding evidence": "supported",
        "same observations + affirmative counterevidence": "conflicted",
    }
    prompt = build_prompt(_run_input())
    assert "do not introduce numerical probabilities" in prompt.lower()
    assert "PARTIALLY_SUPPORTED" in prompt and "SUPPORTED" in prompt and "CONFLICTED" in prompt
    assert set(scenarios.values()) == {
        "not_currently_supported",
        "partially_supported",
        "supported",
        "conflicted",
    }
