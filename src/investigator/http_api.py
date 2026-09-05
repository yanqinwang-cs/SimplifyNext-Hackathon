"""Minimal standard-library HTTP API for the investigator workspace."""

import argparse
import json
import os
import re
import shutil
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from pydantic import BaseModel, ConfigDict, Field
from urllib.parse import unquote, urlparse, parse_qs

from investigator.services.evidence_requests import EvidenceRequestConflict, HumanEvidenceWorkflow, ReportIntegrityError
from investigator.services.production_runner import default_production_run
from investigator.llm.bedrock import BedrockModelClient, CredentialOverride, clear_credential_override, credential_status, debug_credentials_enabled, set_credential_override
from investigator.state.repository import CaseRepository
from investigator.workspace_agent import WorkspaceAgent, WorkspaceChatRequest, WorkspaceToolAuthorizationError
from investigator.model_registry import MODEL_REGISTRY
from investigator.services.vnext_runner import VNextProductionRunner
from investigator.models.source import SourceType
from investigator.graph import GraphScope
from investigator.models.assessment import AssessmentContext, AssessmentSubject, SubjectRelationship
from investigator.models.source import Source
from investigator.state.case_state import CaseState
from investigator.help import product_guide
from investigator.public_views import assessment_view, workspace_view, public_run_handle_for_instance, public_source_handle, public_student_handle, resolve_run_handle, resolve_source_handle, resolve_student_handle, document_format
from investigator.runtime_settings import RuntimeSettingsError, settings as runtime_settings, set_model_overrides, reset_model_overrides


def _safe_filename_token(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip(".-") or "run"


class CaseTitleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=160)


def sample_cases() -> list[dict[str, str]]:
    return [
        {"sampleId": "law-exam", "title": "Law Exam Investigation"},
        {"sampleId": "multi-candidate", "title": "Multi-Candidate Collaboration Review"},
    ]


def allocate_case_id(repository: CaseRepository) -> str:
    numbers = [int(case_id.removeprefix("case-")) for case_id in repository.list_case_ids() if case_id.removeprefix("case-").isdigit()]
    return f"case-{max(numbers, default=0) + 1:06d}"


def create_case(workflow: HumanEvidenceWorkflow, payload: dict[str, Any]) -> dict[str, Any]:
    title = str(payload.get("title") or "").strip()
    assessment = payload.get("assessment") or {}
    if not title:
        raise ValueError("title is required")
    case_id = allocate_case_id(workflow.repository)
    context_payload = {key: assessment.get(key) for key in ("title", "assessment_type", "venue", "start_time", "end_time") if assessment.get(key) is not None}
    context = AssessmentContext(assessment_id=f"{case_id}-assessment", **context_payload)
    state = CaseState(case_id=case_id, title=title, description=str(payload.get("description") or ""), assessment_context=context, subjects={"subject_1": AssessmentSubject(subject_id="subject_1", display_name="Student 1")})
    workflow.repository.save(state)
    workflow.record_workspace_event(case_id, {"type": "case_created", "case_revision": state.revision, "human_summary": "Case created."})
    return workflow.get_workspace(case_id)


def seed_sample_case(workflow: HumanEvidenceWorkflow, sample_id: str, case_id: str) -> dict[str, Any]:
    samples = {item["sampleId"]: item for item in sample_cases()}
    if sample_id not in samples:
        raise ValueError(f"Unknown sample case: {sample_id!r}")
    fixture_root = Path(__file__).resolve().parent / "public_samples"
    descriptions = {"law-exam": "A rich single-case investigation with multiple records.", "multi-candidate": "A controlled five-subject assessment."}
    if sample_id == "law-exam":
        source_root = fixture_root / "law_exam" / "sources"
        sources = {f"S{index}": Source(id=f"S{index}", name=path.name, source_type=SourceType.DOCUMENT, content=path.read_text(encoding="utf-8"), metadata={"filename": path.name, "assessment_scope": GraphScope(scope_type="case").model_dump(mode="json")}) for index, path in enumerate(sorted(source_root.glob("*.md")), start=1)}
        state = CaseState(case_id=case_id, title="Law Exam Investigation", description=descriptions[sample_id], case_kind="sample", sample_id=sample_id, assessment_context=AssessmentContext(assessment_id=f"{case_id}-assessment", title="Business Law Individual In-Class Assessment 2", assessment_type="closed-notes individual assessment"), subjects={"subject_A": AssessmentSubject(subject_id="subject_A", display_name="Candidate A", candidate_number="BL-041")}, sources=sources)
    elif sample_id == "multi-candidate":
        source_root = fixture_root / "multi_candidate" / "sources"
        subjects = {f"subject_{letter}": AssessmentSubject(subject_id=f"subject_{letter}", display_name=f"Candidate {letter}", candidate_number=number) for letter, number in (("A", "BL-041"), ("B", "BL-073"), ("C", "BL-118"), ("D", "BL-162"), ("E", "BL-205"))}
        sources = {}
        for index, path in enumerate(sorted(source_root.glob("*.md")), start=1):
            letter = next((candidate for candidate in "ABCDE" if f"candidate_{candidate}_" in path.name), None)
            scope = GraphScope(scope_type="subject", subject_id=f"subject_{letter}") if letter else GraphScope(scope_type="case")
            sources[f"S{index}"] = Source(id=f"S{index}", name=path.name, source_type=SourceType.DOCUMENT, content=path.read_text(encoding="utf-8"), metadata={"filename": path.name, "assessment_scope": scope.model_dump(mode="json")})
        seating_source = next((source for source in sources.values() if source.name == "seating_plan.md"), None)
        if seating_source is None:
            raise ValueError("The Multi-Candidate sample requires seating_plan.md for relationship provenance")
        state = CaseState(case_id=case_id, title="Multi-Candidate Collaboration Review", description=descriptions[sample_id], case_kind="sample", sample_id=sample_id, assessment_context=AssessmentContext(assessment_id=f"{case_id}-assessment", title="Business Law Individual In-Class Assessment 2", assessment_type="closed-notes individual assessment", venue="Seminar Room 4"), subjects=subjects, subject_relationships={"rel_A_B_adjacent": SubjectRelationship(relationship_id="rel_A_B_adjacent", subject_ids=["subject_A", "subject_B"], relationship_type="adjacent_seating", source_ids=[seating_source.id], description="Candidate A and Candidate B were seated next to one another.")}, sources=sources)
    else:
        raise ValueError(f"Unknown sample case: {sample_id!r}")
    workflow.repository.save(state)
    workflow.record_workspace_event(case_id, {"type": "case_created", "case_revision": state.revision, "human_summary": f"Sample case {state.title} was opened."})
    return workflow.get_workspace(case_id)


SAMPLE_CASE_IDS = {"law-exam": "law-exam-working", "multi-candidate": "multi-candidate-working"}


def open_sample_case(workflow: HumanEvidenceWorkflow, sample_id: str) -> str:
    case_id = SAMPLE_CASE_IDS[sample_id]
    if workflow.repository.exists(case_id):
        state = workflow.repository.require_case(case_id)
        if state.case_kind != "sample" or state.sample_id != sample_id:
            raise ValueError("Sample working copy metadata is invalid")
    else:
        seed_sample_case(workflow, sample_id, case_id)
    return case_id


def reset_sample_case(workflow: HumanEvidenceWorkflow, sample_id: str) -> str:
    case_id = SAMPLE_CASE_IDS[sample_id]
    with workflow._lock:
        state = workflow.repository.require_case(case_id) if workflow.repository.exists(case_id) else None
        if workflow.has_active_run(case_id, state):
            raise EvidenceRequestConflict("Sample cannot be reset while an assessment is running")
        if workflow.repository.case_artifact_dir(case_id).exists():
            shutil.rmtree(workflow.repository.case_artifact_dir(case_id))
        workflow._workspace_events.pop(case_id, None)
        workflow._in_flight_actor.pop(case_id, None)
        workflow._model_revision_active.discard(case_id)
        seed_sample_case(workflow, sample_id, case_id)
    return case_id


class InvestigatorApiHandler(BaseHTTPRequestHandler):
    workflow: HumanEvidenceWorkflow
    workspace_agent: WorkspaceAgent

    def _public_workspace(self, case_id: str) -> dict[str, Any]:
        view = workspace_view(self.workflow, case_id)
        view["chatHistory"] = self.workspace_agent.chat_history(case_id)
        return view

    def do_GET(self) -> None:
        parts = self._parts()
        if parts == ["api", "runtime-settings"]:
            try:
                case_id = parse_qs(urlparse(self.path).query).get("caseId", [None])[0]
                self._write(200, runtime_settings(case_id=case_id, workflow=self.workflow))
            except RuntimeSettingsError:
                self._write(422, {"error": "The configured model is not supported by this prototype", "code": "UNSUPPORTED_MODEL_CONFIGURATION"})
            return
        if self.workflow.run_mode == "vnext" and len(parts) == 4 and parts[0:2] == ["api", "cases"] and parts[3] == "workspace":
            try: self._write(200, self._public_workspace(parts[2]))
            except (KeyError, ValueError): self._write(404, {"error": "Case not found"})
            return
        if self.workflow.run_mode == "vnext" and len(parts) == 7 and parts[0:2] == ["api", "cases"] and parts[3] == "assessment-runs" and parts[5:7] == ["audit-trace", "download"]:
            try:
                run_id = resolve_run_handle(self.workflow, parts[2], parts[4])
                body = self.workflow.audit_trace_file(parts[2], run_id, parts[4])
                filename = f"caselens-{_safe_filename_token(parts[2])}-{_safe_filename_token(parts[4])}-trace.jsonl"
                self._write_bytes(200, body, "application/x-ndjson; charset=utf-8", f'attachment; filename="{filename}"')
            except RuntimeError:
                self._write(409, {"error": "Assessment trace is not finalized"})
            except (KeyError, ValueError):
                self._write(404, {"error": "Assessment trace not found"})
            return
        if self.workflow.run_mode == "vnext" and len(parts) == 6 and parts[0:2] == ["api", "cases"] and parts[3] == "assessment-runs" and parts[5] == "audit-trace":
            try:
                run_id = resolve_run_handle(self.workflow, parts[2], parts[4])
                self._write(200, self.workflow.audit_trace(parts[2], run_id, parts[4]))
            except (KeyError, ValueError):
                self._write(404, {"error": "Assessment trace not found"})
            return
        if self.workflow.run_mode == "vnext" and len(parts) == 4 and parts[0:2] == ["api", "cases"] and parts[3] == "report":
            try:
                assessment = parse_qs(urlparse(self.path).query).get("assessment", [None])[0]
                run_id = resolve_run_handle(self.workflow, parts[2], assessment) if assessment else None
                self._write(200, self.workflow.get_report(parts[2], run_id))
            except ReportIntegrityError: self._write(500, {"error": "The assessment report could not be loaded safely."})
            except (KeyError, ValueError): self._write(404, {"error": "Assessment report not found"})
            return
        if self.workflow.run_mode == "vnext" and len(parts) == 6 and parts[0:2] == ["api", "cases"] and parts[3] == "assessment-runs" and parts[5] == "report":
            try:
                run_id = resolve_run_handle(self.workflow, parts[2], parts[4])
                self._write(200, self.workflow.get_report(parts[2], run_id))
            except ReportIntegrityError: self._write(500, {"error": "The assessment report could not be loaded safely."})
            except (KeyError, ValueError): self._write(404, {"error": "Assessment report not found"})
            return
        if self.workflow.run_mode == "vnext" and len(parts) == 7 and parts[0:2] == ["api", "cases"] and parts[3] == "assessment-runs" and parts[5] == "sources":
            try:
                run_id = resolve_run_handle(self.workflow, parts[2], parts[4])
                self._write(200, self.workflow.get_historical_source(parts[2], run_id, parts[6]))
            except ReportIntegrityError: self._write(500, {"error": "The assessment source could not be loaded safely."})
            except (KeyError, ValueError): self._write(404, {"error": "Historical source not found"})
            return
        if self.workflow.run_mode == "vnext" and len(parts) == 5 and parts[0:2] == ["api", "cases"] and parts[3] == "sources":
            try:
                state = self.workflow.repository.require_case(parts[2]); source_id = resolve_source_handle(state, parts[4]); source = state.sources[source_id]
                self._write(200, {"caseId": parts[2], "source": {"sourceHandle": parts[4], "fileName": source.name, "documentFormat": document_format(source.name), "content": source.content or ""}})
            except (KeyError, ValueError): self._write(404, {"error": "Source not found"})
            return
        if self.workflow.run_mode == "vnext" and len(parts) == 5 and parts[0:2] == ["api", "cases"] and parts[3] == "assessment-runs":
            try:
                run_id = resolve_run_handle(self.workflow, parts[2], parts[4])
                run = next(item for item in self.workflow.get_runs(parts[2]) if item.get("run_id") == run_id)
                self._write(200, {"runHandle": parts[4], "state": str(run.get("outcome_type") or "RUNNING").lower(), "startedAt": run.get("started_at"), "endedAt": run.get("ended_at"), "message": (run.get("final_error") or {}).get("message", "") if isinstance(run.get("final_error"), dict) else "", "reportAvailable": run.get("vnext_status") == "completed", "reportStale": assessment_view(self.workflow, parts[2])["reportStale"], "workspace": self._public_workspace(parts[2])})
            except (KeyError, ValueError, StopIteration): self._write(404, {"error": "Assessment run not found"})
            return
        if parts == ["api", "cases"]:
            cases = []
            for case_id in self.workflow.repository.list_case_ids():
                state = self.workflow.repository.load(case_id)
                if state.case_kind == "sample":
                    continue
                cases.append({"caseId": case_id, "title": state.title})
            self._write(200, {"cases": cases})
            return
        if parts == ["api", "product-guide"]:
            body = product_guide().encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/markdown; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self._cors_headers()
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                return
            return
        if parts == ["api", "debug", "aws-credentials", "status"]:
            if not debug_credentials_enabled():
                self._write(404, {"error": "Debug credential endpoints are disabled"})
            else:
                self._write(200, credential_status())
            return
        if parts == ["api", "debug", "runtime-settings"]:
            if not debug_credentials_enabled():
                self._write(404, {"error": "Debug runtime settings are disabled"})
            else:
                self._write(200, {"aws": {"mode": "temporary_override" if credential_status()["override_active"] else "default_chain"}, **runtime_settings()})
            return
        if len(parts) == 4 and parts[0:2] == ["api", "cases"] and parts[3] == "workspace":
            self._write(200, self.workflow.get_workspace(parts[2]) | {"chatHistory": self.workspace_agent.chat_history(parts[2]), "guidance": self.workflow.get_guidance_context(parts[2])})
            return
        if len(parts) == 4 and parts[0:2] == ["api", "cases"] and parts[3] == "report":
            self._write(200, self.workflow.get_report(parts[2]))
            return
        if parts == ["api", "samples"]:
            self._write(200, {"samples": sample_cases()})
            return
        if len(parts) == 4 and parts[0:2] == ["api", "cases"] and parts[3] == "traces":
            self._write(200, {"caseId": parts[2], "traces": self.workflow.get_traces(parts[2])})
            return
        if len(parts) == 4 and parts[0:2] == ["api", "cases"] and parts[3] == "runs":
            self._write(200, {"caseId": parts[2], "runs": self.workflow.get_runs(parts[2])})
            return
        if len(parts) == 6 and parts[0:2] == ["api", "cases"] and parts[3] == "runs" and parts[5] == "raw-traces":
            if self.workflow.run_mode == "vnext":
                self._write(404, {"error": "Not found"})
                return
            try:
                body = self.workflow.sanitized_raw_trace(parts[2], parts[4])
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.send_header("Content-Disposition", 'inline; filename="raw_traces.jsonl"')
                self.send_header("Content-Length", str(len(body)))
                self._cors_headers()
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    return
            except (KeyError, ValueError):
                self._write(404, {"error": "Run not found"})
            return
        self._write(404, {"error": "Not found"})

    def do_OPTIONS(self) -> None:
        if self._is_blocked_legacy_vnext_route(self._parts()):
            self._write(404, {"error": "Not found"})
            return
        origin = self.headers.get("Origin")
        allowed = {item.strip() for item in os.getenv("SIMPLIFYNEXT_ALLOWED_ORIGINS", "http://127.0.0.1:3000,http://localhost:3000").split(",") if item.strip()}
        if origin and origin not in allowed:
            self._write(403, {"error": "Origin is not allowed"})
            return
        self.send_response(204)
        self._cors_headers()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_POST(self) -> None:
        parts = self._parts()
        if self._is_blocked_legacy_vnext_route(parts):
            self._write(404, {"error": "Not found"})
            return
        if parts == ["api", "runtime-settings", "aws-credentials"]:
            try:
                payload = self._read_json()
                required = {"aws_access_key_id", "aws_secret_access_key", "aws_session_token"}
                if set(payload) != required or any(not isinstance(payload[field], str) or not payload[field].strip() or len(payload[field]) > 4096 for field in required):
                    raise ValueError("All temporary AWS credential fields are required")
                set_credential_override(CredentialOverride(**payload))
                self._write(200, runtime_settings(workflow=self.workflow))
            except ValueError as exc:
                self._write(422, {"error": str(exc), "code": "INVALID_CREDENTIAL_INPUT"})
            return
        if parts == ["api", "runtime-settings", "models"]:
            try:
                payload = self._read_json()
                if set(payload) != {"investigator", "workspaceHelp"}:
                    raise RuntimeSettingsError("Investigator and Workspace Help model choices are required")
                set_model_overrides({"investigator": payload["investigator"], "workspace_help": payload["workspaceHelp"]})
                self._write(200, runtime_settings(workflow=self.workflow))
            except RuntimeSettingsError as exc:
                self._write(422, {"error": str(exc), "code": "INVALID_MODEL_SETTINGS"})
            return
        if parts == ["api", "runtime-settings", "models", "reset"]:
            reset_model_overrides()
            self._write(200, runtime_settings(workflow=self.workflow))
            return
        if self.workflow.run_mode == "vnext" and ((len(parts) == 4 and parts[0:2] == ["api", "cases"] and parts[3] in {"relationships", "context"}) or (len(parts) == 6 and parts[0:2] == ["api", "cases"] and parts[3] in {"subjects", "evidence-requests"})):
            self._write(404, {"error": "Not found"})
            return
        if self.workflow.run_mode == "vnext" and parts == ["api", "cases"]:
            try:
                payload = self._read_json()
                if set(payload) - {"title"}: raise ValueError("Only title is supported")
                created = create_case(self.workflow, payload)
                self._write(201, {"caseId": created["caseId"], "workspace": self._public_workspace(created["caseId"])})
            except (ValueError, KeyError): self._write(422, {"error": "Invalid case request"})
            return
        if self.workflow.run_mode == "vnext" and len(parts) == 4 and parts[0:2] == ["api", "cases"] and parts[3] == "run":
            try:
                if not self.workflow.repository.require_case(parts[2]).subjects:
                    self._write(409, {"error": "A configured student is required before assessment", "code": "NO_CONFIGURED_STUDENT"})
                    return
                run_id, workspace = self.workflow.start_run_with_id(parts[2])
                run = next(item for item in self.workflow.get_runs(parts[2]) if item.get("run_id") == run_id)
                state = str(run.get("outcome_type") or "RUNNING").lower()
                self._write(202, {"run": {"runHandle": public_run_handle_for_instance(parts[2], run_id, run.get("run_instance_id")), "state": state, "startedAt": run.get("started_at")}, "workspace": self._public_workspace(parts[2])})
            except KeyError: self._write(404, {"error": "Case not found"})
            except EvidenceRequestConflict as exc: self._write(409, {"error": str(exc)})
            except ValueError: self._write(422, {"error": "Assessment could not be admitted from the current case configuration."})
            except RuntimeError: self._write(503, {"error": "Assessment could not be started safely."})
            return
        if self.workflow.run_mode == "vnext" and len(parts) == 4 and parts[0:2] == ["api", "cases"] and parts[3] in {"sources", "students"}:
            try:
                state = self.workflow.repository.require_case(parts[2]); payload = self._read_json(); expected = payload.get("caseRevision")
                if state.case_kind == "sample":
                    self._write(409, {"error": "Sample cases are preconfigured. Reset the sample to restore its original evidence.", "code": "SAMPLE_READ_ONLY"})
                    return
                if parts[3] == "sources":
                    if set(payload) - {"fileName", "content", "mediaType", "caseRevision"}: raise ValueError("Invalid source request")
                    source = self.workflow.add_direct_source(parts[2], display_name=str(payload.get("fileName") or ""), content=str(payload.get("content") or ""), source_type=SourceType.DOCUMENT, metadata={"media_type": payload.get("mediaType") or "text/plain"}, assessment_scope=GraphScope(scope_type="case"), expected_case_revision=expected)
                    self._write(200, {"source": {"sourceHandle": public_source_handle(parts[2], source.id), "fileName": source.name, "documentFormat": document_format(source.name)}, "workspace": self._public_workspace(parts[2])})
                else:
                    if set(payload) - {"displayName", "caseRevision"}: raise ValueError("Invalid student request")
                    student = self.workflow.add_subject(parts[2], {"display_name": str(payload.get("displayName") or "")}, expected)
                    self._write(200, {"student": {"studentHandle": public_student_handle(parts[2], student.subject_id), "displayName": student.display_name, "candidateNumber": student.candidate_number}, "workspace": self._public_workspace(parts[2])})
            except KeyError: self._write(404, {"error": "Case not found"})
            except EvidenceRequestConflict as exc: self._write(409, {"error": str(exc)})
            except ValueError as exc:
                message = str(exc) if "distinct identifier" in str(exc) or "required" in str(exc) else "Invalid student or source request"
                self._write(422, {"error": message})
            return
        if self.workflow.run_mode == "vnext" and len(parts) == 6 and parts[0:2] == ["api", "cases"] and parts[3] == "students" and parts[5] == "rename":
            try:
                state = self.workflow.repository.require_case(parts[2]); payload = self._read_json()
                if state.case_kind == "sample":
                    self._write(409, {"error": "Sample cases are preconfigured. Reset the sample to restore its original evidence.", "code": "SAMPLE_READ_ONLY"})
                    return
                student_id = resolve_student_handle(state, parts[4]); student = self.workflow.rename_subject(parts[2], student_id, str(payload.get("displayName") or ""), payload.get("caseRevision"))
                self._write(200, {"student": {"studentHandle": public_student_handle(parts[2], student.subject_id), "displayName": student.display_name, "candidateNumber": student.candidate_number}, "workspace": self._public_workspace(parts[2])})
            except KeyError: self._write(404, {"error": "Student not found"})
            except EvidenceRequestConflict as exc: self._write(409, {"error": str(exc)})
            except ValueError as exc: self._write(422, {"error": str(exc), "code": "INVALID_STUDENT"})
            return
        if self.workflow.run_mode == "vnext" and parts in (["api", "samples", "open"], ["api", "samples", "reset"]):
            try:
                payload = self._read_json()
                if set(payload) != {"sampleId"}: raise ValueError("Invalid sample request")
                sample_id = str(payload["sampleId"])
                if parts[-1] == "open":
                    case_id = open_sample_case(self.workflow, sample_id)
                else:
                    case_id = reset_sample_case(self.workflow, sample_id)
                    self.workspace_agent.session_store.clear_case(case_id)
                self._write(200, {"caseId": case_id, "workspace": self._public_workspace(case_id)})
            except (KeyError, ValueError): self._write(404, {"error": "Sample not found"})
            except EvidenceRequestConflict as exc: self._write(409, {"error": str(exc), "code": "SAMPLE_BUSY"})
            return
        if parts == ["api", "cases"]:
            try:
                workspace = create_case(self.workflow, self._read_json())
                self._write(201, {"caseId": workspace["caseId"], "workspace": workspace})
            except (ValueError, KeyError) as exc:
                self._write(422, {"error": str(exc)})
            return
        if len(parts) == 4 and parts[:2] == ["api", "samples"] and parts[3] in {"open", "reset"}:
            try:
                payload = self._read_json()
                sample_id = parts[2]
                case_id = str(payload.get("case_id") or f"{sample_id}-working")
                self._write(200, {"caseId": case_id, "workspace": seed_sample_case(self.workflow, sample_id, case_id)})
            except (ValueError, KeyError) as exc:
                self._write(422, {"error": str(exc)})
            return
        if len(parts) == 3 and parts[:2] == ["api", "samples"] and parts[2] == "open":
            try:
                payload = self._read_json()
                sample_id = str(payload.get("sample_id") or "")
                case_id = str(payload.get("case_id") or f"{sample_id}-working")
                self._write(200, {"caseId": case_id, "workspace": seed_sample_case(self.workflow, sample_id, case_id)})
            except (ValueError, KeyError) as exc:
                self._write(422, {"error": str(exc)})
            return
        if len(parts) == 3 and parts[:2] == ["api", "samples"] and parts[2] == "reset":
            try:
                payload = self._read_json()
                sample_id = str(payload.get("sample_id") or "")
                case_id = str(payload.get("case_id") or f"{sample_id}-working")
                self._write(200, {"caseId": case_id, "workspace": seed_sample_case(self.workflow, sample_id, case_id)})
            except (ValueError, KeyError) as exc:
                self._write(422, {"error": str(exc)})
            return
        if len(parts) == 5 and parts[0:2] == ["api", "cases"] and parts[3:5] == ["workspace", "chat"]:
            try:
                payload = WorkspaceChatRequest.model_validate(self._read_json())
                result = self.workspace_agent.chat(parts[2], payload.message)
                self._write(200, {"response": result.response, "actions": result.actions, "recovery": result.recovery})
            except WorkspaceToolAuthorizationError as exc:
                self._write(403, {"error": str(exc)})
            except (ValueError, KeyError, EvidenceRequestConflict) as exc:
                self._write(422, {"error": str(exc)})
            return
        if parts == ["api", "debug", "aws-credentials"]:
            if not debug_credentials_enabled():
                self._write(404, {"error": "Debug credential endpoints are disabled"})
                return
            try:
                payload = self._read_json()
                required = ("aws_access_key_id", "aws_secret_access_key", "aws_session_token")
                if any(not isinstance(payload.get(field), str) or not payload[field] for field in required):
                    raise ValueError("All temporary AWS credential fields are required")
                set_credential_override(CredentialOverride(**{field: payload[field] for field in required}, region_name=payload.get("region_name")))
                self._write(200, credential_status())
            except ValueError as exc:
                self._write(422, {"error": str(exc)})
            return
        if parts == ["api", "debug", "runtime-settings", "models"]:
            if not debug_credentials_enabled():
                self._write(404, {"error": "Debug runtime settings are disabled"})
                return
            try:
                self._write(200, set_model_overrides(self._read_json()))
            except ValueError as exc:
                self._write(422, {"error": str(exc)})
            return
        if len(parts) == 4 and parts[0:2] == ["api", "cases"] and parts[3] == "run":
            try:
                self._write(200, self.workflow.start_run(parts[2]))
            except EvidenceRequestConflict as exc:
                self._write(409, {"error": str(exc)})
            return
        if len(parts) == 4 and parts[0:2] == ["api", "cases"] and parts[3] in {"sources", "subjects", "relationships"}:
            try:
                payload = self._read_json()
                expected_revision = payload.pop("case_revision", None)
                if parts[3] == "sources":
                    if "assessment_scope" in payload:
                        raise ValueError("Source applicability is calculated internally and cannot be supplied through the public upload API")
                    scope = payload.pop("assessment_scope", None)
                    source = self.workflow.add_direct_source(
                        parts[2], display_name=str(payload.pop("display_name", "")), content=str(payload.pop("content", "")),
                        source_type=SourceType(payload.pop("source_type", "other")), metadata=dict(payload.pop("metadata", {}) or {}),
                        assessment_scope=scope, expected_case_revision=expected_revision,
                    )
                    self._write(200, {"source": source.model_dump(mode="json"), "workspace": self.workflow.get_workspace(parts[2])})
                elif parts[3] == "subjects":
                    subject = self.workflow.add_subject(parts[2], payload, expected_revision)
                    self._write(200, {"subject": subject.model_dump(mode="json"), "workspace": self.workflow.get_workspace(parts[2])})
                elif parts[3] == "relationships":
                    if self.workflow.run_mode == "vnext":
                        self._write(404, {"error": "Not found"})
                        return
                    relationship = self.workflow.add_relationship(parts[2], payload, expected_revision)
                    self._write(200, {"relationship": relationship.model_dump(mode="json"), "workspace": self.workflow.get_workspace(parts[2])})
            except (ValueError, KeyError, EvidenceRequestConflict) as exc:
                self._write(409 if isinstance(exc, EvidenceRequestConflict) else 422, {"error": str(exc)})
            return
        if len(parts) == 6 and parts[0:2] == ["api", "cases"] and parts[3] == "subjects" and parts[5] == "rename":
            try:
                payload = self._read_json()
                subject = self.workflow.rename_subject(parts[2], parts[4], str(payload.get("display_name") or ""), payload.get("case_revision"))
                self._write(200, {"student": subject.model_dump(mode="json"), "workspace": self.workflow.get_workspace(parts[2])})
            except (ValueError, EvidenceRequestConflict) as exc:
                self._write(409 if isinstance(exc, EvidenceRequestConflict) else 422, {"error": str(exc)})
            return
        if len(parts) != 6 or parts[0:2] != ["api", "cases"] or parts[3] != "evidence-requests":
            self._write(404, {"error": "Not found"})
            return
        try:
            payload = self._read_json()
            if parts[5] == "fulfil":
                result = self.workflow.respond(parts[2], parts[4], {"request_id": parts[4], "status": "fulfilled", "note": payload.get("note")}, payload.get("sources"), payload.get("case_revision"))
            elif parts[5] == "unavailable":
                result = self.workflow.respond(parts[2], parts[4], {"request_id": parts[4], "status": "unavailable", "note": payload.get("note")}, [], payload.get("case_revision"))
            else:
                self._write(404, {"error": "Not found"})
                return
            self._write(200, self.workflow.get_workspace(parts[2]) | {"completedRequest": result.model_dump(mode="json")})
        except (EvidenceRequestConflict, ValueError, json.JSONDecodeError) as exc:
            self._write(409 if isinstance(exc, EvidenceRequestConflict) else 422, {"error": str(exc)})

    def do_DELETE(self) -> None:
        parts = self._parts()
        if self._is_blocked_legacy_vnext_route(parts):
            self._write(404, {"error": "Not found"})
            return
        if parts == ["api", "runtime-settings", "aws-credentials"]:
            clear_credential_override()
            self._write(200, runtime_settings(workflow=self.workflow))
            return
        if self.workflow.run_mode == "vnext" and len(parts) == 5 and parts[0:2] == ["api", "cases"] and parts[3] == "students":
            try:
                state = self.workflow.repository.require_case(parts[2])
            except KeyError:
                self._write(404, {"error": "Case not found"})
                return
            if state.case_kind == "sample":
                self._write(409, {"error": "Sample cases are preconfigured. Reset the sample to restore its original evidence.", "code": "SAMPLE_READ_ONLY"})
                return
            try:
                student_id = resolve_student_handle(state, parts[4])
            except (KeyError, ValueError):
                self._write(404, {"error": "Student not found"})
                return
            try:
                self.workflow.remove_subject(parts[2], student_id, self._read_json().get("caseRevision")); self._write(200, self._public_workspace(parts[2]))
            except EvidenceRequestConflict as exc:
                self._write(409, {"error": str(exc), "code": "CASE_MUTATION_CONFLICT"})
            except ValueError as exc:
                message = str(exc)
                if "at least one student" in message:
                    self._write(422, {"error": "A case must retain at least one student.", "code": "FINAL_STUDENT_REQUIRED"})
                elif "referenced by a relationship" in message:
                    self._write(409, {"error": "The student is required by an existing relationship.", "code": "STUDENT_REFERENCED"})
                else:
                    self._write(422, {"error": "The student could not be removed.", "code": "INVALID_STUDENT"})
            return
        if len(parts) == 5 and parts[0:2] == ["api", "cases"] and parts[3] == "subjects":
            try:
                payload = self._read_json()
                self.workflow.remove_subject(parts[2], parts[4], payload.get("case_revision"))
                self._write(200, self.workflow.get_workspace(parts[2]))
            except (ValueError, EvidenceRequestConflict) as exc:
                self._write(409 if isinstance(exc, EvidenceRequestConflict) else 422, {"error": str(exc)})
            return
        if parts == ["api", "debug", "runtime-settings", "models"]:
            if not debug_credentials_enabled():
                self._write(404, {"error": "Debug runtime settings are disabled"})
                return
            self._write(200, reset_model_overrides())
            return
        if self._parts() != ["api", "debug", "aws-credentials"]:
            self._write(404, {"error": "Not found"})
            return
        if not debug_credentials_enabled():
            self._write(404, {"error": "Debug credential endpoints are disabled"})
            return
        clear_credential_override()
        self._write(200, credential_status())

    def do_PATCH(self) -> None:
        parts = self._parts()
        if self.workflow.run_mode != "vnext" or len(parts) != 3 or parts[0:2] != ["api", "cases"]:
            self._write(404, {"error": "Not found"})
            return
        try:
            payload = CaseTitleUpdate.model_validate(self._read_json())
            state = self.workflow.update_case_title(parts[2], payload.title)
            self._write(200, {"workspace": self._public_workspace(state.case_id)})
        except KeyError:
            self._write(404, {"error": "Case not found"})
        except EvidenceRequestConflict as exc:
            self._write(409, {"error": str(exc), "code": "CASE_READ_ONLY"})
        except ValueError as exc:
            self._write(422, {"error": str(exc), "code": "INVALID_CASE_NAME"})

    def do_PUT(self) -> None:
        parts = self._parts()
        if self.workflow.run_mode == "vnext":
            self._write(404, {"error": "Not found"})
            return
        if len(parts) != 4 or parts[0:2] != ["api", "cases"] or parts[3] != "context":
            self._write(404, {"error": "Not found"})
            return
        try:
            payload = self._read_json()
            expected_revision = payload.pop("case_revision", None)
            context = self.workflow.update_context(parts[2], payload, expected_revision)
            self._write(200, {"context": context.model_dump(mode="json"), "workspace": self.workflow.get_workspace(parts[2])})
        except (ValueError, KeyError, EvidenceRequestConflict) as exc:
            self._write(409 if isinstance(exc, EvidenceRequestConflict) else 422, {"error": str(exc)})

    def _parts(self) -> list[str]:
        return [unquote(part) for part in urlparse(self.path).path.strip("/").split("/") if part]

    def _is_blocked_legacy_vnext_route(self, parts: list[str]) -> bool:
        """Keep legacy mutation and caller-selected sample routes unreachable in vNext."""
        if self.workflow.run_mode != "vnext":
            return False
        legacy_subjects = (
            len(parts) == 4 and parts[0:2] == ["api", "cases"] and parts[3] == "subjects",
            len(parts) == 5 and parts[0:2] == ["api", "cases"] and parts[3] == "subjects",
            len(parts) == 6 and parts[0:2] == ["api", "cases"] and parts[3] == "subjects" and parts[5] == "rename",
        )
        alternate_sample = len(parts) == 4 and parts[0:2] == ["api", "samples"] and parts[3] in {"open", "reset"}
        return any(legacy_subjects) or alternate_sample

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(payload, dict):
            raise ValueError("JSON request body must be an object")
        return payload

    def _write(self, status: int, payload: dict) -> None:
        origin = self.headers.get("Origin")
        allowed = {item.strip() for item in os.getenv("SIMPLIFYNEXT_ALLOWED_ORIGINS", "http://127.0.0.1:3000,http://localhost:3000").split(",") if item.strip()}
        if origin and origin not in allowed:
            status, payload = 403, {"error": "Origin is not allowed"}
        encoded = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        if origin and origin in allowed:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.end_headers()
        try:
            self.wfile.write(encoded)
        except (BrokenPipeError, ConnectionResetError):
            return

    def _write_bytes(self, status: int, body: bytes, content_type: str, content_disposition: str | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if content_disposition:
            self.send_header("Content-Disposition", content_disposition)
        self._cors_headers()
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            return

    def _cors_headers(self) -> None:
        origin = self.headers.get("Origin")
        allowed = {item.strip() for item in os.getenv("SIMPLIFYNEXT_ALLOWED_ORIGINS", "http://127.0.0.1:3000,http://localhost:3000").split(",") if item.strip()}
        if origin and origin in allowed:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")

    def log_message(self, *_args: object) -> None:
        return


def create_server(repository_root: str | Path = "data/cases", host: str = "127.0.0.1", port: int = 8000, run_callback=None, run_mode: str | None = None) -> ThreadingHTTPServer:
    configured_mode = run_mode or os.environ.get("SIMPLIFYNEXT_RUN_MODE") or ("legacy" if run_callback is not None else "vnext")
    if configured_mode not in {"vnext", "legacy"}:
        raise ValueError("SIMPLIFYNEXT_RUN_MODE must be either 'vnext' or 'legacy'")
    callback = run_callback or (VNextProductionRunner().run if configured_mode == "vnext" else default_production_run)
    workflow = HumanEvidenceWorkflow(CaseRepository(repository_root), run_callback=callback, run_mode=configured_mode)
    workflow.resume_callback = lambda case_id: workflow.start_run(case_id)
    InvestigatorApiHandler.workflow = workflow
    model = MODEL_REGISTRY["anthropic.claude-opus-4-5"]
    workspace_model = runtime_settings()["models"]["workspaceHelp"]["effectiveModel"] if configured_mode == "vnext" else model.name
    workspace_spec = MODEL_REGISTRY[workspace_model]
    InvestigatorApiHandler.workspace_agent = WorkspaceAgent(workflow, BedrockModelClient(model_id=workspace_spec.invocation_id, region=workspace_spec.region))
    return ThreadingHTTPServer((host, port), InvestigatorApiHandler)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", default="data/cases")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    server = create_server(args.repository, args.host, args.port)
    print(f"Investigator API listening on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
