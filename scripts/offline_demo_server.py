"""Run the real vNext HTTP API offline, with deterministic in-process fakes for every model call.

Assessments and Workspace Help replies come from the fake below, so no provider key is read and no paid call is made.
Used by the browser tests and by anyone who wants to try the product without a key.
"""

from __future__ import annotations

import argparse
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from botocore.exceptions import ReadTimeoutError

from investigator import http_api
from investigator.llm import ModelCallMetadata, ModelCallResult, ModelNativeCall, ModelTextBlock
from investigator.model_registry import MODEL_REGISTRY
from investigator.services.vnext_runner import VNextProductionRunner
from investigator.vnext import AssessmentStatus, Confidence
from investigator.vnext.semantic import (
    InvestigatorSemanticAssessment,
    SemanticSubjectAssessment,
    SemanticViolationAssessment,
)
from investigator.workspace_agent import WorkspaceAgent

STUDENTS_MARKER = "\nEXPECTED CONFIGURED STUDENT IDS\n"
RULE_PRESET_MARKER = "\nAPPLICABLE POLICY / CONFIGURED VIOLATIONS (NORMATIVE CONTEXT, NOT FACTUAL EVIDENCE)\n"


class OfflineDemoModelClient:
    """Schema-valid provider-boundary fake; it never reads credentials and never calls a provider."""

    def __init__(self, model_id: str | None = None, region: str | None = None, log_path: Path | None = None) -> None:
        self.model_id = model_id or "offline-demo.fake"
        self.region = region or "us-east-1"
        self.log_path = log_path
        self.delay_seconds = float(os.getenv("STAGE7_FAKE_DELAY_SECONDS", "0"))
        self._lock = threading.Lock()
        if log_path and not log_path.exists():
            self._write_log([])

    def _write_log(self, calls: list[dict[str, Any]]) -> None:
        if self.log_path:
            self.log_path.write_text(json.dumps(calls, indent=2) + "\n", encoding="utf-8")

    def _record(self, kind: str) -> None:
        if not self.log_path:
            return
        with self._lock:
            try:
                calls = json.loads(self.log_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                calls = []
            calls.append({"kind": kind, "model": self.model_id, "region": self.region})
            self._write_log(calls)

    @staticmethod
    def _json_after(prompt: str, marker: str) -> Any:
        """Decode the JSON value that the prompt places directly after a marker line."""
        start = prompt.find(marker)
        if start < 0:
            return None
        value, _ = json.JSONDecoder().raw_decode(prompt, start + len(marker))
        return value

    def _assessment(self, prompt: str) -> InvestigatorSemanticAssessment:
        student_ids = self._json_after(prompt, STUDENTS_MARKER) or ["case_subject"]
        rule_preset = self._json_after(prompt, RULE_PRESET_MARKER) or {}
        violation_ids = [violation["violation_id"] for violation in rule_preset.get("violations", [])]
        return InvestigatorSemanticAssessment(
            subject_assessments=[
                SemanticSubjectAssessment(
                    subject_id=student_id,
                    violation_assessments=[
                        SemanticViolationAssessment(
                            violation_id=violation_id,
                            status=AssessmentStatus.NOT_CURRENTLY_SUPPORTED,
                            unresolved_points=["The offline demo provider supplies no supporting finding."],
                            reasoning_summary="Offline demo assessment; no provider was consulted.",
                            confidence=Confidence.LOW,
                        )
                        for violation_id in violation_ids
                    ],
                    furthest_conclusion={
                        "statement": "The available record does not currently support a violation.",
                        "confidence": "low",
                    },
                )
                for student_id in student_ids
            ],
        )

    def call(self, input_data: Any, output_schema: type[Any]) -> ModelCallResult:
        self._record("structured")
        if os.getenv("STAGE7_FAKE_TIMEOUT") == "1" and "TIMEOUT_SENTINEL" in str(input_data):
            raise ReadTimeoutError(
                endpoint_url="https://bedrock-runtime.us-east-1.amazonaws.com/model/fake/converse",
                error="OFFLINE_DEMO_TIMEOUT",
            )
        if self.delay_seconds:
            time.sleep(self.delay_seconds)
        raw_output = self._assessment(str(input_data)).model_dump(mode="json")
        return ModelCallResult(
            parsed=output_schema.model_validate(raw_output),
            metadata=ModelCallMetadata(
                provider="offline-demo",
                model=self.model_id,
                input_tokens=24,
                output_tokens=32,
                latency_seconds=0.001,
                parse_success=True,
                finish_reason="stop",
            ),
            raw_output=raw_output,
        )

    def call_native(self, messages: Any, tools: list[dict[str, Any]]) -> ModelNativeCall:
        self._record("native")
        return ModelNativeCall(
            text_blocks=[ModelTextBlock(text="The controlled Workspace Help provider returned a read-only response.")],
            tool_uses=[],
            metadata=ModelCallMetadata(
                provider="offline-demo",
                model=self.model_id,
                input_tokens=12,
                output_tokens=12,
                latency_seconds=0.001,
                parse_success=True,
                finish_reason="stop",
            ),
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    log_path = args.repository / ".stage7-provider-calls.json"
    default_spec = MODEL_REGISTRY["anthropic.claude-sonnet-4-5"]
    client = OfflineDemoModelClient(default_spec.invocation_id, default_spec.region, log_path=log_path)
    server = http_api.create_server(
        args.repository, args.host, args.port, run_callback=VNextProductionRunner(client=client).run
    )
    # Help gets the same offline client; otherwise it would build a real provider client from the environment.
    http_api.InvestigatorApiHandler.workspace_agent = WorkspaceAgent(
        http_api.InvestigatorApiHandler.workflow, client=client
    )
    print(f"Offline demo API listening on http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
