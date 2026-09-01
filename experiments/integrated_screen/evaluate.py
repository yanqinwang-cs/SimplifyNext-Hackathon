from __future__ import annotations

from typing import Any


HARD_FAILURE_PREFIXES = ("FAIL /",)


def evaluate_trajectory(result: dict[str, Any]) -> dict[str, Any]:
    """Hybrid mechanical screen; semantic checks stay broad and auditable."""
    failures = [trace["error"] for trace in result.get("traces", []) if trace.get("error")]
    if result.get("termination", "").startswith(HARD_FAILURE_PREFIXES):
        return {"outcome": "FAIL", "hard_failures": failures or [result["termination"]], "manual_review": []}
    return {"outcome": "NEEDS_MANUAL_REVIEW", "hard_failures": failures, "manual_review": ["Assess broad fixture trajectory properties from the trace."]}

