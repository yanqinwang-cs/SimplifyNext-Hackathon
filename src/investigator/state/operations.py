from investigator.models.hypothesis import (
    HypothesisStatus,
    HypothesisTransition,
    HypothesisTransitionType,
)
from investigator.state.case_state import CaseState


def apply_hypothesis_updates(
    state: CaseState, updates: list[HypothesisTransition]
) -> CaseState:
    """Return a status-updated copy while preserving the input state and tree."""
    updated = state.model_copy(deep=True)
    status_by_transition = {
        HypothesisTransitionType.WEAKEN: HypothesisStatus.WEAKENED,
        HypothesisTransitionType.CONFLICT: HypothesisStatus.CONFLICTED,
        HypothesisTransitionType.REMOVE: HypothesisStatus.REMOVED,
        HypothesisTransitionType.ACTIVATE: HypothesisStatus.ACTIVE,
    }
    for update in updates:
        hypothesis = updated.get_hypothesis(update.hypothesis_id)
        if update.transition in status_by_transition:
            hypothesis.status = status_by_transition[update.transition]
    return updated

