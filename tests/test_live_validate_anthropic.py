import json
from pathlib import Path

from investigator.llm import ModelCallMetadata, ModelCallResult
from investigator.models.assessment import AssessmentSubject
from investigator.models.source import Source, SourceType
from investigator.services.evidence_requests import HumanEvidenceWorkflow
from investigator.services.vnext_runner import VNextProductionRunner
from investigator.state.repository import CaseRepository
from investigator.vnext import AssessmentStatus, Confidence, FurthestJustifiedConclusion, InvestigatorProposal
from investigator.vnext.semantic import InvestigatorSemanticAssessment, SemanticSubjectAssessment, SemanticViolationAssessment
from investigator.vnext.presets import preset_for_case

from scripts.live_validate_anthropic import run_admitted_validation


class FakeAnthropicClient:
    def __init__(self, response: InvestigatorSemanticAssessment) -> None:
        self.response = response
        self.calls = 0
        self.admitted_before_call = False
        self.snapshot_before_call = False
        self.workflow: HumanEvidenceWorkflow | None = None
        self.case_id = "case-01"

    def call(self, _prompt, schema):
        self.calls += 1
        run_id = self.workflow.current_run_id(self.case_id) if self.workflow else None
        directory = self.workflow.repository.run_dir(self.case_id, run_id) if self.workflow and run_id else Path("missing")
        self.admitted_before_call = run_id is not None and directory.is_dir()
        self.snapshot_before_call = (directory / "assessment_input_snapshot.json").is_file()
        return ModelCallResult(parsed=schema.model_validate(self.response), metadata=ModelCallMetadata(provider="fake-anthropic", model="fixture", input_tokens=2, output_tokens=3, latency_seconds=0, parse_success=True), raw_output=self.response.model_dump(mode="json"))


def test_harness_admits_and_finalizes_normal_vnext_run_offline(tmp_path):
    workflow = HumanEvidenceWorkflow(CaseRepository(tmp_path / "cases"), run_mode="vnext")
    state = workflow.ensure_case("case-01")
    state.sources["S1"] = Source(id="S1", name="record.md", source_type=SourceType.DOCUMENT, content="A substantive record")
    workflow.repository.save(state)
    state.subjects["subject_1"] = AssessmentSubject(subject_id="subject_1", display_name="Student 1")
    workflow.repository.save(state)
    preset = preset_for_case(state)
    response = InvestigatorSemanticAssessment(
        semantic_items=[],
        subject_assessments=[SemanticSubjectAssessment(
            subject_id="subject_1",
            violation_assessments=[SemanticViolationAssessment(violation_id=item.violation_id, status=AssessmentStatus.NOT_CURRENTLY_SUPPORTED, reasoning_summary="Bounded fixture.", confidence=Confidence.LOW) for item in preset.violations],
            furthest_conclusion=FurthestJustifiedConclusion(statement="No finding is supported.", confidence=Confidence.LOW),
        )],
    )
    fake = FakeAnthropicClient(response)
    fake.workflow = workflow
    result, directory = run_admitted_validation(workflow, "case-01", fake)
    assert fake.calls == 1
    assert fake.admitted_before_call and fake.snapshot_before_call
    assert result.status.value == "completed"
    assert (directory / "assessment_input_snapshot.json").is_file()
    assert (directory / "report_record.json").is_file()
    assert (directory / "vnext_result.json").is_file()
    run_result = json.loads((directory / "run_result.json").read_text())
    assert run_result["final_runtime_status"] == "IDLE"
    assert run_result["outcome_type"] == "COMPLETED"
    assert workflow.current_run_id("case-01") is None
    summary_path = tmp_path / "validation-summary.json"
    summary_path.write_text(json.dumps({"run_directory": str(directory)}))
    assert summary_path.is_file()
