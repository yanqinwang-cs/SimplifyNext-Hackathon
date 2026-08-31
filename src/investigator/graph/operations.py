from collections.abc import Mapping

from investigator.graph.models import EpistemicStatus


def propagate_epistemic_status(statuses: Mapping[str, EpistemicStatus], parent_by_child: Mapping[str, str], node_id: str, status: EpistemicStatus) -> dict[str, EpistemicStatus]:
    """Apply only explicit asymmetric specialization propagation rules."""
    updated = dict(statuses)
    updated[node_id] = status
    if status is EpistemicStatus.ESTABLISHED:
        current = node_id
        while current in parent_by_child:
            current = parent_by_child[current]
            updated[current] = EpistemicStatus.ESTABLISHED
    elif status is EpistemicStatus.REJECTED:
        stack = [child for child, parent in parent_by_child.items() if parent == node_id]
        while stack:
            child = stack.pop()
            updated[child] = EpistemicStatus.REJECTED
            stack.extend(grandchild for grandchild, parent in parent_by_child.items() if parent == child)
    return updated
