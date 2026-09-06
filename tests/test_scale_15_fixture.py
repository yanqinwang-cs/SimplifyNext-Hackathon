from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from investigator.reporting import build_input_snapshot, build_report_record, public_report_from_record
from investigator.services.evidence_requests import HumanEvidenceWorkflow
from investigator.state import CaseRepository
from investigator.test_fixtures import load_scale_fixture_case_state
from investigator.vnext import (
    AssessmentStatus,
    Confidence,
    FurthestJustifiedConclusion,
    VNextInvestigationRunner,
    run_input_from_case_state,
)
from investigator.vnext.presets import academic_integrity_core_preset
from investigator.vnext.semantic import (
    EvidenceStatementItem,
    InvestigatorSemanticAssessment,
    PropositionItem,
    SemanticSubjectAssessment,
    SemanticViolationAssessment,
    compile_semantic_assessment,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/vnext_scale/case_15_5a_plus_5b_plus_5c"
SCRIPT = ROOT / "scripts/load_scale_fixture.py"


def fixture_state():
    return load_scale_fixture_case_state(FIXTURE)


def source_id(state, filename: str) -> str:
    return next(item.id for item in state.sources.values() if item.metadata.get("filename") == filename)


def test_fifteen_candidate_fixture_integrity_and_cohort_isolation() -> None:
    state = fixture_state()
    assert len(state.subjects) == 15
    assert {item.display_name for item in state.subjects.values()} == {f"Candidate {letter}" for letter in "ABCDEFGHIJKLMNO"}
    assert len(state.sources) == 42
    assert len({item.id for item in state.sources.values()}) == 42
    assert len(state.subject_relationships) == 3
    assert {tuple(item.subject_ids) for item in state.subject_relationships.values()} == {
        ("subject_A", "subject_B"), ("subject_F", "subject_G"), ("subject_K", "subject_L")
    }
    private = [item for item in state.sources.values() if item.metadata["assessment_scope"]["scope_type"] == "subject"]
    assert len(private) == 30
    for letter in "FGHIJKLMNO":
        files = [item for item in private if item.metadata["assessment_scope"]["subject_id"] == f"subject_{letter}"]
        assert len(files) == 2
        assert all(f"Candidate {letter}" in item.content for item in files)
        assert all(not any(f"Candidate {old}" in item.content or f"subject_{old}" in item.content for old in "ABCDE") for item in files)
    for letter in "EJO":
        content = next(item.content for item in state.sources.values() if item.metadata.get("filename") == f"candidate_{letter}_invigilator_report.md")
        assert "smartphone" in content and "screen was illuminated" in content and "device was retained" in content
    assert not any("evaluator_only" in json.dumps(item.model_dump(mode="json")).lower() for item in state.sources.values())


def test_importer_rewrites_case_id_and_replace_is_scoped(tmp_path: Path) -> None:
    repository = tmp_path / "cases"
    env = {"PYTHONPATH": str(ROOT / "src")}
    command = [sys.executable, str(SCRIPT), str(FIXTURE), "--repository", str(repository), "--case-id", "scale-15-working"]
    result = subprocess.run(command, cwd=ROOT, env=env, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    state = CaseRepository(repository).load("scale-15-working")
    assert state.case_id == "scale-15-working"
    assert (repository / "scale-15-working.json").is_file()
    assert state.case_kind == "user" and state.sample_id is None
    assert len(state.subjects) == 15 and len(state.sources) == 42
    assert "expected assessments: 60" in result.stdout
    rejected = subprocess.run(command, cwd=ROOT, env=env, text=True, capture_output=True)
    assert rejected.returncode != 0
    replaced = subprocess.run([*command, "--replace"], cwd=ROOT, env=env, text=True, capture_output=True)
    assert replaced.returncode == 0, replaced.stderr
    workflow = HumanEvidenceWorkflow(CaseRepository(repository), run_mode="vnext")
    run_id = workflow.begin_run("scale-15-working", start_revision=0)
    assert workflow.current_run_id("scale-15-working") == run_id
    assert (repository / "scale-15-working" / "runs" / run_id / "run_result.json").is_file()


def _offline_assessment(state) -> InvestigatorSemanticAssessment:
    items: list[EvidenceStatementItem] = []
    assessments: list[SemanticSubjectAssessment] = []
    preset = academic_integrity_core_preset()
    for letter in "ABCDEFGHIJKLMNO":
        subject_id = f"subject_{letter}"
        marker = source_id(state, f"candidate_{letter}_marker_report.md")
        invig = source_id(state, f"candidate_{letter}_invigilator_report.md")
        marker_ref, invig_ref = f"ev_{letter.lower()}_marker", f"ev_{letter.lower()}_invig"
        items.extend([
            EvidenceStatementItem(local_ref=marker_ref, kind="evidence_statement", statement=f"Candidate {letter}'s marker report.", basis_source_ids=[marker]),
            EvidenceStatementItem(local_ref=invig_ref, kind="evidence_statement", statement=f"Candidate {letter}'s invigilator report.", basis_source_ids=[invig]),
        ])
        rows = []
        for violation in preset.violations:
            status = AssessmentStatus.NOT_CURRENTLY_SUPPORTED
            refs: list[str] = []
            if violation.violation_id == "prohibited_collaboration" and letter in "AB":
                status, refs = AssessmentStatus.PARTIALLY_SUPPORTED, [marker_ref, invig_ref]
            if violation.violation_id == "unauthorized_device" and letter in "EJO":
                status, refs = AssessmentStatus.SUPPORTED, [invig_ref]
            rows.append(SemanticViolationAssessment(
                violation_id=violation.violation_id, status=status, supporting_item_refs=refs,
                reasoning_summary=f"Bounded offline assessment for Candidate {letter}.", confidence=Confidence.HIGH if refs else Confidence.LOW,
            ))
        assessments.append(SemanticSubjectAssessment(
            subject_id=subject_id, violation_assessments=rows,
            furthest_conclusion=FurthestJustifiedConclusion(statement=f"Bounded offline assessment for Candidate {letter}.", confidence=Confidence.HIGH),
            suggested_next_step="Proceed to human institutional review." if letter in "ABEJO" else "No further enquiry is currently justified.",
        ))
    return InvestigatorSemanticAssessment(semantic_items=items, subject_assessments=assessments)


def test_fifteen_candidate_full_offline_pipeline_and_exact_coverage() -> None:
    state = fixture_state()
    run_input = run_input_from_case_state(state, academic_integrity_core_preset())
    compiled = compile_semantic_assessment(_offline_assessment(state), run_input)
    result = VNextInvestigationRunner(lambda _: compiled).run(run_input)
    assert len(result.subject_assessments) == 15
    assert {item.subject_id for item in result.subject_assessments} == {f"subject_{letter}" for letter in "ABCDEFGHIJKLMNO"}
    assert all(len(item.violation_assessments) == 4 for item in result.subject_assessments)
    assert sum(len(item.violation_assessments) for item in result.subject_assessments) == 60
    report = build_report_record(build_input_snapshot(state, academic_integrity_core_preset(), "offline-15"), result, completed_at="offline")
    public = public_report_from_record(report, current_case_name=state.title, report_state="current", is_latest_successful_assessment=True, run_handle="offline-15")
    serialized = json.dumps(public)
    assert all(f"Candidate {letter}" in serialized for letter in "ABCDEFGHIJKLMNO")
    assert "subject_A" not in serialized and "subject_O" not in serialized
    by_subject = {item.subject_id: {row.violation_id: row for row in item.violation_assessments} for item in result.subject_assessments}
    assert all(by_subject[f"subject_{letter}"]["prohibited_collaboration"].status is AssessmentStatus.PARTIALLY_SUPPORTED for letter in "AB")
    assert all(by_subject[f"subject_{letter}"]["unauthorized_device"].status is AssessmentStatus.SUPPORTED for letter in "EJO")
    assert all(all(row.status is AssessmentStatus.NOT_CURRENTLY_SUPPORTED for row in by_subject[f"subject_{letter}"].values()) for letter in "CDHIKLMN")


@pytest.mark.parametrize(("target", "source_letter"), [("subject_A", "F"), ("subject_F", "A"), ("subject_K", "F"), ("subject_J", "E"), ("subject_O", "E")])
def test_private_cross_cohort_evidence_is_rejected(target: str, source_letter: str) -> None:
    state = fixture_state()
    run_input = run_input_from_case_state(state, academic_integrity_core_preset())
    base = _offline_assessment(state)
    item = EvidenceStatementItem(local_ref="cross_private", kind="evidence_statement", statement="Cross-cohort private evidence.", basis_source_ids=[source_id(state, f"candidate_{source_letter}_marker_report.md")])
    target_assessment = next(row for row in base.subject_assessments if row.subject_id == target)
    invalid_rows = [target_assessment.violation_assessments[0].model_copy(update={"status": AssessmentStatus.SUPPORTED, "supporting_item_refs": ["cross_private"]}), *target_assessment.violation_assessments[1:]]
    invalid = base.model_copy(update={"semantic_items": [*base.semantic_items, item], "subject_assessments": [target_assessment.model_copy(update={"violation_assessments": invalid_rows}) if row.subject_id == target else row for row in base.subject_assessments]})
    with pytest.raises(Exception, match="scope"):
        compile_semantic_assessment(invalid, run_input)


def test_relationship_material_cannot_be_reassigned_to_another_cohort() -> None:
    state = fixture_state()
    run_input = run_input_from_case_state(state, academic_integrity_core_preset())
    base = _offline_assessment(state)
    seating = source_id(state, "cohort_5a_seating_plan.md")
    relationship_item = EvidenceStatementItem(local_ref="ab_seating", kind="evidence_statement", statement="A and B were adjacent.", basis_source_ids=[seating])
    proposition = PropositionItem(local_ref="fg_claim", kind="proposition", statement="F and G collaborated.", about_subject_ids=["subject_F", "subject_G"], basis_item_refs=["ab_seating"])
    invalid = base.model_copy(update={"semantic_items": [*base.semantic_items, relationship_item, proposition]})
    with pytest.raises(Exception, match="scope"):
        compile_semantic_assessment(invalid, run_input)
