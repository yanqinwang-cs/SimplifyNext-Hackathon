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
        for evidence_id in (
            update.add_supporting_evidence_ids
            + update.add_conflicting_evidence_ids
            + update.add_specificity_basis
        ):
            if evidence_id in updated.hypotheses:
                raise ValueError(f"Hypothesis ID {evidence_id!r} cannot be used as evidence")
            updated.get_evidence(evidence_id)
        for evidence_id in update.add_supporting_evidence_ids:
            if evidence_id not in hypothesis.supporting_evidence_ids:
                hypothesis.supporting_evidence_ids.append(evidence_id)
        for evidence_id in update.add_conflicting_evidence_ids:
            if evidence_id not in hypothesis.conflicting_evidence_ids:
                hypothesis.conflicting_evidence_ids.append(evidence_id)
        for evidence_id in update.add_specificity_basis:
            if evidence_id not in hypothesis.specificity_basis:
                hypothesis.specificity_basis.append(evidence_id)
        if update.transition in status_by_transition:
            hypothesis.status = status_by_transition[update.transition]
    return updated
