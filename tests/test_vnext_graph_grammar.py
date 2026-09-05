from investigator.graph import OPERATION_CONTRACTS
from investigator.models import AssessmentContext, AssessmentSubject, Source, SourceType, SubjectRelationship
from investigator.vnext import AssessmentRulePreset, VNextRunInput, ViolationDefinition
from investigator.vnext.graph_grammar import model_facing_graph_grammar, model_facing_operation_names
from investigator.vnext.model import build_prompt


def _run_input() -> VNextRunInput:
    subjects = {
        "subject_A": AssessmentSubject(subject_id="subject_A", display_name="Candidate A"),
        "subject_B": AssessmentSubject(subject_id="subject_B", display_name="Candidate B"),
    }
    source = Source(id="S1", name="record", source_type=SourceType.DOCUMENT, content="Candidate A and Candidate B")
    return VNextRunInput(
        case_id="case-1",
        assessment_context=AssessmentContext(assessment_id="assessment-1"),
        subjects=subjects,
        subject_relationships={
            "rel_AB": SubjectRelationship(
                relationship_id="rel_AB", subject_ids=["subject_A", "subject_B"],
                relationship_type="observed_communication", source_ids=["S1"],
            ),
        },
        sources={"S1": source},
        rule_preset=AssessmentRulePreset(
            preset_id="preset",
            violations=[ViolationDefinition(violation_id="V1", label="Rule", rule_text="Rule", prohibited_conduct="Conduct")],
        ),
    )


def test_model_grammar_matches_vnext_operations_and_registry_reference_types() -> None:
    grammar = model_facing_graph_grammar()
    assert model_facing_operation_names() == set(OPERATION_CONTRACTS)
    for operation, contract in OPERATION_CONTRACTS.items():
        assert f"{operation}: creates " in grammar
        for reference in contract.references:
            assert f"{reference.field}:" in grammar
            for node_type in reference.allowed_types:
                assert node_type.value.upper() in grammar


def test_prompt_contains_complete_grammar_and_demonstrated_prohibitions() -> None:
    prompt = build_prompt(_run_input())
    assert "COMPLETE LEGAL GRAPH OPERATION CONTRACT" in prompt
    assert "SUBJECT -> RELATIONSHIP" in prompt
    assert "HYPOTHESIS -> SUPPORTS -> HYPOTHESIS" in prompt
    assert "add_evidence" in prompt and "add_specialization" in prompt
    assert "IF YOU ARE UNSURE WHETHER A GRAPH OPERATION IS LEGAL, OMIT IT" in prompt
