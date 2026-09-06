"""Developer-only bounded live validation of the production vNext path."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from investigator.llm import ModelClient, create_model_client
from investigator.runtime_settings import effective_model
from investigator.services.evidence_requests import HumanEvidenceWorkflow
from investigator.services.vnext_runner import VNextProductionRunner
from investigator.state.repository import CaseRepository


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
    result = VNextProductionRunner(client=client).run(args.case_id, workflow)
    payload = {
        "case_id": args.case_id,
        "provider": "anthropic",
        "model": model_spec.anthropic_model_id,
        "provider_calls": client.calls,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "result": result.model_dump(mode="json"),
    }
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "anthropic_validation.json"
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output_path.resolve()), "provider_calls": client.calls, "model": model_spec.anthropic_model_id}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
