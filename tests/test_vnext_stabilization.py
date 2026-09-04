import json
from pathlib import Path
import time

import pytest

from investigator.cycle import RequestEvidence
from investigator.http_api import create_case, product_guide, sample_cases, seed_sample_case
from investigator.graph import GraphNode, GraphNodeType
from investigator.llm import ModelCallMetadata, ModelNativeCall, ModelTextBlock
from investigator.models.evidence_request import EvidenceRequestStatus
from investigator.services.evidence_requests import EvidenceRequestConflict, HumanEvidenceWorkflow
from investigator.state import CaseRepository, CaseState
from investigator.workspace_agent import WorkspaceAgent
from investigator.vnext import run_input_from_case_state
from investigator.vnext.presets import preset_for_case


class FailingWorkspaceClient:
    def __init__(self) -> None:
        self.calls = []

    def call_native(self, messages, tools):
        self.calls.append(messages)
        raise RuntimeError("offline failure")


def make_case(tmp_path: Path, mode: str) -> HumanEvidenceWorkflow:
    workflow = HumanEvidenceWorkflow(CaseRepository(tmp_path / mode), run_mode=mode)
    state = CaseState(case_id="case-01", title="Test", reasoning_graph=None)
    state.reasoning_graph = __import__("investigator.graph", fromlist=["CaseGraph"]).CaseGraph(
        case_id="case-01", nodes={"U1": GraphNode(id="U1", node_type=GraphNodeType.UNCERTAINTY, statement="Open")}, edges={}
    )
    workflow.repository.save(state)
    return workflow


def add_pending(workflow: HumanEvidenceWorkflow) -> None:
    workflow.request_evidence("case-01", RequestEvidence(
        target_uncertainty_id="U1", information_sought="More records", reason="Need context", expected_information_value="Could resolve uncertainty"
    ))


def complete_callback(case_id: str, workflow: HumanEvidenceWorkflow) -> None:
    workflow.set_runtime(case_id, "COMPLETED", "NONE")


def wait_for(workflow: HumanEvidenceWorkflow, status: str) -> None:
    for _ in range(100):
        if workflow.ensure_case("case-01").runtime_status == status:
            return
        time.sleep(0.01)
    pytest.fail(f"workflow did not reach {status}")


def test_legacy_pending_request_still_blocks(tmp_path: Path) -> None:
    workflow = make_case(tmp_path, "legacy")
    add_pending(workflow)
    with pytest.raises(EvidenceRequestConflict):
        workflow.start_run("case-01")


def test_vnext_pending_request_is_ignored_and_history_remains(tmp_path: Path) -> None:
    workflow = make_case(tmp_path, "vnext")
    add_pending(workflow)
    workflow.run_callback = complete_callback
    workflow.start_run("case-01")
    wait_for(workflow, "COMPLETED")
    state = workflow.ensure_case("case-01")
    assert len(state.evidence_request_history) == 1
    assert state.evidence_request_history[0].status is EvidenceRequestStatus.PENDING


def test_vnext_waiting_runtime_is_normalized_without_erasing_history(tmp_path: Path) -> None:
    workflow = make_case(tmp_path, "vnext")
    add_pending(workflow)
    state = workflow.ensure_case("case-01")
    state.runtime_status = "WAITING_FOR_EVIDENCE"
    workflow.repository.save(state)
    workflow.run_callback = complete_callback
    workflow.start_run("case-01")
    wait_for(workflow, "COMPLETED")
    assert workflow.ensure_case("case-01").evidence_request_history[0].status is EvidenceRequestStatus.PENDING


def test_vnext_help_retry_has_clean_conversation_and_single_safe_fallback(tmp_path: Path) -> None:
    workflow = make_case(tmp_path, "vnext")
    client = FailingWorkspaceClient()
    agent = WorkspaceAgent(workflow, client)
    result = agent.chat("case-01", "Explain the case")
    assert result.response == "Help is temporarily unavailable. The case and assessment are unaffected."
    session = agent.session_store.session("case-01")
    assert [item["role"] for item in session["conversation"]] == ["user", "assistant"]
    assert [item["role"] for item in session["chat_history"]] == ["human", "workspace"]
    assert len(client.calls) == 2
    assert [item["text"] for item in client.calls[0] if item.get("role") == "user"] == ["Explain the case"]
    assert [item["text"] for item in client.calls[1] if item.get("role") == "user"] == ["Explain the case"]


def test_fixture_variants_are_runtime_visible_but_evaluator_material_is_not(tmp_path: Path) -> None:
    root = Path("tests/fixtures/vnext_multi_subject")
    for directory in ("case_5a_combined", "case_5b_marker_only", "case_5c_invigilator_only"):
        state = CaseState.model_validate(json.loads((root / directory / "case_state.json").read_text()))
        assert set(state.subjects) == {"subject_A", "subject_B", "subject_C", "subject_D", "subject_E"}
        assert len(state.sources) == 14
        for subject_id in state.subjects:
            assert sum(f"{subject_id.split('_')[-1]}_marker_report" in source.metadata.get("filename", "") for source in state.sources.values()) == 1
            assert sum(f"{subject_id.split('_')[-1]}_invigilator_report" in source.metadata.get("filename", "") for source in state.sources.values()) == 1
            for source in state.sources.values():
                if subject_id.split("_")[-1] in source.metadata.get("filename", "") and ("marker_report" in source.metadata.get("filename", "") or "invigilator_report" in source.metadata.get("filename", "")):
                    assert source.metadata["assessment_scope"]["scope_type"] == "subject"
                    assert source.metadata["assessment_scope"]["subject_id"] == subject_id
        run_input_from_case_state(state, preset_for_case(state))
        assert all("evaluator_only" not in source.content for source in state.sources.values() if source.content)
    assert (root / "evaluator_only" / "hidden_ground_truth.json").is_file()


def test_fixture_reports_are_independently_authored_and_do_not_assert_outcomes() -> None:
    root = Path("tests/fixtures/vnext_multi_subject")
    for directory in ("case_5a_combined", "case_5b_marker_only", "case_5c_invigilator_only"):
        for path in (root / directory / "sources").glob("*.md"):
            text = path.read_text().lower()
            if "marker" in path.name:
                assert "same mistake as" not in text
                assert "similar to candidate" not in text
            assert "established collaboration" not in text
            assert "definitely copied" not in text


def test_vnext_activity_uses_current_product_language(tmp_path: Path) -> None:
    workflow = make_case(tmp_path, "vnext")
    workflow.run_callback = complete_callback
    workflow.start_run("case-01")
    wait_for(workflow, "COMPLETED")
    summaries = [event["human_summary"].lower() for event in workflow.workspace_events("case-01")]
    assert "assessment started." in summaries
    assert not any(term in summary for summary in summaries for term in ("steward", "autonomous investigation", "waiting for evidence", "failed turn", "resume"))


def test_guidance_resolves_subject_and_material_names(tmp_path: Path) -> None:
    workflow = make_case(tmp_path, "vnext")
    state = workflow.ensure_case("case-01")
    state.subjects = {"subject_A": __import__("investigator.models", fromlist=["AssessmentSubject"]).AssessmentSubject(subject_id="subject_A", display_name="Candidate A", candidate_number="BL-041")}
    state.sources["S1"] = __import__("investigator.models", fromlist=["Source"]).Source(id="S1", name="Invigilator report", source_type="document", content="record")
    workflow.repository.save(state)
    run_dir = tmp_path / "vnext" / "case-01" / "runs" / "run_000001"
    run_dir.mkdir(parents=True)
    (run_dir / "run_result.json").write_text(json.dumps({"run_id": "run_000001", "vnext_status": "completed", "start_revision": 0, "vnext_result_path": str(run_dir / "vnext_result.json")}))
    (run_dir / "vnext_result.json").write_text(json.dumps({"result": {"graph": {"nodes": {"node_abc123": {"statement": "Candidate A looked toward the adjacent seat.", "metadata": {"source_id": "S1"}}}}, "subject_assessments": [{"subject_id": "subject_A", "violation_assessments": [{"violation_id": "V1", "status": "supported", "reasoning_summary": "bounded", "supporting_node_ids": ["node_abc123"], "mitigating_node_ids": [], "unresolved_points": []}], "furthest_conclusion": {"statement": "bounded", "confidence": "low"}}]}}))
    context = workflow.get_guidance_context("case-01")
    assessment = context["per_subject_assessments"][0]
    assert assessment["subject_display_name"] == "Candidate A"
    assert assessment["subject_candidate_number"] == "BL-041"
    assert assessment["violation_assessments"][0]["supporting_material"][0]["statement"] == "Candidate A looked toward the adjacent seat."
    assert assessment["violation_assessments"][0]["supporting_material"][0]["source_labels"] == ["Invigilator report"]


def test_case_creation_and_public_sample_catalog_are_minimal(tmp_path: Path) -> None:
    workflow = HumanEvidenceWorkflow(CaseRepository(tmp_path / "cases"), run_mode="vnext")
    workspace = create_case(workflow, {"title": "New review", "description": "Context", "assessment": {"title": "Assessment", "assessment_type": "closed notes"}})
    assert workspace["caseId"] == "case-000001"
    assert workspace["assessmentContext"]["title"] == "Assessment"
    assert [item["title"] for item in sample_cases()] == ["Law Exam Investigation", "Multi-Candidate Collaboration Review"]
    assert "evaluator_only" not in product_guide()


def test_public_samples_seed_real_visible_sources_without_hidden_material(tmp_path: Path) -> None:
    workflow = HumanEvidenceWorkflow(CaseRepository(tmp_path / "cases"), run_mode="vnext")
    law = seed_sample_case(workflow, "law-exam", "law-exam-working")
    multi = seed_sample_case(workflow, "multi-candidate", "multi-candidate-working")
    assert len(law["visibleSources"]) == 17
    assert all("source" not in item["name"].lower() or "source 1" not in item["name"].lower() for item in multi["visibleSources"])
    assert len(multi["visibleSources"]) == 14
    assert all("evaluator_only" not in item["content"] for item in multi["visibleSources"])


def test_public_sample_fixture_integrity() -> None:
    law_expected = {
        "blank_tutorial.md", "student_script.md", "marker_report.md", "invigilator_report.md",
        "assessment_rules.md", "course_materials.md", "prior_assessed_work.md", "student_support_session.md",
        "assessment_logistics.md", "entry_permitted_items_record.md", "device_examination_report.md",
        "smart_glasses_activity_record.md", "external_ai_service_record.md", "device_account_linkage_record.md",
        "network_connectivity_record.md", "deterministic_timeline.md", "student_clarification_record.md",
    }
    law_root = Path("tests/fixtures/public_samples/law_exam/sources")
    assert {path.name for path in law_root.glob("*.md")} == law_expected
    assert not any((law_root / name).exists() for name in ("case_context_note.txt", "assessment_rule_clarification.txt", "student_b_witness_statement.md", "observation_timing_record.md"))

    multi_root = Path("tests/fixtures/public_samples/multi_candidate/sources")
    multi_files = {path.name for path in multi_root.glob("*.md")}
    assert len(multi_files) == 14
    assert all("source 1" not in path.lower() and "source 2" not in path.lower() for path in multi_files)
    assert all("visible sample source material" not in path.read_text(encoding="utf-8").lower() for path in multi_root.glob("*.md"))
    assert not any("evaluator_only" in path.parts for path in multi_root.rglob("*"))


def test_workspace_home_keeps_samples_in_one_cases_list_and_new_case_minimal() -> None:
    page = Path("frontend/app/page.tsx").read_text(encoding="utf-8")
    assert page.count('>Cases</h2>') == 1
    assert ">Samples</h2>" not in page
    assert 'placeholder="Case name"' in page
    assert 'placeholder="Assessment title"' not in page
    assert 'placeholder="Assessment type"' not in page


def test_new_cases_start_with_one_persistent_student_and_can_add_remove(tmp_path: Path) -> None:
    repository = CaseRepository(tmp_path / "cases")
    workflow = HumanEvidenceWorkflow(repository, run_mode="vnext")
    first = create_case(workflow, {"title": "First case"})
    second = create_case(workflow, {"title": "Second case"})
    first_state = repository.load(first["caseId"])
    assert list(first_state.subjects) == ["subject_1"]
    assert first_state.subjects["subject_1"].display_name == "Student 1"
    assert repository.load(first["caseId"]).subjects["subject_1"].display_name == "Student 1"
    assert list(repository.load(second["caseId"]).subjects) == ["subject_1"]

    added = workflow.add_subject(first["caseId"], {"display_name": "Student 2"})
    assert added.subject_id == "subject_2"
    workflow.remove_subject(first["caseId"], added.subject_id)
    with pytest.raises(ValueError, match="at least one student"):
        workflow.remove_subject(first["caseId"], "subject_1")


def test_student_identity_is_explicit_and_samples_keep_their_configuration(tmp_path: Path) -> None:
    workflow = HumanEvidenceWorkflow(CaseRepository(tmp_path / "cases"), run_mode="vnext")
    user = create_case(workflow, {"title": "Explicit students"})
    before = workflow.ensure_case(user["caseId"])
    workflow.add_direct_source(user["caseId"], display_name="Evidence", content="Candidate A appears in prose", source_type="document", expected_case_revision=before.revision)
    assert [item.display_name for item in workflow.ensure_case(user["caseId"]).subjects.values()] == ["Student 1"]
    law = seed_sample_case(workflow, "law-exam", "law-exam-working")
    multi = seed_sample_case(workflow, "multi-candidate", "multi-candidate-working")
    assert [item["display_name"] for item in law["subjects"]] == ["Candidate A"]
    assert [item["display_name"] for item in multi["subjects"]] == ["Candidate A", "Candidate B", "Candidate C", "Candidate D", "Candidate E"]
