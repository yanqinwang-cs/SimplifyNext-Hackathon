from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


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

    @model_validator(mode="after")
    def material_change_has_release_anchor(self) -> "TrajectoryRequirements":
        if self.require_material_graph_change_after_release and not self.required_release_ids:
            raise ValueError("material graph change requires at least one required release ID")
        return self


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
    visible = {identifier for trace in traces for identifier in trace.get("visible_released_evidence_ids", [])}
    failures = []
    for identifier in requirements.required_release_ids:
        if identifier not in releases: failures.append(f"required release missing: {identifier}")
    for identifier in requirements.required_action_ids:
        if identifier not in actions: failures.append(f"required action missing: {identifier}")
    for release_id, forbidden in requirements.forbidden_actions_after_release.items():
        anchor = next((index for index, trace in enumerate(traces) if release_id in {item["id"] for item in (trace.get("environment_release") or [])}), None)
        if anchor is not None:
            for trace in traces[anchor + 1:]:
                if trace.get("executed_action_id") in forbidden:
                    failures.append(f"forbidden action executed after release: {release_id}")
    for identifier in requirements.required_visible_evidence_ids:
        if identifier not in visible: failures.append(f"released evidence was not visible in an Investigator observation: {identifier}")
    if requirements.require_material_graph_change_after_release:
        anchors = [index for index, trace in enumerate(traces) if any(item["id"] in requirements.required_release_ids for item in (trace.get("environment_release") or []))]
        if anchors:
            anchor = anchors[0]
            changed = any(
                trace.get("actor") in {"investigator", "steward"}
                and (trace.get("graph_fingerprint_before") != trace.get("graph_fingerprint_after") or trace.get("focus_before") != trace.get("focus_after") or any(trace.get("graph_delta", {}).get(key) for key in ("added_node_ids", "added_edge_ids", "removed_edge_ids", "node_status_changes")))
                for trace in traces[anchor + 1:]
            )
            if not changed:
                failures.append("no material graph or focus change occurred after required release")
    if requirements.require_stop_unresolved and result.get("termination") != "STOP_UNRESOLVED": failures.append("STOP_UNRESOLVED was not reached")
    if requirements.require_stop_unresolved and requirements.require_trusted_exhaustion_for_stop:
        stop_contexts = [trace for trace in traces if trace.get("actor") == "steward" and trace.get("steward_decision", {}).get("operation") == "stop_unresolved"]
        context = stop_contexts[-1].get("steward_review_context") if stop_contexts else None
        if not context or not context.get("global_frontier_assessed") or context.get("materially_usable_action_ids") or context.get("obvious_useful_region_remains") or not context.get("local_frontier_exhausted"):
            failures.append("STOP_UNRESOLVED occurred without trusted global-frontier assessment")
    hard_failures.extend(failures)
    if hard_failures:
        return {"outcome": "FAIL", "hard_failures": hard_failures, "manual_review": []}
    if requirements.qualitative_checks:
        return {"outcome": "NEEDS_MANUAL_REVIEW", "hard_failures": [], "manual_review": requirements.qualitative_checks}
    return {"outcome": "PASS", "hard_failures": [], "manual_review": []}
