"""Start the real vNext HTTP server with a deterministic test-only provider."""

from __future__ import annotations

import argparse
import json
import re
import threading
import os
import time
from pathlib import Path
from typing import Any

from investigator import http_api
from botocore.exceptions import ReadTimeoutError
from investigator.llm import ModelCallMetadata, ModelCallResult, ModelNativeCall, ModelTextBlock
from investigator.model_registry import MODEL_REGISTRY
from investigator.vnext import (
    AssessmentStatus,
    Confidence,
    FurthestJustifiedConclusion,
    InvestigatorAssessment,
    InvestigatorProposal,
    SubjectAssessment,
    ViolationAssessment,
)
from investigator.services.vnext_runner import VNextProductionRunner


class Stage7FakeModelClient:
    """Schema-valid provider-boundary fake; it never falls back to Bedrock."""

    def __init__(self, model_id: str | None = None, region: str | None = None, client: Any = None, log_path: Path | None = None) -> None:
        self.model_id = model_id or "stage7.fake"
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
        match = re.search(re.escape(marker) + r"\n(\[.*?\]|\{.*?\})\n", prompt, flags=re.DOTALL)
        return json.loads(match.group(1)) if match else None

    def _assessment(self, prompt: str) -> InvestigatorAssessment:
        student_ids = self._json_after(prompt, "EXPECTED CONFIGURED STUDENT IDS") or ["subject_1"]
        violation_ids = [
            "unauthorized_device", "unauthorized_external_communication", "unauthorized_assistance", "prohibited_collaboration"
        ]
        return InvestigatorAssessment(
            proposal=InvestigatorProposal(),
            subject_assessments=[
                SubjectAssessment(
                    subject_id=student_id,
                    violation_assessments=[
                        ViolationAssessment(
                            violation_id=violation_id,
                            status=AssessmentStatus.NOT_CURRENTLY_SUPPORTED,
                            supporting_node_ids=[],
                            mitigating_node_ids=[],
                            unresolved_points=["The controlled acceptance provider supplies no supporting finding."],
                            reasoning_summary="Controlled offline acceptance assessment.",
                            confidence=Confidence.LOW,
                        )
                        for violation_id in violation_ids
                    ],
                    furthest_conclusion=FurthestJustifiedConclusion(
                        statement="The available record does not currently support a violation.",
                        based_on_violation_ids=[],
                        confidence=Confidence.LOW,
                    ),
                )
                for student_id in student_ids
            ],
        )

    def call(self, input_data: Any, output_schema: type[Any]) -> ModelCallResult:
        self._record("structured")
        if os.getenv("STAGE7_FAKE_TIMEOUT") == "1" and "TIMEOUT_SENTINEL" in str(input_data):
            raise ReadTimeoutError(
                endpoint_url="https://bedrock-runtime.us-east-1.amazonaws.com/model/fake/converse",
                error="PRE5A_AUDIT_ACCESS PRE5A_AUDIT_SECRET PRE5A_AUDIT_SESSION",
            )
        if self.delay_seconds:
            time.sleep(self.delay_seconds)
        parsed = self._assessment(str(input_data))
        return ModelCallResult(
            parsed=output_schema.model_validate(parsed),
            metadata=ModelCallMetadata(
                provider="stage7-fake",
                model=self.model_id,
                input_tokens=24,
                output_tokens=32,
                latency_seconds=0.001,
                parse_success=True,
                finish_reason="stop",
            ),
            raw_output=parsed.model_dump(mode="json"),
        )

    def call_native(self, input_data: Any, tools: list[dict[str, Any]]) -> ModelNativeCall:
        self._record("native")
        return ModelNativeCall(
            text_blocks=[ModelTextBlock(text="The controlled Workspace Help provider returned a read-only response.")],
            tool_uses=[],
            metadata=ModelCallMetadata(
                provider="stage7-fake",
                model=self.model_id,
                input_tokens=12,
                output_tokens=12,
                latency_seconds=0.001,
                parse_success=True,
                finish_reason="stop",
            ),
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    log_path = args.repository / ".stage7-provider-calls.json"
    default_spec = MODEL_REGISTRY["anthropic.claude-sonnet-4-5"]
    client = Stage7FakeModelClient(default_spec.invocation_id, default_spec.region, log_path=log_path)
    http_api.BedrockModelClient = Stage7FakeModelClient
    callback = VNextProductionRunner(client=client).run
    server = http_api.create_server(args.repository, args.host, args.port, run_callback=callback, run_mode="vnext")
    print(f"Stage 7 fake API listening on http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
