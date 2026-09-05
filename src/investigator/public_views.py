"""Strict allow-listed contracts for the normal vNext HTTP boundary."""

import hashlib
from typing import Any
from pydantic import BaseModel, ConfigDict, Field
from investigator.state.case_state import CaseState

def public_run_handle(case_id: str, run_id: str) -> str:
    return _handle("assessment", case_id, run_id)

def resolve_run_handle(workflow: Any, case_id: str, handle: str) -> str:
    for run in workflow.get_runs(case_id):
        if public_run_handle(case_id, str(run.get("run_id"))) == handle:
            return str(run["run_id"])
    raise KeyError("Assessment run not found")

def assessment_view(workflow: Any, case_id: str) -> dict[str, Any]:
    state = workflow.repository.require_case(case_id)
    runs = workflow.get_runs(case_id)
    latest = runs[-1] if runs else None
    successful = next((item for item in reversed(runs) if item.get("vnext_status") == "completed"), None)
    active = next((item for item in reversed(runs) if item.get("outcome_type") == "RUNNING"), None)
    if active:
        public_state = "running"
    elif latest and latest.get("outcome_type") in {"FAILED", "INTERRUPTED"}:
        public_state = "failed_previous_report_retained" if successful else "failed_no_report"
    elif latest and latest.get("outcome_type") == "STOPPED":
        public_state = "stopped"
    elif successful and int(successful.get("final_committed_revision") or successful.get("start_revision") or 0) < state.revision:
        public_state = "stale"
    elif successful:
        public_state = "complete"
    else:
        public_state = "not_started"
    def attempt(item: dict[str, Any] | None) -> dict[str, Any] | None:
        if not item: return None
        outcome = str(item.get("outcome_type") or "RUNNING").lower()
        return {"runHandle": public_run_handle(case_id, str(item["run_id"])), "state": outcome, "startedAt": item.get("started_at"), "endedAt": item.get("ended_at"), "message": (item.get("final_error") or {}).get("message", "") if isinstance(item.get("final_error"), dict) else ""}
    return {"state": public_state, "activeRun": attempt(active), "latestAttempt": attempt(latest), "reportAvailable": successful is not None, "reportStale": public_state == "stale"}

class PublicStudent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    studentHandle: str
    displayName: str
    candidateNumber: str | None = None

class PublicSource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sourceHandle: str
    fileName: str
    documentFormat: str

class PublicReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    state: str
    assessmentIsStale: bool

class PublicWorkspace(BaseModel):
    model_config = ConfigDict(extra="forbid")
    caseId: str
    caseRevision: int
    title: str
    caseStatus: str
    caseKind: str
    sample: dict[str, str] | None = None
    capabilities: dict[str, bool]
    preloadedSourceCount: int = 0
    assessment: dict[str, Any]
    runtimeStatus: str
    students: list[PublicStudent]
    sources: list[PublicSource]
    report: PublicReport
    chatHistory: list[dict[str, str]] = Field(default_factory=list)
    activity: list[dict[str, str]] = Field(default_factory=list)

class PublicSourceDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    caseId: str
    source: dict[str, str]

def _handle(prefix: str, case_id: str, internal_id: str) -> str:
    digest = hashlib.sha256(f"simplifynext:{case_id}:{internal_id}".encode()).hexdigest()[:24]
    return f"{prefix}-{digest}"

def public_student_handle(case_id: str, subject_id: str) -> str:
    return _handle("student", case_id, subject_id)

def public_source_handle(case_id: str, source_id: str) -> str:
    return _handle("source", case_id, source_id)

def resolve_student_handle(state: CaseState, handle: str) -> str:
    for subject_id in state.subjects:
        if public_student_handle(state.case_id, subject_id) == handle:
            return subject_id
    raise KeyError("Student not found")

def resolve_source_handle(state: CaseState, handle: str) -> str:
    for source_id in state.sources:
        if public_source_handle(state.case_id, source_id) == handle:
            return source_id
    raise KeyError("Source not found")

def document_format(filename: str) -> str:
    return "markdown" if filename.lower().endswith((".md", ".markdown")) else "plain_text"

def workspace_view(workflow: Any, case_id: str) -> dict[str, Any]:
    state = workflow.repository.require_case(case_id)
    runs = workflow.get_runs(case_id)
    latest = next((run for run in reversed(runs) if run.get("vnext_status") == "completed"), None)
    report = workflow.get_report(case_id)
    return PublicWorkspace(
        caseId=state.case_id, caseRevision=state.revision, title=report["title"], caseStatus=state.case_status,
        runtimeStatus=state.runtime_status, students=[PublicStudent(studentHandle=public_student_handle(case_id, item.subject_id), displayName=item.display_name, candidateNumber=item.candidate_number) for item in sorted(state.subjects.values(), key=lambda item: item.subject_id)],
        sources=[PublicSource(sourceHandle=public_source_handle(case_id, item.id), fileName=item.name, documentFormat=document_format(item.name)) for item in sorted(state.sources.values(), key=lambda item: item.id)],
    report=PublicReport(state=report["reportState"], assessmentIsStale=report["assessmentIsStale"]),
        caseKind=state.case_kind,
        sample={"sampleId": state.sample_id, "title": state.title} if state.case_kind == "sample" and state.sample_id else None,
        capabilities={"editStudents": state.case_kind != "sample", "addEvidence": state.case_kind != "sample", "resetSample": state.case_kind == "sample", "runAssessment": True, "useHelp": True, "viewSources": True},
        preloadedSourceCount=len(state.sources) if state.case_kind == "sample" else 0,
        assessment=assessment_view(workflow, case_id),
        chatHistory=[{"role": str(item.get("role", "workspace")), "text": str(item.get("text", ""))} for item in workflow._workspace_events.get(case_id, []) if item.get("type") == "chat"],
        activity=[{"type": str(item.get("type", "")), "summary": str(item.get("human_summary", "")), "createdAt": str(item.get("created_at", ""))} for item in workflow.workspace_events(case_id)],
    ).model_dump(mode="json")

def safe_help_context(workflow: Any, case_id: str) -> dict[str, Any]:
    state = workflow.repository.require_case(case_id)
    report = workflow.get_report(case_id)
    return {"caseTitle": report["title"], "assessment": {"state": report["reportState"], "assessmentIsStale": report["assessmentIsStale"]}, "students": report["students"], "sources": [{"sourceHandle": public_source_handle(case_id, source.id), "fileName": source.name, "documentFormat": document_format(source.name)} for source in state.sources.values()], "runtimeStatus": state.runtime_status}
