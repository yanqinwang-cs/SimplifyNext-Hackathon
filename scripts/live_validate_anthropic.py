"""Developer-only bounded live validation of the production vNext path."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from investigator.llm import ModelClient, create_model_client, redact_sensitive_text
from investigator.runtime_settings import effective_model
from investigator.services.evidence_requests import HumanEvidenceWorkflow
from investigator.services.vnext_runner import VNextProductionRunner
from investigator.state.repository import CaseRepository
from investigator.vnext.presets import preset_for_case


class LiveCallBudgetExceeded(RuntimeError):
    pass


class BudgetedModelClient:
    def __init__(self, client: ModelClient, limit: int) -> None:
        self.client = client
        self.limit = limit
        self.calls = 0

    def _permit(self) -> None:
        if self.calls >= self.limit:
            raise LiveCallBudgetExceeded(f"Anthropic provider-call budget exceeded ({self.limit})")
        self.calls += 1

    def call(self, input_data: Any, output_schema: Any):
        self._permit()
        return self.client.call(input_data, output_schema)

    def call_native(self, input_data: Any, tools: list[dict[str, Any]]):
        self._permit()
        return self.client.call_native(input_data, tools)


def admit_vnext_run(workflow: HumanEvidenceWorkflow, case_id: str) -> tuple[str, Path]:
    """Create the same durable admission artifacts before any provider call."""
    state = workflow.repository.require_case(case_id)
    run_id = workflow.begin_run(case_id, start_revision=state.revision, preset=preset_for_case(state))
    workflow.set_runtime(case_id, "RUNNING", "INVESTIGATOR")
    directory = workflow.repository.run_dir(case_id, run_id)
    if (
        workflow.current_run_id(case_id) != run_id
        or not directory.is_dir()
        or not (directory / "assessment_input_snapshot.json").is_file()
        or not (directory / "run_result.json").is_file()
        or workflow.repository.require_case(case_id).runtime_status != "RUNNING"
    ):
        workflow.set_runtime(case_id, "FAILED", "NONE", failure_category="RUN_ADMISSION", message="Required vNext run artifacts were not admitted")
        workflow.finalize_run(case_id, expected_run_id=run_id, termination_reason="admission_failed")
        raise RuntimeError("vNext run admission did not produce the required active artifacts")
    return run_id, directory


def run_admitted_validation(workflow: HumanEvidenceWorkflow, case_id: str, client: ModelClient) -> tuple[Any, Path]:
    """Run the production runner synchronously and finalize its admitted run."""
    _, directory = admit_vnext_run(workflow, case_id)
    try:
        result = VNextProductionRunner(client=client).run(case_id, workflow)
    except Exception as exc:
        workflow.set_runtime(case_id, "FAILED", "NONE", failure_category=type(exc).__name__, message=str(exc))
        workflow.finalize_run(case_id, termination_reason="failed", final_error={"type": type(exc).__name__, "message": str(exc)})
        raise
    workflow.set_runtime(case_id, "IDLE", "NONE")
    workflow.finalize_run(case_id, termination_reason="completed")
    return result, directory


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="required acknowledgement that this makes paid provider calls")
    parser.add_argument("--case-id", default="case-01")
    parser.add_argument("--repository", default="data/cases")
    parser.add_argument("--output-dir", default="artifacts/live-validation")
    parser.add_argument("--max-provider-calls", type=int, default=2)
    args = parser.parse_args()
    if not args.live:
        parser.error("--live is required; this script never calls Anthropic without explicit acknowledgement")
    if args.max_provider_calls <= 0:
        parser.error("--max-provider-calls must be a positive integer")

    repository = CaseRepository(args.repository)
    repository.require_case(args.case_id)
    model_spec = effective_model("investigator")
    client = BudgetedModelClient(create_model_client(model_spec, provider="anthropic"), args.max_provider_calls)
    workflow = HumanEvidenceWorkflow(repository, run_mode="vnext")
    result = None
    directory = None
    error = None
    try:
        result, directory = run_admitted_validation(workflow, args.case_id, client)
    except Exception as exc:
        error = {"type": type(exc).__name__, "message": redact_sensitive_text(str(exc))}
        runs = workflow.get_runs(args.case_id)
        if runs:
            directory = repository.run_dir(args.case_id, str(runs[-1]["run_id"]))
    payload = {
        "case_id": args.case_id,
        "provider": "anthropic",
        "model": model_spec.anthropic_model_id,
        "provider_calls": client.calls,
        "run_directory": str(directory.resolve()) if directory else None,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "error": error,
        "result": result.model_dump(mode="json") if result else None,
    }
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "anthropic_validation.json"
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output_path.resolve()), "provider_calls": client.calls, "model": model_spec.anthropic_model_id}, indent=2))
    return 1 if error else 0


if __name__ == "__main__":
    raise SystemExit(main())
