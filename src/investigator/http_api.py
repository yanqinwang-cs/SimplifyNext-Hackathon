"""Minimal standard-library HTTP API for the investigator workspace."""

import argparse
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from investigator.services.evidence_requests import EvidenceRequestConflict, HumanEvidenceWorkflow
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


def sample_cases() -> list[dict[str, str]]:
    return [
        {"id": "law-exam", "title": "Law Exam Investigation", "description": "A rich single-case investigation with multiple records."},
        {"id": "multi-candidate", "title": "Multi-Candidate Collaboration Review", "description": "A controlled five-subject assessment."},
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
    samples = {item["id"]: item for item in sample_cases()}
    if sample_id not in samples:
        raise ValueError(f"Unknown sample case: {sample_id!r}")
    fixture_root = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "public_samples"
    if sample_id == "law-exam":
        source_root = fixture_root / "law_exam" / "sources"
        sources = {f"S{index}": Source(id=f"S{index}", name=path.name, source_type=SourceType.DOCUMENT, content=path.read_text(encoding="utf-8"), metadata={"filename": path.name, "assessment_scope": GraphScope(scope_type="case").model_dump(mode="json")}) for index, path in enumerate(sorted(source_root.glob("*.md")), start=1)}
        state = CaseState(case_id=case_id, title="Law Exam Investigation", description=samples[sample_id]["description"], assessment_context=AssessmentContext(assessment_id=f"{case_id}-assessment", title="Business Law Individual In-Class Assessment 2", assessment_type="closed-notes individual assessment"), subjects={"subject_A": AssessmentSubject(subject_id="subject_A", display_name="Candidate A", candidate_number="BL-041")}, sources=sources)
    elif sample_id == "multi-candidate":
        source_root = fixture_root / "multi_candidate" / "sources"
        subjects = {f"subject_{letter}": AssessmentSubject(subject_id=f"subject_{letter}", display_name=f"Candidate {letter}", candidate_number=number) for letter, number in (("A", "BL-041"), ("B", "BL-073"), ("C", "BL-118"), ("D", "BL-162"), ("E", "BL-205"))}
        sources = {}
        for index, path in enumerate(sorted(source_root.glob("*.md")), start=1):
            letter = next((candidate for candidate in "ABCDE" if f"candidate_{candidate}_" in path.name), None)
            scope = GraphScope(scope_type="subject", subject_id=f"subject_{letter}") if letter else GraphScope(scope_type="case")
            sources[f"S{index}"] = Source(id=f"S{index}", name=path.name, source_type=SourceType.DOCUMENT, content=path.read_text(encoding="utf-8"), metadata={"filename": path.name, "assessment_scope": scope.model_dump(mode="json")})
        state = CaseState(case_id=case_id, title="Multi-Candidate Collaboration Review", description=samples[sample_id]["description"], assessment_context=AssessmentContext(assessment_id=f"{case_id}-assessment", title="Business Law Individual In-Class Assessment 2", assessment_type="closed-notes individual assessment", venue="Seminar Room 4"), subjects=subjects, subject_relationships={"rel_A_B_adjacent": SubjectRelationship(relationship_id="rel_A_B_adjacent", subject_ids=["subject_A", "subject_B"], relationship_type="adjacent_seating", description="Candidate A and Candidate B were seated next to one another.")}, sources=sources)
    else:
        raise ValueError(f"Unknown sample case: {sample_id!r}")
    workflow.repository.save(state)
    workflow.record_workspace_event(case_id, {"type": "case_created", "case_revision": state.revision, "human_summary": f"Sample case {state.title} was opened."})
    return workflow.get_workspace(case_id)


class InvestigatorApiHandler(BaseHTTPRequestHandler):
    workflow: HumanEvidenceWorkflow
    workspace_agent: WorkspaceAgent

    def do_GET(self) -> None:
        parts = self._parts()
        if parts == ["api", "cases"]:
            cases = []
            for case_id in self.workflow.repository.list_case_ids():
                state = self.workflow.repository.load(case_id)
                if case_id == "case-01" and state.title == "Business Law Tutorial 5":
                    continue
                runs = self.workflow.get_runs(case_id)
                cases.append({"case_id": case_id, "title": state.title, "last_updated_at": state.last_updated_at.isoformat(), "revision": state.revision, "subject_count": len(state.subjects), "latest_assessment_status": runs[-1].get("vnext_status") if runs else None})
            self._write(200, {"cases": cases})
            return
        if parts == ["api", "product-guide"]:
            body = product_guide().encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/markdown; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
            return
        if parts == ["api", "debug", "aws-credentials", "status"]:
            if not debug_credentials_enabled():
                self._write(404, {"error": "Debug credential endpoints are disabled"})
            else:
                self._write(200, credential_status())
            return
        if len(parts) == 4 and parts[0:2] == ["api", "cases"] and parts[3] == "workspace":
            self._write(200, self.workflow.get_workspace(parts[2]) | {"chatHistory": self.workspace_agent.chat_history(parts[2]), "guidance": self.workflow.get_guidance_context(parts[2])})
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
            try:
                body = self.workflow.sanitized_raw_trace(parts[2], parts[4])
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.send_header("Content-Disposition", 'inline; filename="raw_traces.jsonl"')
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)
            except (KeyError, ValueError):
                self._write(404, {"error": "Run not found"})
            return
        self._write(404, {"error": "Not found"})

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_POST(self) -> None:
        parts = self._parts()
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
            except (ValueError, EvidenceRequestConflict) as exc:
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
                else:
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
        if len(parts) == 5 and parts[0:2] == ["api", "cases"] and parts[3] == "subjects":
            try:
                payload = self._read_json()
                self.workflow.remove_subject(parts[2], parts[4], payload.get("case_revision"))
                self._write(200, self.workflow.get_workspace(parts[2]))
            except (ValueError, EvidenceRequestConflict) as exc:
                self._write(409 if isinstance(exc, EvidenceRequestConflict) else 422, {"error": str(exc)})
            return
        if self._parts() != ["api", "debug", "aws-credentials"]:
            self._write(404, {"error": "Not found"})
            return
        if not debug_credentials_enabled():
            self._write(404, {"error": "Debug credential endpoints are disabled"})
            return
        clear_credential_override()
        self._write(200, credential_status())

    def do_PUT(self) -> None:
        parts = self._parts()
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

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(payload, dict):
            raise ValueError("JSON request body must be an object")
        return payload

    def _write(self, status: int, payload: dict) -> None:
        encoded = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(encoded)

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
    workspace_model = os.environ.get("WORKSPACE_MODEL_ID") or (MODEL_REGISTRY["anthropic.claude-haiku-4-5"].invocation_id if configured_mode == "vnext" else model.invocation_id)
    InvestigatorApiHandler.workspace_agent = WorkspaceAgent(workflow, BedrockModelClient(model_id=workspace_model, region=model.region))
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
