"""Offline blind validation of the model-facing semantic IR.

The assessments in this module are deliberately hand-authored from public
fixture material.  Evaluator-only files are read only after all blind runs
have completed successfully in the comparison test.
"""

from __future__ import annotations

import json
from pathlib import Path
import re

import pytest
from pydantic import ValidationError

from investigator.graph import GraphScope, GraphScopeType
from investigator.models import AssessmentContext, AssessmentSubject, Source, SourceType, SubjectRelationship
from investigator.reporting import build_input_snapshot, build_report_record
from investigator.state import CaseState
from investigator.vnext import (
    AssessmentStatus,
    AssessmentRulePreset,
    Confidence,
    FurthestJustifiedConclusion,
    InvestigatorAssessment,
    VNextInvestigationRunner,
    ViolationAssessment,
    run_input_from_case_state,
)
from investigator.vnext.model import build_prompt
from investigator.vnext.presets import academic_integrity_core_preset
from investigator.vnext.semantic import (
    EvidenceStatementItem,
    HypothesisItem,
    InvestigatorSemanticAssessment,
    PropositionItem,
    SemanticItemKind,
    SemanticSubjectAssessment,
    SemanticValidationError,
    SemanticViolationAssessment,
    build_semantic_symbol_table,
    compile_semantic_assessment,
)


ROOT = Path(__file__).resolve().parents[1]
MULTI_FIXTURES = ROOT / "tests" / "fixtures" / "vnext_multi_subject"
LAW_SOURCES = ROOT / "src" / "investigator" / "public_samples" / "law_exam" / "sources"
EVALUATOR_ONLY = MULTI_FIXTURES / "evaluator_only"


def _law_state() -> CaseState:
    sources = {}
    case_scope = GraphScope(scope_type=GraphScopeType.CASE).model_dump(mode="json")
    for index, path in enumerate(sorted(LAW_SOURCES.glob("*.md")), start=1):
        sources[f"S{index}"] = Source(
            id=f"S{index}",
            name=path.name,
            source_type=SourceType.DOCUMENT,
            content=path.read_text(encoding="utf-8"),
            metadata={"filename": path.name, "assessment_scope": case_scope},
        )
    return CaseState(
        case_id="law-exam-offline-blind",
        title="Law Exam Investigation",
        assessment_context=AssessmentContext(
            assessment_id="law-exam-offline-blind-assessment",
            title="Business Law Individual In-Class Assessment 2",
            assessment_type="closed-notes individual assessment",
        ),
        subjects={"subject_A": AssessmentSubject(subject_id="subject_A", display_name="Candidate A", candidate_number="BL-041")},
        sources=sources,
    )


def _fixture_state(case_name: str) -> CaseState:
    path = MULTI_FIXTURES / case_name / "case_state.json"
    return CaseState.model_validate(json.loads(path.read_text(encoding="utf-8")))


def _source_id(state: CaseState, filename: str) -> str:
    return next(source.id for source in state.sources.values() if source.name == filename)


def _evidence(local_ref: str, source_id: str, statement: str) -> EvidenceStatementItem:
    return EvidenceStatementItem(local_ref=local_ref, kind="evidence_statement", statement=statement, basis_source_ids=[source_id])


def _proposition(local_ref: str, subject_ids: list[str], basis_refs: list[str], statement: str) -> PropositionItem:
    return PropositionItem(local_ref=local_ref, kind="proposition", statement=statement, about_subject_ids=subject_ids, basis_item_refs=basis_refs)


def _hypothesis(local_ref: str, subject_ids: list[str], statement: str) -> HypothesisItem:
    return HypothesisItem(local_ref=local_ref, kind="hypothesis", statement=statement, about_subject_ids=subject_ids)


def _row(
    violation_id: str,
    *,
    status: AssessmentStatus = AssessmentStatus.NOT_CURRENTLY_SUPPORTED,
    confidence: Confidence | None = None,
    supporting: list[str] | None = None,
    conflicting: list[str] | None = None,
    limiting: list[str] | None = None,
    alternatives: list[str] | None = None,
    unresolved: list[str] | None = None,
) -> SemanticViolationAssessment:
    return SemanticViolationAssessment(
        violation_id=violation_id,
        status=status,
        supporting_item_refs=supporting or [],
        conflicting_item_refs=conflicting or [],
        limiting_item_refs=limiting or [],
        alternative_item_refs=alternatives or [],
        unresolved_points=unresolved or [],
        reasoning_summary="The available record supports a bounded assessment without treating opportunity or association as proof.",
        confidence=confidence or (Confidence.LOW if status is AssessmentStatus.NOT_CURRENTLY_SUPPORTED else Confidence.MODERATE),
    )


def _subject_assessment(subject_id: str, rows: list[SemanticViolationAssessment]) -> SemanticSubjectAssessment:
    return SemanticSubjectAssessment(
        subject_id=subject_id,
        violation_assessments=rows,
        furthest_conclusion=FurthestJustifiedConclusion(
            statement="The available record supports only a bounded assessment; final institutional judgment remains human.",
            confidence=Confidence.LOW,
        ),
    )


def _update_subject_a_first_violation(assessment: InvestigatorSemanticAssessment, **updates: object) -> InvestigatorSemanticAssessment:
    rows = list(assessment.subject_assessments[0].violation_assessments)
    rows[0] = rows[0].model_copy(update=updates)
    subjects = [assessment.subject_assessments[0].model_copy(update={"violation_assessments": rows}), *assessment.subject_assessments[1:]]
    return assessment.model_copy(update={"subject_assessments": subjects})


def _blind_assessment(state: CaseState, case_name: str) -> InvestigatorSemanticAssessment:
    preset = academic_integrity_core_preset()
    violation_ids = [item.violation_id for item in preset.violations]
    items: list[object] = []
    rows_by_subject: dict[str, list[SemanticViolationAssessment]] = {}

    if case_name == "law":
        entry = _source_id(state, "entry_permitted_items_record.md")
        device = _source_id(state, "device_examination_report.md")
        activity = _source_id(state, "smart_glasses_activity_record.md")
        service = _source_id(state, "external_ai_service_record.md")
        linkage = _source_id(state, "device_account_linkage_record.md")
        invigilator = _source_id(state, "invigilator_report.md")
        clarification = _source_id(state, "student_clarification_record.md")
        items = [
            _evidence("e_entry", entry, "The entry record describes prescription-style glasses and no separately observed electronic device."),
            _evidence("e_device", device, "The examined glasses contain connected-device capabilities and retained activity records."),
            _evidence("e_activity", activity, "The activity record shows three interactive sessions during the assessment window."),
            _evidence("e_service", service, "The external-service record contains three matching assistant sessions."),
            _evidence("e_linkage", linkage, "The linkage record matches device sessions, service sessions, and the companion account."),
            _evidence("e_invig", invigilator, "The invigilator observed repeated temple touches but could not identify a device or confirm use."),
            _evidence("e_clarification", clarification, "The student disputes that the examined device was the pair worn during the assessment."),
            _proposition("p_device", ["subject_A"], ["e_entry", "e_device", "e_activity"], "The record supports that a capable pair of glasses existed and was active during the window, but attribution to the worn pair remains contested."),
            _proposition("p_service", ["subject_A"], ["p_device", "e_service", "e_linkage"], "The device, account, and external-service records are technically linked, without independently proving who supplied the inputs."),
            _hypothesis("h_backup_glasses", ["subject_A"], "The student wore a non-electronic backup pair while the examined device belonged elsewhere."),
        ]
        rows_by_subject["subject_A"] = [
            _row(violation_ids[0], status=AssessmentStatus.PARTIALLY_SUPPORTED, supporting=["p_device"], limiting=["e_clarification"], alternatives=["h_backup_glasses"], unresolved=["Whether the examined device was the pair worn by Candidate A during the assessment."]),
            _row(violation_ids[1], supporting=["p_service"], limiting=["e_invig"], alternatives=["h_backup_glasses"], unresolved=["Whether the linked activity reflects Candidate A's own use rather than device or account attribution alone."]),
            _row(violation_ids[2], supporting=["p_service"], limiting=["e_clarification"], alternatives=["h_backup_glasses"], unresolved=["Whether the external-service activity was actually initiated or relied upon by Candidate A."]),
            _row(violation_ids[3], limiting=["e_invig"], alternatives=["h_backup_glasses"], unresolved=["Whether any observed conduct involved another person or only adjustment of ordinary glasses."]),
        ]
    else:
        subjects = sorted(state.subjects)
        marker_refs: dict[str, str] = {}
        invigilator_refs: dict[str, str] = {}
        for subject_id in subjects:
            letter = subject_id.rsplit("_", 1)[-1]
            marker_id = _source_id(state, f"Candidate {letter} marker report")
            invig_id = _source_id(state, f"Candidate {letter} invigilator report")
            marker_ref = f"e_{letter.lower()}_marker"
            invig_ref = f"e_{letter.lower()}_invig"
            prop_ref = f"p_{letter.lower()}_script"
            items.extend([
                _evidence(marker_ref, marker_id, f"The marker record describes Candidate {letter}'s script independently."),
                _evidence(invig_ref, invig_id, f"The invigilator record describes Candidate {letter}'s observed behavior independently."),
                _proposition(prop_ref, [subject_id], ["e_context", marker_ref], f"The case rules and Candidate {letter}'s own marker record support a subject-scoped script observation."),
            ])
            marker_refs[subject_id] = prop_ref
            invigilator_refs[subject_id] = invig_ref
            rows_by_subject[subject_id] = [
                _row(
                    violation_ids[0],
                    status=AssessmentStatus.SUPPORTED,
                    supporting=[invig_ref],
                    confidence=Confidence.HIGH,
                ) if subject_id == "subject_E" else _row(violation_ids[0], limiting=[invig_ref]),
                _row(violation_ids[1], limiting=[invig_ref], unresolved=["Whether the recorded behavior had any communicative content."] if subject_id in {"subject_A", "subject_B"} else []),
                _row(violation_ids[2], supporting=[prop_ref] if case_name in {"case_5a_combined", "case_5b_marker_only"} and subject_id in {"subject_A", "subject_B"} else [], limiting=[invig_ref]),
                _row(
                    violation_ids[3],
                    status=AssessmentStatus.PARTIALLY_SUPPORTED if case_name == "case_5a_combined" and subject_id in {"subject_A", "subject_B"} else AssessmentStatus.NOT_CURRENTLY_SUPPORTED,
                    supporting=[prop_ref] if case_name in {"case_5a_combined", "case_5b_marker_only"} and subject_id in {"subject_A", "subject_B"} else [],
                    limiting=[invig_ref],
                    alternatives=["h_ab_alternative"] if subject_id in {"subject_A", "subject_B"} and case_name == "case_5a_combined" else [],
                    unresolved=["Whether separate observations can be connected to prohibited collaboration rather than proximity or independent work."] if subject_id in {"subject_A", "subject_B"} else [],
                ),
            ]
        context_id = _source_id(state, "Assessment rules")
        items.insert(0, _evidence("e_context", context_id, "The assessment rules record the individual assessment setting and applicable conduct boundaries."))
        if case_name == "case_5a_combined":
            items.append(_hypothesis("h_ab_alternative", ["subject_A", "subject_B"], "The adjacent candidates' observations may reflect ordinary proximity or independent work rather than collaboration."))

    return InvestigatorSemanticAssessment(
        semantic_items=items,
        subject_assessments=[_subject_assessment(subject_id, rows_by_subject[subject_id]) for subject_id in state.subjects],
    )


def _run_blind(case_name: str) -> tuple[CaseState, object, dict[str, object], str]:
    if case_name == "law":
        state = _law_state()
    else:
        state = _fixture_state(case_name)
    preset = academic_integrity_core_preset()
    run_input = run_input_from_case_state(state, preset)
    assessment = _blind_assessment(state, case_name)
    validated = InvestigatorSemanticAssessment.model_validate(assessment.model_dump(mode="python"))
    compiled = compile_semantic_assessment(validated, run_input)
    result = VNextInvestigationRunner(lambda _: compiled).run(run_input)
    snapshot = build_input_snapshot(state, preset, f"offline-{case_name}")
    report = build_report_record(snapshot, result, completed_at="offline")
    return state, result, report, build_prompt(run_input)


def _assert_report(report: dict[str, object], state: CaseState) -> None:
    students = report["students"]
    assert [item["subject_id"] for item in students] == list(state.subjects)
    expected = [item.violation_id for item in academic_integrity_core_preset().violations]
    for student in students:
        assert [item["violation_id"] for item in student["violations"]] == expected
        assert "alternative_explanations" in student
        for finding in student["violations"]:
            assert {"supporting_material", "conflicting_material", "limiting_material", "unresolved_points"} <= set(finding)
    serialized = json.dumps(report)
    assert not re.search(r"\b(?:E|P|H|U)\d+(?:\.\d+)?\b", serialized)
    assert "Candidate" in serialized or "Student" in serialized
    all_findings = [finding for student in students for finding in student["violations"]]
    assert any(finding["limiting_material"] for finding in all_findings)
    assert any(finding["unresolved_points"] for finding in all_findings)
    assert "Final institutional judgment remains human." in (ROOT / "src" / "investigator" / "help" / "product_guide.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("case_name", ["law", "case_5a_combined", "case_5b_marker_only", "case_5c_invigilator_only"])
def test_blind_case_passes_complete_pipeline(case_name: str) -> None:
    state, result, report, prompt = _run_blind(case_name)
    _assert_report(report, state)
    assert result.graph.nodes
    assert "semantic_items" in prompt
    assert all(path.name not in prompt for path in EVALUATOR_ONLY.iterdir())
    if case_name != "law":
        assert not any("evaluator_only" in source.name for source in state.sources.values())


def test_blind_inputs_exclude_evaluator_only_material() -> None:
    for case_name in ("case_5a_combined", "case_5b_marker_only", "case_5c_invigilator_only"):
        state = _fixture_state(case_name)
        run_input = run_input_from_case_state(state, academic_integrity_core_preset())
        prompt = build_prompt(run_input)
        assert not any("hidden_ground_truth" in source.name or "expected_comparison" in source.name for source in run_input.sources.values())
        assert "hidden_ground_truth" not in prompt
        assert "expected_comparison" not in prompt


@pytest.mark.parametrize("case_name", ["case_5a_combined", "case_5b_marker_only", "case_5c_invigilator_only"])
def test_candidate_e_is_a_bounded_device_positive_control(case_name: str) -> None:
    state, result, _, _ = _run_blind(case_name)
    e_assessment = next(item for item in result.subject_assessments if item.subject_id == "subject_E")
    by_violation = {item.violation_id: item for item in e_assessment.violation_assessments}
    device = by_violation["unauthorized_device"]
    assert device.status is AssessmentStatus.SUPPORTED
    assert device.confidence is Confidence.HIGH
    assert device.supporting_node_ids
    assert all(
        by_violation[violation_id].status is AssessmentStatus.NOT_CURRENTLY_SUPPORTED
        for violation_id in ("unauthorized_external_communication", "unauthorized_assistance", "prohibited_collaboration")
    )
    assert "smartphone" in next(source.content for source in state.sources.values() if source.name == "Candidate E invigilator report")


def test_case_5a_collaboration_is_at_least_partial_for_a_and_b() -> None:
    _, result, _, _ = _run_blind("case_5a_combined")
    allowed = {AssessmentStatus.PARTIALLY_SUPPORTED, AssessmentStatus.SUPPORTED}
    for subject_id in ("subject_A", "subject_B"):
        subject = next(item for item in result.subject_assessments if item.subject_id == subject_id)
        collaboration = next(item for item in subject.violation_assessments if item.violation_id == "prohibited_collaboration")
        assert collaboration.status in allowed


def test_candidate_e_device_source_is_identical_across_variants_and_public_sample_has_14_files() -> None:
    variants = ("case_5a_combined", "case_5b_marker_only", "case_5c_invigilator_only")
    contents = [
        (MULTI_FIXTURES / variant / "sources" / "candidate_E_invigilator_report.md").read_text(encoding="utf-8")
        for variant in variants
    ]
    assert len(set(contents)) == 1
    assert "No device or note was seen" not in contents[0]
    assert "surrender the device" in contents[0]
    assert len(list((ROOT / "tests" / "fixtures" / "public_samples" / "multi_candidate" / "sources").glob("*"))) == 14


def test_adversarial_schema_boundaries() -> None:
    with pytest.raises(ValidationError):
        EvidenceStatementItem.model_validate({"local_ref": "e", "kind": "evidence_statement", "statement": "x", "basis_item_refs": ["p"]})
    with pytest.raises(ValidationError):
        PropositionItem.model_validate({"local_ref": "p", "kind": "proposition", "statement": "x", "about_subject_ids": ["subject_A"], "basis_source_ids": ["S1"], "basis_item_refs": ["e"]})
    with pytest.raises(ValidationError):
        InvestigatorSemanticAssessment.model_validate({"semantic_items": [{"local_ref": "u", "kind": "uncertainty", "statement": "x"}], "subject_assessments": []})


def test_adversarial_scope_and_reference_boundaries() -> None:
    state = _fixture_state("case_5a_combined")
    run_input = run_input_from_case_state(state, academic_integrity_core_preset())
    base = _blind_assessment(state, "case_5a_combined")
    with pytest.raises(SemanticValidationError):
        compile_semantic_assessment(base.model_copy(update={"semantic_items": base.semantic_items + [_proposition("p_private_joint", ["subject_A", "subject_B"], ["e_a_marker", "e_b_marker"], "An illegal joint proposition.")]}), run_input)
    valid_case_subject = _proposition("p_case_subject", ["subject_A"], ["e_context", "e_a_marker"], "A valid narrowed proposition.")
    valid = base.model_copy(update={"semantic_items": base.semantic_items + [valid_case_subject]})
    compile_semantic_assessment(valid, run_input)
    valid_hypothesis = _hypothesis("h_valid_joint", ["subject_A", "subject_B"], "A relationship-level alternative.")
    compile_semantic_assessment(base.model_copy(update={"semantic_items": base.semantic_items + [valid_hypothesis]}), run_input)
    with pytest.raises(SemanticValidationError):
        compile_semantic_assessment(base.model_copy(update={"semantic_items": base.semantic_items + [_hypothesis("h_missing_relationship", ["subject_A", "subject_C"], "No existing relationship.")]}), run_input)

    duplicate = base.model_copy(update={"semantic_items": base.semantic_items + [base.semantic_items[0].model_copy(update={"local_ref": base.semantic_items[1].local_ref})]})
    with pytest.raises(SemanticValidationError, match=r"semantic_items\[1\].*semantic_items\[17\]"):
        compile_semantic_assessment(duplicate, run_input)
    unknown = _update_subject_a_first_violation(base, limiting_item_refs=["missing_ref"])
    with pytest.raises(SemanticValidationError, match=r"limiting_item_refs\[0\]"):
        compile_semantic_assessment(unknown, run_input)
    wrong_kind = _update_subject_a_first_violation(base, alternative_item_refs=["e_a_marker"])
    with pytest.raises(SemanticValidationError, match="Actual kind: evidence_statement.*Required kind: hypothesis"):
        compile_semantic_assessment(wrong_kind, run_input)


def test_adversarial_status_order_and_deduplication() -> None:
    state = _fixture_state("case_5a_combined")
    run_input = run_input_from_case_state(state, academic_integrity_core_preset())
    base = _blind_assessment(state, "case_5a_combined")
    reordered = base.model_copy(update={"subject_assessments": [subject.model_copy(update={"violation_assessments": list(reversed(subject.violation_assessments))}) for subject in base.subject_assessments]})
    compiled = compile_semantic_assessment(reordered, run_input)
    assert [item.violation_id for item in compiled.subject_assessments[0].violation_assessments] == [item.violation_id for item in run_input.rule_preset.violations]

    duplicate_preset = AssessmentRulePreset.model_construct(preset_id="duplicate", violations=[run_input.rule_preset.violations[0], run_input.rule_preset.violations[0]])
    with pytest.raises(SemanticValidationError, match="duplicate violation"):
        compile_semantic_assessment(base, run_input, preset=duplicate_preset)

    subject_a_rows = list(base.subject_assessments[0].violation_assessments)
    subject_a_rows[0] = subject_a_rows[0].model_copy(update={"supporting_item_refs": ["p_a_script", "p_a_script"]})
    duplicate_refs = base.model_copy(update={"subject_assessments": [base.subject_assessments[0].model_copy(update={"violation_assessments": subject_a_rows}), *base.subject_assessments[1:]]})
    deduped = compile_semantic_assessment(duplicate_refs, run_input)
    assert len(deduped.subject_assessments[0].violation_assessments[0].supporting_node_ids) == 1

    conflicted = _update_subject_a_first_violation(base, status=AssessmentStatus.CONFLICTED, supporting_item_refs=["p_a_script"], conflicting_item_refs=[])
    with pytest.raises(SemanticValidationError, match="requires conflicting material"):
        compile_semantic_assessment(conflicted, run_input)


def test_adversarial_scope_stickiness_and_unresolved_points() -> None:
    state = _fixture_state("case_5a_combined")
    run_input = run_input_from_case_state(state, academic_integrity_core_preset())
    base = _blind_assessment(state, "case_5a_combined")
    a_marker = _source_id(state, "Candidate A marker report")
    b_marker = _source_id(state, "Candidate B marker report")
    mixed = base.model_copy(update={"semantic_items": base.semantic_items + [_evidence("e_mixed", a_marker, "A record mentions Candidate B, but remains A-scoped.") .model_copy(update={"basis_source_ids": [a_marker, b_marker]})]})
    with pytest.raises(SemanticValidationError, match="Split this into separate semantic items"):
        compile_semantic_assessment(mixed, run_input)
    mention = base.model_copy(update={"semantic_items": base.semantic_items + [_evidence("e_mention", a_marker, "The A-scoped record mentions Candidate B.")]})
    compiled = compile_semantic_assessment(mention, run_input)
    mention_update = next(item for item in compiled.proposal.graph_updates if getattr(item, "local_ref", None) == "e_mention")
    assert mention_update.scope.scope_type is GraphScopeType.SUBJECT
    assert mention_update.scope.subject_id == "subject_A"

    unresolved_rows = list(base.subject_assessments[0].violation_assessments)
    unresolved_rows[0] = unresolved_rows[0].model_copy(update={"unresolved_points": ["Whether the observation had communicative content."]})
    unresolved = base.model_copy(update={"subject_assessments": [base.subject_assessments[0].model_copy(update={"violation_assessments": unresolved_rows}), *base.subject_assessments[1:]]})
    compiled = compile_semantic_assessment(unresolved, run_input)
    uncertainty = next(item for item in compiled.proposal.graph_updates if getattr(item, "operation", None) == "add_uncertainty")
    assert uncertainty.target_node_id == "evaluation_subject_a_unauthorized_device"


def test_evaluator_comparison_is_only_read_after_all_blind_cases_pass() -> None:
    results = {name: _run_blind(name) for name in ("law", "case_5a_combined", "case_5b_marker_only", "case_5c_invigilator_only")}
    assert all(result[1].subject_assessments for result in results.values())
    comparison = (EVALUATOR_ONLY / "expected_comparison.md").read_text(encoding="utf-8")
    hidden = json.loads((EVALUATOR_ONLY / "hidden_ground_truth.json").read_text(encoding="utf-8"))
    assert comparison and hidden
    assert "case-5a" in comparison and "case-5b" in comparison and "case-5c" in comparison
