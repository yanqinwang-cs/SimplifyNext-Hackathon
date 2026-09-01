"""Offline Luna producer used for prompt calibration.

This is deliberately a small structural producer, not a second contract
implementation: it consumes the same scenario state as the real screen and
returns exactly one JSON-shaped decision.  It never reads expected answers.
"""

from investigator.graph import EdgeRelation, GraphStatus

from experiments.steward_screen.models import StewardScenario


def produce(prompt: str, scenario: StewardScenario) -> dict:
    """Return one decision from visible graph state and trusted context only."""
    assert prompt and "<OUTPUT_CONTRACT>" in prompt
    graph = scenario.graph
    focus = scenario.focus.node_id
    current = graph.nodes[focus]

    if scenario.review_context is not None:
        review = scenario.review_context
        if (review.global_frontier_assessed and review.local_frontier_exhausted
                and not review.neglected_candidate_node_ids
                and not review.materially_usable_action_ids
                and not review.obvious_useful_region_remains):
            return {"operation": "stop_unresolved", "assessment": "No trusted useful frontier remains.", "reason": "The trusted frontier review is exhausted.", "important_unresolved_ids": review.active_unresolved_ids[:1], "reopening_conditions": "Reopen on materially relevant evidence."}

    archived_relevant = [node.id for node in graph.nodes.values() if node.status is GraphStatus.ARCHIVED and any(edge.target_id == node.id or edge.source_id == node.id for edge in graph.edges.values())]
    if archived_relevant:
        target = sorted(archived_relevant)[0]
        return {"operation": "reactivate", "assessment": "Archived material is structurally relevant.", "reason": "Active graph relations make the archived object relevant.", "target_node_id": target}

    parents = [edge.target_id for edge in graph.edges.values() if edge.source_id == focus and edge.relation is EdgeRelation.SPECIALIZES and graph.nodes[edge.target_id].status is GraphStatus.ACTIVE]
    if parents:
        return {"operation": "generalize", "assessment": "The specific active focus has a viable active parent.", "reason": "Move one level to the immediate active SPECIALIZES parent.", "target_node_id": focus}

    protected = {focus, *scenario.focus.recent_node_ids}
    active_unsupported = [node.id for node in graph.nodes.values() if node.id not in protected and node.status is GraphStatus.ACTIVE and not any(edge.source_id == node.id or edge.target_id == node.id for edge in graph.edges.values())]
    if active_unsupported:
        return {"operation": "archive", "assessment": "An unrelated active object has no useful relation.", "reason": "Remove the stale object from active reasoning.", "target_node_id": sorted(active_unsupported)[0]}

    candidates = [node.id for node in graph.nodes.values() if node.id != focus and node.status is GraphStatus.ACTIVE]
    if candidates and not any(edge.source_id == focus or edge.target_id == focus for edge in graph.edges.values()):
        return {"operation": "shift_focus", "assessment": "Another active object is the remaining useful destination.", "reason": "Redirect focus to an existing active object.", "destination_node_id": sorted(candidates)[0]}

    return {"operation": "keep_focus", "assessment": "The current focus remains useful.", "reason": "No separate graph-management change is required."}
