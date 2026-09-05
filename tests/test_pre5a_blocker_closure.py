"""Offline regressions for the PRE-5A blocker closure boundary."""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from investigator.http_api import create_case, create_server, seed_sample_case
from investigator.models.assessment import AssessmentSubject
from investigator.public_views import public_student_handle
from investigator.services.evidence_requests import EvidenceRequestConflict, HumanEvidenceWorkflow
from investigator.state import CaseRepository, CaseState


def request(base: str, method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
    body = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"}
    req = urllib.request.Request(base + path, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def running_server(tmp_path: Path, workflow: HumanEvidenceWorkflow | None = None):
    active = workflow or HumanEvidenceWorkflow(CaseRepository(tmp_path / "cases"), run_mode="vnext")
    server = create_server(tmp_path / "api-cases", port=0, run_callback=lambda _case_id, _workflow: None, run_mode="vnext")
    server.RequestHandlerClass.workflow = active
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return active, server, thread, f"http://127.0.0.1:{server.server_address[1]}"


def stop_server(server, thread) -> None:
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def test_fresh_product_catalog_has_only_samples_until_users_create_cases(tmp_path: Path) -> None:
    workflow, server, thread, base = running_server(tmp_path)
    try:
        status, payload = request(base, "GET", "/api/cases")
        assert status == 200 and payload["cases"] == []
        status, payload = request(base, "GET", "/api/samples")
        assert status == 200 and len(payload["samples"]) == 2
        for title in ("Business Law Tutorial 5", "Law Exam Investigation"):
            status, created = request(base, "POST", "/api/cases", {"title": title})
            assert status == 201
            assert any(item["caseId"] == created["caseId"] and item["title"] == title for item in request(base, "GET", "/api/cases")[1]["cases"])
        assert not (Path(__file__).resolve().parents[1] / "data" / "cases" / "case-01.json").exists()
    finally:
        stop_server(server, thread)


def test_legacy_vnext_routes_are_rejected_before_mutation(tmp_path: Path) -> None:
    workflow, server, thread, base = running_server(tmp_path)
    try:
        created = create_case(workflow, {"title": "Boundary case"})
        case_id = created["caseId"]
        seed_sample_case(workflow, "law-exam", "law-exam-working")
        before_case = workflow.repository.load(case_id).model_dump(mode="json")
        before_ids = set(workflow.repository.list_case_ids())
        rejected = [
            ("POST", f"/api/cases/{case_id}/subjects", {"display_name": "Leaked"}),
            ("DELETE", f"/api/cases/{case_id}/subjects/subject_1", {}),
            ("POST", f"/api/cases/{case_id}/subjects/subject_1/rename", {"display_name": "Leaked"}),
            ("POST", "/api/samples/law-exam/open", {"case_id": case_id}),
            ("POST", "/api/samples/law-exam/reset", {"case_id": case_id}),
        ]
        for method, path, payload in rejected:
            status, body = request(base, method, path, payload)
            assert status == 404
            assert body == {"error": "Not found"}
        assert workflow.repository.load(case_id).model_dump(mode="json") == before_case
        assert set(workflow.repository.list_case_ids()) == before_ids
        assert workflow.workspace_events(case_id) == [
            item for item in workflow.workspace_events(case_id) if item["type"] == "case_created"
        ]
        assert workflow.repository.load("law-exam-working").case_kind == "sample"
    finally:
        stop_server(server, thread)


def test_sample_mutation_services_are_read_only_and_catalog_keeps_real_user_case(tmp_path: Path) -> None:
    workflow = HumanEvidenceWorkflow(CaseRepository(tmp_path / "cases"), run_mode="vnext")
    user = create_case(workflow, {"title": "Law Exam Investigation"})
    seed_sample_case(workflow, "law-exam", "law-exam-working")
    sample_before = workflow.repository.load("law-exam-working").model_dump(mode="json")
    for action in (
        lambda: workflow.add_direct_source("law-exam-working", display_name="new.md", content="new"),
        lambda: workflow.add_subject("law-exam-working", {"display_name": "Candidate B"}),
        lambda: workflow.rename_subject("law-exam-working", "subject_A", "Changed"),
        lambda: workflow.remove_subject("law-exam-working", "subject_A"),
    ):
        with pytest.raises(EvidenceRequestConflict, match="read-only"):
            action()
    assert workflow.repository.load("law-exam-working").model_dump(mode="json") == sample_before
    _, server, thread, base = running_server(tmp_path, workflow)
    try:
        status, payload = request(base, "GET", "/api/cases")
        assert status == 200
        assert any(item["caseId"] == user["caseId"] for item in payload["cases"])
        assert not any(item["caseId"] == "law-exam-working" for item in payload["cases"])
    finally:
        stop_server(server, thread)


def test_student_mutations_validate_the_full_proposed_registry(tmp_path: Path) -> None:
    workflow = HumanEvidenceWorkflow(CaseRepository(tmp_path / "cases"), run_mode="vnext")
    workspace = create_case(workflow, {"title": "Students"})
    case_id = workspace["caseId"]
    state_before = workflow.repository.load(case_id)
    with pytest.raises(ValueError, match="distinct identifier"):
        workflow.add_subject(case_id, {"display_name": " student-1 "})
    assert workflow.repository.load(case_id).model_dump(mode="json") == state_before.model_dump(mode="json")
    added = workflow.add_subject(case_id, {"display_name": "Ann"})
    before_rename = workflow.repository.load(case_id)
    with pytest.raises(ValueError, match="distinct identifier"):
        workflow.rename_subject(case_id, "subject_1", "ANN")
    assert workflow.repository.load(case_id).model_dump(mode="json") == before_rename.model_dump(mode="json")
    with pytest.raises(ValueError, match="distinct identifier"):
        workflow.rename_subject(case_id, added.subject_id, "Student-1")
    workflow.rename_subject(case_id, "subject_1", "Student One")
    assert workflow.repository.load(case_id).subjects["subject_1"].display_name == "Student One"

    _, server, thread, base = running_server(tmp_path, workflow)
    try:
        before_http = workflow.repository.load(case_id).model_dump(mode="json")
        status, body = request(base, "POST", f"/api/cases/{case_id}/students", {"displayName": "student-one"})
        assert status == 422
        assert body["error"] == "Each student must have a distinct identifier."
        assert workflow.repository.load(case_id).model_dump(mode="json") == before_http
        handle = public_student_handle(case_id, "subject_1")
        status, body = request(base, "POST", f"/api/cases/{case_id}/students/{handle}/rename", {"displayName": "ANN"})
        assert status == 422
        assert body["error"] == "Each student must have a distinct identifier."
        assert workflow.repository.load(case_id).model_dump(mode="json") == before_http
    finally:
        stop_server(server, thread)


def test_delete_student_maps_case_handle_and_mutation_errors_truthfully(tmp_path: Path) -> None:
    workflow = HumanEvidenceWorkflow(CaseRepository(tmp_path / "cases"), run_mode="vnext")
    first = create_case(workflow, {"title": "First"})
    second = create_case(workflow, {"title": "Second"})
    second_student = workflow.add_subject(second["caseId"], {"display_name": "Student 2"})
    _, server, thread, base = running_server(tmp_path, workflow)
    try:
        status, body = request(base, "DELETE", "/api/cases/missing/students/unknown", {})
        assert status == 404 and body == {"error": "Case not found"}
        status, body = request(base, "DELETE", f"/api/cases/{first['caseId']}/students/unknown", {})
        assert status == 404 and body == {"error": "Student not found"}
        cross_case_handle = public_student_handle(second["caseId"], second_student.subject_id)
        status, body = request(base, "DELETE", f"/api/cases/{first['caseId']}/students/{cross_case_handle}", {})
        assert status == 404 and body == {"error": "Student not found"}
        before = workflow.repository.load(first["caseId"])
        status, body = request(base, "DELETE", f"/api/cases/{first['caseId']}/students/{public_student_handle(first['caseId'], 'subject_1')}", {})
        assert status == 422 and body["code"] == "FINAL_STUDENT_REQUIRED"
        assert workflow.repository.load(first["caseId"]).revision == before.revision

        sample_id = "law-exam-working"
        seed_sample_case(workflow, "law-exam", sample_id)
        sample_before = workflow.repository.load(sample_id).model_dump(mode="json")
        sample_handle = public_student_handle(sample_id, "subject_A")
        status, body = request(base, "DELETE", f"/api/cases/{sample_id}/students/{sample_handle}", {})
        assert status == 409 and body["code"] == "SAMPLE_READ_ONLY"
        assert workflow.repository.load(sample_id).model_dump(mode="json") == sample_before

        workflow.add_subject(first["caseId"], {"display_name": "Student 2"})
        started = threading.Event()
        release = threading.Event()

        def callback(case_id: str, active: HumanEvidenceWorkflow) -> None:
            started.set()
            release.wait(timeout=2)
            active.set_runtime(case_id, "COMPLETED", "NONE")

        workflow.run_callback = callback
        runner = threading.Thread(target=workflow.start_run, args=(first["caseId"],), daemon=True)
        runner.start()
        assert started.wait(timeout=2)
        before_running = workflow.repository.load(first["caseId"])
        running_handle = public_student_handle(first["caseId"], "subject_2")
        status, body = request(base, "DELETE", f"/api/cases/{first['caseId']}/students/{running_handle}", {})
        assert status == 409 and body["code"] == "CASE_MUTATION_CONFLICT"
        assert body["error"] != "Student not found"
        assert workflow.repository.load(first["caseId"]).revision == before_running.revision
        release.set()
        runner.join(timeout=2)
        for _ in range(100):
            if workflow.current_run_id(first["caseId"]) is None:
                break
            time.sleep(0.01)
        status, _ = request(base, "DELETE", f"/api/cases/{first['caseId']}/students/{running_handle}", {})
        assert status == 200
    finally:
        stop_server(server, thread)


def test_sample_reset_rejects_finalizing_run_until_active_owner_releases(tmp_path: Path) -> None:
    workflow = HumanEvidenceWorkflow(CaseRepository(tmp_path / "cases"), run_mode="vnext")
    seed_sample_case(workflow, "law-exam", "law-exam-working")
    started = threading.Event()
    release = threading.Event()

    def callback(case_id: str, active: HumanEvidenceWorkflow) -> None:
        run_id = active.current_run_id(case_id)
        assert run_id is not None
        run_dir = active._run_dir(case_id, run_id)
        (run_dir / "report_record.json").write_text(json.dumps({"case_id": case_id}), encoding="utf-8")
        active.set_runtime(case_id, "COMPLETED", "NONE")
        started.set()
        release.wait(timeout=2)

    workflow.run_callback = callback
    _, server, thread, base = running_server(tmp_path, workflow)
    runner = None
    try:
        runner = threading.Thread(target=workflow.start_run, args=("law-exam-working",), daemon=True)
        runner.start()
        assert started.wait(timeout=2)
        run_id = workflow.current_run_id("law-exam-working")
        assert run_id is not None
        run_dir = workflow._run_dir("law-exam-working", run_id)
        original_sources = workflow.repository.load("law-exam-working").sources
        status, body = request(base, "POST", "/api/samples/reset", {"sampleId": "law-exam"})
        assert status == 409 and body["code"] == "SAMPLE_BUSY"
        assert run_dir.exists() and (run_dir / "report_record.json").exists()
        assert workflow.repository.load("law-exam-working").sources == original_sources
        release.set()
        runner.join(timeout=2)
        for _ in range(100):
            if workflow.current_run_id("law-exam-working") is None:
                break
            time.sleep(0.01)
        assert workflow.current_run_id("law-exam-working") is None
        status, _ = request(base, "POST", "/api/samples/reset", {"sampleId": "law-exam"})
        assert status == 200
        assert not run_dir.exists()
        assert workflow.repository.load("law-exam-working").sources == original_sources
    finally:
        release.set()
        if runner is not None:
            runner.join(timeout=2)
        stop_server(server, thread)


def test_snapshot_admission_failure_does_not_publish_running_state_or_partial_run(tmp_path: Path, monkeypatch) -> None:
    import investigator.services.evidence_requests as evidence_requests

    workflow = HumanEvidenceWorkflow(CaseRepository(tmp_path / "cases"), run_mode="vnext")
    create_case(workflow, {"title": "Admission"})
    before = workflow.repository.load("case-000001")
    monkeypatch.setattr(evidence_requests, "build_input_snapshot", lambda *_args: (_ for _ in ()).throw(RuntimeError("snapshot failed")))
    with pytest.raises(RuntimeError, match="snapshot failed"):
        workflow.start_run("case-000001")
    after = workflow.repository.load("case-000001")
    assert after.runtime_status == before.runtime_status == "IDLE"
    assert workflow.current_run_id("case-000001") is None
    assert not list((tmp_path / "cases" / "case-000001" / "runs").glob("run_*/run_result.json"))


def test_initial_run_record_write_failure_does_not_publish_running_state(tmp_path: Path, monkeypatch) -> None:
    workflow = HumanEvidenceWorkflow(CaseRepository(tmp_path / "cases"), run_mode="vnext")
    create_case(workflow, {"title": "Admission"})
    original = workflow._write_run_result

    def fail_initial(path: Path, result: dict) -> None:
        if path.name == "run_result.json":
            raise OSError("run record failed")
        original(path, result)

    monkeypatch.setattr(workflow, "_write_run_result", fail_initial)
    with pytest.raises(OSError, match="run record failed"):
        workflow.start_run("case-000001")
    assert workflow.repository.load("case-000001").runtime_status == "IDLE"
    assert workflow.current_run_id("case-000001") is None
    assert not list((tmp_path / "cases" / "case-000001" / "runs").glob("run_*/run_result.json"))


def test_case_running_state_write_failure_rolls_back_admission(tmp_path: Path) -> None:
    class FailingRepository(CaseRepository):
        fail_running = True

        def save(self, case_state: CaseState) -> None:
            if self.fail_running and case_state.runtime_status == "RUNNING":
                self.fail_running = False
                raise OSError("case state failed")
            super().save(case_state)

    repository = FailingRepository(tmp_path / "cases")
    workflow = HumanEvidenceWorkflow(repository, run_mode="vnext")
    create_case(workflow, {"title": "Admission"})
    with pytest.raises(OSError, match="case state failed"):
        workflow.start_run("case-000001")
    assert repository.load("case-000001").runtime_status == "IDLE"
    assert workflow.current_run_id("case-000001") is None
    assert not list((tmp_path / "cases" / "case-000001" / "runs").glob("run_*/run_result.json"))


def test_worker_start_failure_rolls_back_admission(tmp_path: Path, monkeypatch) -> None:
    workflow = HumanEvidenceWorkflow(CaseRepository(tmp_path / "cases"), run_mode="vnext")
    create_case(workflow, {"title": "Admission"})

    class FailingThread:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def start(self) -> None:
            raise RuntimeError("thread failed")

    monkeypatch.setattr("threading.Thread", FailingThread)
    with pytest.raises(RuntimeError, match="Assessment could not be started"):
        workflow.start_run("case-000001")
    assert workflow.repository.load("case-000001").runtime_status == "IDLE"
    assert workflow.current_run_id("case-000001") is None
    assert not list((tmp_path / "cases" / "case-000001" / "runs").glob("run_*/run_result.json"))


def test_duplicate_admission_and_terminal_cleanup_keep_one_owner(tmp_path: Path) -> None:
    workflow = HumanEvidenceWorkflow(CaseRepository(tmp_path / "cases"), run_mode="vnext")
    create_case(workflow, {"title": "Admission"})
    started = threading.Event()
    release = threading.Event()

    def callback(case_id: str, active: HumanEvidenceWorkflow) -> None:
        started.set()
        release.wait(timeout=2)
        active.set_runtime(case_id, "COMPLETED", "NONE")

    workflow.run_callback = callback
    first_result: list[object] = []

    def first_start() -> None:
        try:
            first_result.append(workflow.start_run_with_id("case-000001"))
        except Exception as exc:  # pragma: no cover - assertion below reports it
            first_result.append(exc)

    first_thread = threading.Thread(target=first_start)
    first_thread.start()
    assert started.wait(timeout=2)
    with pytest.raises(EvidenceRequestConflict, match="already running"):
        workflow.start_run("case-000001")
    release.set()
    first_thread.join(timeout=2)
    assert first_result and not isinstance(first_result[0], Exception)


def test_invalid_persisted_student_registry_is_rejected_before_running(tmp_path: Path) -> None:
    repository = CaseRepository(tmp_path / "cases")
    state = CaseState(
        case_id="case-01",
        title="Invalid old case",
        subjects={
            "subject_A": AssessmentSubject(subject_id="subject_A", display_name="Candidate A"),
            "subject_B": AssessmentSubject(subject_id="subject_B", display_name="candidate-a"),
        },
    )
    repository.save(state)
    workflow = HumanEvidenceWorkflow(repository, run_mode="vnext")
    with pytest.raises(EvidenceRequestConflict, match="distinct identifier"):
        workflow.start_run("case-01")
    assert repository.load("case-01").runtime_status == "IDLE"
    assert workflow.get_runs("case-01") == []


def test_orphaned_running_state_is_reconciled_without_provider_call(tmp_path: Path) -> None:
    workflow = HumanEvidenceWorkflow(CaseRepository(tmp_path / "cases"), run_mode="vnext")
    state = CaseState(case_id="case-01", title="Orphan")
    state.subjects = {"subject_1": AssessmentSubject(subject_id="subject_1", display_name="Student 1")}
    state.runtime_status = "RUNNING"
    workflow.repository.save(state)
    workflow.recover_interrupted_run("case-01")
    recovered = workflow.repository.load("case-01")
    assert recovered.runtime_status == "INTERRUPTED"
    assert recovered.last_error == {"failure_category": "PROCESS_RESTART", "message": "The assessment was interrupted when the service restarted."}
    assert workflow.current_run_id("case-01") is None


def test_fast_completion_returns_the_exact_admitted_run_identity(tmp_path: Path) -> None:
    workflow = HumanEvidenceWorkflow(CaseRepository(tmp_path / "cases"), run_mode="vnext")
    create_case(workflow, {"title": "Fast"})

    def complete(case_id: str, active: HumanEvidenceWorkflow) -> None:
        active.set_runtime(case_id, "COMPLETED", "NONE")

    workflow.run_callback = complete
    run_id, _workspace = workflow.start_run_with_id("case-000001")
    for _ in range(100):
        if any(item.get("run_id") == run_id and item.get("outcome_type") == "COMPLETED" for item in workflow.get_runs("case-000001")):
            break
        time.sleep(0.01)
    runs = workflow.get_runs("case-000001")
    assert any(item.get("run_id") == run_id for item in runs)
    assert workflow.current_run_id("case-000001") is None
