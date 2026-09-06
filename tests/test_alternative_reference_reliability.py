from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from investigator.reporting import build_input_snapshot, build_report_record
from investigator.services.vnext_runner import VNextProductionRunner
from investigator.state import CaseState
from investigator.test_fixtures import load_scale_fixture_case_state
from investigator.vnext import AssessmentStatus, VNextInvestigationRunner, run_input_from_case_state
from investigator.vnext.model import build_prompt
from investigator.vnext.presets import academic_integrity_core_preset
from investigator.vnext.semantic import (
    HypothesisItem,
    InvestigatorSemanticAssessment,
    PropositionItem,
    SemanticValidationError,
    compile_semantic_assessment,
)


ROOT = Path(__file__).resolve().parents[1]
SCALE_ROOT = ROOT / "tests/fixtures/vnext_scale/case_10_5a_plus_5b"


def _scale_assessment(state):
    spec = importlib.util.spec_from_file_location(
        "scale_tests", ROOT / "tests/test_vnext_next_step_and_scale_fixture.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._scale_assessment(state)


def _compiler_input():
    return _compiler_module()._input()


def _compiler_module():
    spec = importlib.util.spec_from_file_location(
        "compiler_tests", ROOT / "tests/test_vnext_semantic_compiler.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_proposition_as_alternative_is_rejected_with_actionable_retry_contract() -> None:
    run_input = _compiler_input()
    base = _compiler_module()._assessment()
    invalid = base.model_copy(update={
        "semantic_items": [
            item for item in base.semantic_items if item.local_ref != "alt_a"
        ] + [
            PropositionItem(local_ref="prop-alt", kind="proposition", statement="Permitted prior study explains the pattern.", about_subject_ids=["subject_A"], basis_item_refs=["e_a"]),
        ],
        "subject_assessments": [
            base.subject_assessments[0].model_copy(update={
                "violation_assessments": [base.subject_assessments[0].violation_assessments[0].model_copy(update={"alternative_item_refs": ["prop-alt"]})]
            }),
            base.subject_assessments[1],
        ],
    })
    with pytest.raises(SemanticValidationError) as caught:
        compile_semantic_assessment(invalid, run_input)
    message = str(caught.value)
    assert "kind=proposition" in message
    assert "hypothesis items only" in message
    assert "alternative_item_refs" in message
    assert "do not reuse a proposition ref" in message
    assert "create a separate hypothesis" in (caught.value.retry_constraint or "")
    retry_constraints = VNextProductionRunner._semantic_validation_retry_constraints(caught.value)
    assert any("hypothesis refs only" in constraint for constraint in retry_constraints)
    assert any("Rebuild the complete semantic assessment cleanly" in constraint for constraint in retry_constraints)

    corrected = invalid.model_copy(update={
        "semantic_items": [item for item in invalid.semantic_items if item.local_ref != "prop-alt"] + [
            HypothesisItem(local_ref="hyp-alt", kind="hypothesis", statement="Permitted prior study explains the pattern.", about_subject_ids=["subject_A"])
        ],
        "subject_assessments": [
            invalid.subject_assessments[0].model_copy(update={
                "violation_assessments": [invalid.subject_assessments[0].violation_assessments[0].model_copy(update={"alternative_item_refs": ["hyp-alt"]})]
            }),
            invalid.subject_assessments[1],
        ],
    })
    compiled = compile_semantic_assessment(corrected, run_input)
    result = VNextInvestigationRunner(lambda _: compiled).run(run_input)
    state = CaseState(case_id=run_input.case_id, title="Compiler", assessment_context=run_input.assessment_context, subjects=run_input.subjects, subject_relationships=run_input.subject_relationships, sources=run_input.sources)
    report = build_report_record(build_input_snapshot(state, run_input.rule_preset, "offline"), result, completed_at="offline")
    assert result.status.value == "completed"
    assert report["students"]


def test_ten_candidate_proposition_alternative_failure_and_hypothesis_replay() -> None:
    state = load_scale_fixture_case_state(SCALE_ROOT)
    run_input = run_input_from_case_state(state, academic_integrity_core_preset())
    base = _scale_assessment(state)
    proposition = PropositionItem(local_ref="prop-f-similarity", kind="proposition", statement="Permitted prior study may explain Candidate F's similarity.", about_subject_ids=["subject_F"], basis_item_refs=["e_f_marker"])
    f_assessment = next(item for item in base.subject_assessments if item.subject_id == "subject_F")
    f_violation = f_assessment.violation_assessments[0].model_copy(update={"status": AssessmentStatus.PARTIALLY_SUPPORTED, "supporting_item_refs": ["e_f_marker"], "alternative_item_refs": ["prop-f-similarity"]})
    invalid = base.model_copy(update={"semantic_items": [*base.semantic_items, proposition], "subject_assessments": [f_assessment.model_copy(update={"violation_assessments": [f_violation, *f_assessment.violation_assessments[1:]]}) if item.subject_id == "subject_F" else item for item in base.subject_assessments]})
    with pytest.raises(SemanticValidationError, match="kind=proposition"):
        compile_semantic_assessment(invalid, run_input)

    hypothesis = HypothesisItem(local_ref="hyp-f-similarity", kind="hypothesis", statement=proposition.statement, about_subject_ids=["subject_F"])
    corrected = invalid.model_copy(update={"semantic_items": [item for item in invalid.semantic_items if item.local_ref != proposition.local_ref] + [hypothesis], "subject_assessments": [item.model_copy(update={"violation_assessments": [violation.model_copy(update={"alternative_item_refs": ["hyp-f-similarity"]}) for violation in item.violation_assessments]}) if item.subject_id == "subject_F" else item for item in invalid.subject_assessments]})
    compiled = compile_semantic_assessment(corrected, run_input)
    result = VNextInvestigationRunner(lambda _: compiled).run(run_input)
    assert len(result.subject_assessments) == 10
    assert sum(len(item.violation_assessments) for item in result.subject_assessments) == 40


def test_private_f_and_g_evidence_cannot_form_one_joint_proposition() -> None:
    state = load_scale_fixture_case_state(SCALE_ROOT)
    run_input = run_input_from_case_state(state, academic_integrity_core_preset())
    base = _scale_assessment(state)
    invalid = base.model_copy(update={"semantic_items": [*base.semantic_items, PropositionItem(local_ref="prop-fg", kind="proposition", statement="Joint private explanation.", about_subject_ids=["subject_F", "subject_G"], basis_item_refs=["e_f_marker", "e_g_marker"])]})
    with pytest.raises(SemanticValidationError, match="incompatible scopes") as caught:
        compile_semantic_assessment(invalid, run_input)
    assert "subject_F" in str(caught.value) and "subject_G" in str(caught.value)


def test_prompt_exposes_hypothesis_only_alternative_contract_and_retry_text() -> None:
    run_input = _compiler_input()
    prompt = build_prompt(run_input)
    assert "alternative_item_refs MUST reference semantic items whose kind is hypothesis" in prompt
    assert "Do not place proposition or evidence_statement refs" in prompt
    retry = run_input.model_copy(update={"retry_constraints": ["IMPORTANT CORRECTION: alternative_item_refs accepts hypothesis refs only. Create a separate hypothesis item and reference the hypothesis. Rebuild the complete semantic assessment cleanly."]})
    retry_prompt = build_prompt(retry)
    assert "hypothesis refs only" in retry_prompt
    assert "Create a separate hypothesis" in retry_prompt
    assert "Rebuild the complete semantic assessment cleanly" in retry_prompt
