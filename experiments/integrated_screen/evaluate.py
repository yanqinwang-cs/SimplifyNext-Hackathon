from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TrajectoryRequirements(BaseModel):
    model_config = ConfigDict(extra="forbid")
    required_release_ids: list[str] = Field(default_factory=list)
    required_action_ids: list[str] = Field(default_factory=list)
    forbidden_actions_after_release: dict[str, list[str]] = Field(default_factory=dict)
    required_visible_evidence_ids: list[str] = Field(default_factory=list)
    require_material_graph_change_after_release: bool = False
    require_stop_unresolved: bool = False
    require_trusted_exhaustion_for_stop: bool = True
    qualitative_checks: list[str] = Field(default_factory=list)


HARD_FAILURE_PREFIXES = ("FAIL /",)


def evaluate_trajectory(result: dict[str, Any], requirements: TrajectoryRequirements | None = None) -> dict[str, Any]:
    """Evaluate mechanical trajectory properties without requiring exact paths."""
    requirements = requirements or TrajectoryRequirements()
    traces = result.get("traces", [])
    hard_failures = list(result.get("hard_failures", []))
    hard_failures.extend(trace["failure_category"] for trace in traces if trace.get("failure_category"))
    if result.get("termination", "").startswith(HARD_FAILURE_PREFIXES):
        hard_failures.append(result["termination"])
    releases = {evidence["id"] for trace in traces for evidence in (trace.get("environment_release") or [])}
    actions = set(result.get("completed_action_ids", []))
    visible = {identifier for trace in traces for identifier in trace.get("recently_released_evidence_ids", [])}
    failures = []
    for identifier in requirements.required_release_ids:
        if identifier not in releases: failures.append(f"required release missing: {identifier}")
    for identifier in requirements.required_action_ids:
        if identifier not in actions: failures.append(f"required action missing: {identifier}")
    for release_id, forbidden in requirements.forbidden_actions_after_release.items():
        released = False
        for trace in traces:
            if released and set(trace.get("completed_action_ids", [])) & set(forbidden): failures.append(f"forbidden action executed after release: {release_id}")
            if release_id in {item["id"] for item in (trace.get("environment_release") or [])}: released = True
    for identifier in requirements.required_visible_evidence_ids:
        if identifier not in visible: failures.append(f"released evidence was not visible in an Investigator observation: {identifier}")
    if requirements.require_stop_unresolved and result.get("termination") != "STOP_UNRESOLVED": failures.append("STOP_UNRESOLVED was not reached")
    if requirements.require_stop_unresolved and requirements.require_trusted_exhaustion_for_stop:
        stop_contexts = [trace for trace in traces if trace.get("actor") == "steward" and trace.get("steward_decision", {}).get("operation") == "stop_unresolved"]
        if not stop_contexts or stop_contexts[-1].get("materially_usable_action_ids_after"): failures.append("STOP_UNRESOLVED occurred before trusted useful-frontier exhaustion")
    hard_failures.extend(failures)
    if hard_failures:
        return {"outcome": "FAIL", "hard_failures": hard_failures, "manual_review": []}
    if requirements.qualitative_checks:
        return {"outcome": "NEEDS_MANUAL_REVIEW", "hard_failures": [], "manual_review": requirements.qualitative_checks}
    return {"outcome": "PASS", "hard_failures": [], "manual_review": []}
