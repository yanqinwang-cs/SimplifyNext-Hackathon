from __future__ import annotations

import json
from pathlib import Path

import pytest

from investigator.graph import GraphScopeType
from investigator.models import AssessmentContext, AssessmentSubject, Source, SourceType, SubjectRelationship
from investigator.reporting import build_input_snapshot, build_report_record, public_report_from_record
from investigator.state import CaseState
from investigator.vnext import (
    AssessmentStatus,
    Confidence,
    FurthestJustifiedConclusion,
    VNextInvestigationRunner,
    run_input_from_case_state,
)
from investigator.vnext.model import build_prompt
from investigator.vnext.presets import academic_integrity_core_preset
from investigator.vnext.semantic import EvidenceStatementItem, InvestigatorSemanticAssessment, SemanticSubjectAssessment, SemanticValidationError, SemanticViolationAssessment, compile_semantic_assessment


ROOT = Path(__file__).resolve().parents[1]
SCALE_ROOT = ROOT / "tests" / "fixtures" / "vnext_scale" / "case_10_5a_plus_5b"


def _scale_state() -> CaseState:
    manifest = json.loads((SCALE_ROOT / "manifest.json").read_text(encoding="utf-8"))
    sources: dict[str, Source] = {}
    filenames = sorted(path.name for path in (SCALE_ROOT / "sources").glob("*.md"))
    source_ids = {filename: f"S{index:03d}" for index, filename in enumerate(filenames, start=1)}
    shared = set(manifest["shared_files"])
    for filename in filenames:
        path = SCALE_ROOT / "sources" / filename
        stem = path.stem.replace("_", " ")
        if filename in shared:
            name = stem.title()
            scope = {"scope_type": "case", "subject_id": None, "relationship_id": None}
        else:
            letter = filename.split("_")[1]
            name = f"Candidate {letter} {filename.split('_', 2)[2].removesuffix('.md').replace('_', ' ')}"
            scope = {"scope_type": "subject", "subject_id": f"subject_{letter}", "relationship_id": None}
        sources[source_ids[filename]] = Source(
            id=source_ids[filename],
            name=name,
            source_type=SourceType.DOCUMENT,
            content=path.read_text(encoding="utf-8"),
            metadata={"filename": filename, "assessment_scope": scope},
        )
    subjects = {
        f"subject_{letter}": AssessmentSubject(
            subject_id=f"subject_{letter}",
            display_name=f"Candidate {letter}",
            candidate_number=details["candidate_number"],
        )
        for letter, details in manifest["candidates"].items()
    }
    relationships = {
        item["relationship_id"]: SubjectRelationship(
            relationship_id=item["relationship_id"],
            subject_ids=item["subject_ids"],
            relationship_type=item["relationship_type"],
            source_ids=[source_ids[item["source_file"]]],
            description=f"{item['subject_ids'][0]} and {item['subject_ids'][1]} are adjacent.",
        )
        for item in manifest["relationships"]
    }
    return CaseState(
        case_id=manifest["case_id"],
        title=manifest["title"],
        description="Controlled ten-candidate validation fixture.",
        assessment_rule_preset_id=manifest["assessment_rule_preset_id"],
        assessment_context=AssessmentContext(assessment_id="BL-ICA2-2026-SCALE", title="Business Law Ten Candidate Scale", assessment_type="closed-notes individual assessment", venue="Seminar Room 4"),
        subjects=subjects,
        subject_relationships=relationships,
        sources=sources,
    )


def _source_id(state: CaseState, filename: str) -> str:
    return next(source.id for source in state.sources.values() if source.metadata.get("filename") == filename)


def _evidence(local_ref: str, source_id: str, statement: str) -> EvidenceStatementItem:
    return EvidenceStatementItem(local_ref=local_ref, kind="evidence_statement", statement=statement, basis_source_ids=[source_id])


def _scale_assessment(state: CaseState) -> InvestigatorSemanticAssessment:
    items: list[object] = []
    subjects: list[SemanticSubjectAssessment] = []
    for letter in "ABCDEFGHIJ":
        subject_id = f"subject_{letter}"
        marker = _source_id(state, f"candidate_{letter}_marker_report.md")
        invigilator = _source_id(state, f"candidate_{letter}_invigilator_report.md")
        marker_ref = f"e_{letter.lower()}_marker"
        invig_ref = f"e_{letter.lower()}_invig"
        items.extend([
            _evidence(marker_ref, marker, f"Candidate {letter}'s marker record."),
            _evidence(invig_ref, invigilator, f"Candidate {letter}'s invigilator record."),
        ])
        next_step = {
            "A": "Review existing contemporaneous invigilation records or proceed on the current record.",
            "B": "Review existing contemporaneous invigilation records or proceed on the current record.",
            "C": "No further enquiry is currently justified.",
            "D": "No further enquiry is currently justified.",
            "E": "Proceed to human institutional review for device possession; assess other violations separately.",
            "F": "Review existing records if they can distinguish prior-study similarity from during-assessment conduct; otherwise preserve the weaker assessment.",
            "G": "Review existing records if they can distinguish prior-study similarity from during-assessment conduct; otherwise preserve the weaker assessment.",
            "H": "No further enquiry is currently justified.",
            "I": "No further enquiry is currently justified.",
            "J": "Proceed to human institutional review for device possession; assess other violations separately.",
        }[letter]
        rows: list[SemanticViolationAssessment] = []
        for violation in academic_integrity_core_preset().violations:
            status = AssessmentStatus.NOT_CURRENTLY_SUPPORTED
            confidence = Confidence.LOW
            supporting: list[str] = []
            if violation.violation_id == "prohibited_collaboration" and letter in "AB":
                status, confidence, supporting = AssessmentStatus.PARTIALLY_SUPPORTED, Confidence.MODERATE, [marker_ref, invig_ref]
            elif violation.violation_id == "prohibited_collaboration" and letter in "FG":
                supporting = [marker_ref]
            elif violation.violation_id == "unauthorized_device" and letter in "EJ":
                status, confidence, supporting = AssessmentStatus.SUPPORTED, Confidence.HIGH, [invig_ref]
            rows.append(SemanticViolationAssessment(
                violation_id=violation.violation_id,
                status=status,
                supporting_item_refs=supporting,
                limiting_item_refs=[invig_ref] if not supporting else [],
                reasoning_summary=f"Bounded assessment for Candidate {letter}.",
                confidence=confidence,
            ))
        subjects.append(SemanticSubjectAssessment(
            subject_id=subject_id,
            violation_assessments=rows,
            furthest_conclusion=FurthestJustifiedConclusion(statement=f"Bounded assessment for Candidate {letter}.", confidence=Confidence.HIGH if letter in "EJ" else Confidence.MODERATE),
            suggested_next_step=next_step,
        ))
    return InvestigatorSemanticAssessment(semantic_items=items, subject_assessments=subjects)


def _run_scale() -> tuple[CaseState, object, dict[str, object], str]:
    state = _scale_state()
    run_input = run_input_from_case_state(state, academic_integrity_core_preset())
    assessment = _scale_assessment(state)
    compiled = compile_semantic_assessment(assessment, run_input)
    result = VNextInvestigationRunner(lambda _: compiled).run(run_input)
    snapshot = build_input_snapshot(state, academic_integrity_core_preset(), "offline-scale")
    report = build_report_record(snapshot, result, completed_at="offline")
    return state, result, report, build_prompt(run_input)


def test_suggested_next_step_survives_compiler_warden_and_report_without_graph_material() -> None:
    state, result, report, _ = _run_scale()
    for assessment in result.subject_assessments:
        assert assessment.suggested_next_step
    graph_text = json.dumps(result.graph.model_dump(mode="json"))
    assert "Suggested next step" not in graph_text
    assert all("suggested_next_step" not in node.model_dump(mode="json") for node in result.graph.nodes.values())
    assert all(student["suggested_next_step"] for student in report["students"])
    public = public_report_from_record(report, current_case_name=state.title, report_state="current", is_latest_successful_assessment=True, run_handle="offline-scale")
    serialized = json.dumps(public)
    assert serialized.count("Suggested next step") == 0
    assert all(student.get("suggestedNextStep") for student in public["assessment"]["students"])


def test_omitted_or_free_form_next_step_does_not_affect_assessment() -> None:
    state = _scale_state()
    run_input = run_input_from_case_state(state, academic_integrity_core_preset())
    assessment = _scale_assessment(state)
    omitted_payload = assessment.model_dump(mode="python")
    for item in omitted_payload["subject_assessments"]:
        item.pop("suggested_next_step", None)
    omitted = InvestigatorSemanticAssessment.model_validate(omitted_payload)
    compiled = compile_semantic_assessment(omitted, run_input)
    assert all(item.suggested_next_step is None for item in compiled.subject_assessments)
    free_form_first = assessment.subject_assessments[0].model_copy(update={"suggested_next_step": "Keep the current record and ask the institution to review it."})
    free_form = assessment.model_copy(update={"subject_assessments": [free_form_first, *assessment.subject_assessments[1:]]})
    assert compile_semantic_assessment(free_form, run_input).subject_assessments[0].suggested_next_step.startswith("Keep")


def test_scale_fixture_has_exact_coverage_and_two_relationships() -> None:
    state, result, report, prompt = _run_scale()
    assert len(state.subjects) == 10
    assert len(state.sources) == 24
    assert len(state.subject_relationships) == 2
    assert set(state.subject_relationships) == {"rel_A_B_adjacent", "rel_F_G_adjacent"}
    assert len(result.subject_assessments) == 10
    assert sum(len(item.violation_assessments) for item in result.subject_assessments) == 40
    assert len({item.subject_id for item in result.subject_assessments}) == 10
    assert all(len({item.violation_id for item in assessment.violation_assessments}) == 4 for assessment in result.subject_assessments)
    assert len(prompt) > 0
    assert len(prompt) > 5000
    assert all(f"Candidate {letter}" in prompt for letter in "ABCDEFGHIJ")
    assert len(report["students"]) == 10


def test_scale_fixture_positive_and_ablation_roles() -> None:
    _, result, _, _ = _run_scale()
    by_subject = {item.subject_id: {violation.violation_id: violation for violation in item.violation_assessments} for item in result.subject_assessments}
    assert all(by_subject[f"subject_{letter}"]["prohibited_collaboration"].status is AssessmentStatus.PARTIALLY_SUPPORTED for letter in "AB")
    assert all(by_subject[f"subject_{letter}"]["prohibited_collaboration"].status is AssessmentStatus.NOT_CURRENTLY_SUPPORTED for letter in "FG")
    for letter in "EJ":
        assert by_subject[f"subject_{letter}"]["unauthorized_device"].status is AssessmentStatus.SUPPORTED
        assert by_subject[f"subject_{letter}"]["unauthorized_device"].confidence is Confidence.HIGH
        assert all(by_subject[f"subject_{letter}"][violation].status is AssessmentStatus.NOT_CURRENTLY_SUPPORTED for violation in ("unauthorized_external_communication", "unauthorized_assistance", "prohibited_collaboration"))
    for letter in "CDHI":
        assert all(item.status is AssessmentStatus.NOT_CURRENTLY_SUPPORTED for item in by_subject[f"subject_{letter}"].values())


@pytest.mark.parametrize(("target", "source_letter"), [("subject_F", "A"), ("subject_A", "F"), ("subject_J", "E"), ("subject_E", "J")])
def test_private_cross_cohort_evidence_is_rejected(target: str, source_letter: str) -> None:
    state = _scale_state()
    run_input = run_input_from_case_state(state, academic_integrity_core_preset())
    base = _scale_assessment(state)
    source_id = _source_id(state, f"candidate_{source_letter}_marker_report.md")
    invalid_item = _evidence("e_cross", source_id, "Cross-cohort private evidence.")
    target_assessment = next(item for item in base.subject_assessments if item.subject_id == target)
    updated_rows = [target_assessment.violation_assessments[0].model_copy(update={"status": AssessmentStatus.SUPPORTED, "supporting_item_refs": ["e_cross"]}), *target_assessment.violation_assessments[1:]]
    invalid = base.model_copy(update={"semantic_items": [*base.semantic_items, invalid_item], "subject_assessments": [target_assessment.model_copy(update={"violation_assessments": updated_rows}) if item.subject_id == target else item for item in base.subject_assessments]})
    with pytest.raises(SemanticValidationError, match="legal scope"):
        compile_semantic_assessment(invalid, run_input)


def test_relationships_do_not_cross_cohorts_and_public_sample_stays_five_candidates() -> None:
    state = _scale_state()
    assert {tuple(item.subject_ids) for item in state.subject_relationships.values()} == {("subject_A", "subject_B"), ("subject_F", "subject_G")}
    public_sources = list((ROOT / "tests" / "fixtures" / "public_samples" / "multi_candidate" / "sources").glob("*"))
    assert len(public_sources) == 14
    assert not any(path.name.startswith("candidate_F") for path in public_sources)
    public_state = json.loads((ROOT / "tests" / "fixtures" / "public_samples" / "multi_candidate" / "manifest.json").read_text(encoding="utf-8"))
    assert len(public_state["visible_source_files"]) == 14
    assert public_state["subject_ids"] == ["subject_A", "subject_B", "subject_C", "subject_D", "subject_E"]
