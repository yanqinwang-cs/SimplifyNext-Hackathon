"""Minimal standard-library HTTP API for the investigator workspace."""

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from investigator.services.evidence_requests import EvidenceRequestConflict, HumanEvidenceWorkflow
from investigator.services.production_runner import default_production_run
from investigator.llm.bedrock import CredentialOverride, clear_credential_override, credential_status, debug_credentials_enabled, set_credential_override
from investigator.state.repository import CaseRepository


class InvestigatorApiHandler(BaseHTTPRequestHandler):
    workflow: HumanEvidenceWorkflow

    def do_GET(self) -> None:
        parts = self._parts()
        if parts == ["api", "debug", "aws-credentials", "status"]:
            if not debug_credentials_enabled():
                self._write(404, {"error": "Debug credential endpoints are disabled"})
            else:
                self._write(200, credential_status())
            return
        if len(parts) == 4 and parts[0:2] == ["api", "cases"] and parts[3] == "workspace":
            self._write(200, self.workflow.get_workspace(parts[2]))
            return
        if len(parts) == 4 and parts[0:2] == ["api", "cases"] and parts[3] == "traces":
            self._write(200, {"caseId": parts[2], "traces": self.workflow.get_traces(parts[2])})
            return
        if len(parts) == 4 and parts[0:2] == ["api", "cases"] and parts[3] == "runs":
            self._write(200, {"caseId": parts[2], "runs": self.workflow.get_runs(parts[2])})
            return
        if len(parts) == 6 and parts[0:2] == ["api", "cases"] and parts[3] == "runs" and parts[5] == "raw-traces":
            try:
                path = self.workflow.raw_trace_path(parts[2], parts[4])
                body = path.read_bytes()
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


def create_server(repository_root: str | Path = "data/cases", host: str = "127.0.0.1", port: int = 8000, run_callback=None) -> ThreadingHTTPServer:
    workflow = HumanEvidenceWorkflow(CaseRepository(repository_root), run_callback=run_callback or default_production_run)
    workflow.resume_callback = lambda case_id: workflow.start_run(case_id)
    InvestigatorApiHandler.workflow = workflow
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
