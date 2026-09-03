"""Minimal standard-library HTTP API for the investigator workspace."""

import argparse
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
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


def sample_cases() -> list[dict[str, str]]:
    return [
        {"id": "smart-device", "title": "Smart-device concern", "description": "A focused single-subject assessment."},
        {"id": "possible-collaboration", "title": "Possible collaboration", "description": "A two-subject assessment with ambiguous evidence."},
        {"id": "multi-candidate", "title": "Multi-candidate assessment", "description": "An advanced five-subject assessment."},
    ]


class InvestigatorApiHandler(BaseHTTPRequestHandler):
    workflow: HumanEvidenceWorkflow
    workspace_agent: WorkspaceAgent

    def do_GET(self) -> None:
        parts = self._parts()
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
