from __future__ import annotations

from typing import Any


def evaluate_run(result: dict[str, Any], *, max_model_calls: int, max_steps: int) -> dict[str, Any]:
    traces = result.get("traces", [])
    failures: list[str] = []
    if result.get("model_calls", 0) > max_model_calls:
        failures.append("model-call budget exceeded")
    if result.get("orchestration_steps", 0) > max_steps:
        failures.append("orchestration-step budget exceeded")
    for trace in traces:
        if trace.get("actor") in {"investigator", "steward"} and not trace.get("model_attempts"):
            failures.append(f"missing model attempts at step {trace.get('step')}")
    status = result.get("termination_reason", "INTERNAL_ERROR")
    if failures:
        status = "INTERNAL_ERROR"
    elif status == "HANDOFF_TO_HUMAN":
        status = "COMPLETED_HANDOFF"
    elif status == "BUDGET_EXHAUSTED":
        status = "BUDGET_EXHAUSTED"
    elif any(trace.get("failure_category") == "INVESTIGATOR_SCHEMA" for trace in traces):
        status = "SCHEMA_FAILURE"
    elif any(trace.get("failure_category") == "MODEL_ERROR" for trace in traces):
        status = "MODEL_ERROR"
    return {"status": status, "mechanical_failures": failures, "hidden_truth_used": False, "trace_count": len(traces)}
