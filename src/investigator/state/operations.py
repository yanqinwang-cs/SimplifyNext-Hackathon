from investigator.models.hypothesis import (
    HypothesisStatus,
    HypothesisTransition,
    HypothesisTransitionType,
)
from investigator.state.case_state import CaseState
from investigator.models import EvidenceItem, Hypothesis, HypothesisOrigin, HypothesisStatus, Source, Uncertainty, UncertaintyKind, UncertaintyTransitionType
from typing import Any


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
            + update.add_specificity_basis_evidence_ids
        ):
            if evidence_id in updated.hypotheses:
                raise ValueError(f"Hypothesis ID {evidence_id!r} cannot be used as evidence")
            updated.get_evidence(evidence_id)
        if update.transition is HypothesisTransitionType.OTHER:
            continue
        for evidence_id in update.add_supporting_evidence_ids:
            if evidence_id not in hypothesis.supporting_evidence_ids:
                hypothesis.supporting_evidence_ids.append(evidence_id)
        for evidence_id in update.add_conflicting_evidence_ids:
            if evidence_id not in hypothesis.conflicting_evidence_ids:
                hypothesis.conflicting_evidence_ids.append(evidence_id)
        for evidence_id in update.add_specificity_basis_evidence_ids:
            if evidence_id not in hypothesis.specificity_basis:
                hypothesis.specificity_basis.append(evidence_id)
        if update.transition in status_by_transition:
            hypothesis.status = status_by_transition[update.transition]
    return updated


def build_initial_state(case_id: str, title: str, sources: dict[str, Source], evidence: dict[str, EvidenceItem], response: Any) -> CaseState:
    hypotheses: dict[str, Hypothesis] = {}
    uncertainties: dict[str, Uncertainty] = {}
    for proposal in response.hypotheses:
        if proposal.id in hypotheses:
            raise ValueError(f"Duplicate hypothesis ID in initial response: {proposal.id!r}")
        hypothesis = Hypothesis(
            id=proposal.id, parent_id=proposal.parent_id, statement=proposal.statement,
            origin=HypothesisOrigin.AGENT_SUGGESTION, status=HypothesisStatus(proposal.status),
            supporting_evidence_ids=proposal.supported_by, conflicting_evidence_ids=proposal.conflicted_by,
            unresolved_issue_ids=[f"{proposal.id}:U{i}" for i in range(1, len(proposal.unresolved) + 1)],
            specificity_basis=proposal.specificity_basis_evidence_ids,
        )
        hypotheses[proposal.id] = hypothesis
        for index, description in enumerate(proposal.unresolved, start=1):
            uncertainty_id = f"{proposal.id}:U{index}"
            if uncertainty_id in uncertainties:
                raise ValueError(f"Duplicate uncertainty ID in initial response: {uncertainty_id!r}")
            uncertainties[uncertainty_id] = Uncertainty(id=uncertainty_id, kind=UncertaintyKind.UNKNOWN, description=description)
    return CaseState(case_id=case_id, title=title, sources=sources, evidence=evidence, hypotheses=hypotheses, uncertainties=uncertainties)


def build_seeded_initial_state(case_id: str, title: str, sources: dict[str, Source], evidence: dict[str, EvidenceItem], seed_statement: str, analysis: Any, alternatives: list[Any]) -> CaseState:
    hypotheses: dict[str, Hypothesis] = {}
    uncertainties: dict[str, Uncertainty] = {}
    proposals = [("H1", None, seed_statement, analysis, HypothesisOrigin.HUMAN_INPUT)] + [
        (proposal.id, proposal.parent_id, proposal.statement, proposal, HypothesisOrigin.AGENT_SUGGESTION)
        for proposal in alternatives
    ]
    for identifier, parent_id, statement, proposal, origin in proposals:
        if identifier in hypotheses:
            raise ValueError(f"Duplicate hypothesis ID in initial expansion: {identifier!r}")
        if origin is HypothesisOrigin.AGENT_SUGGESTION:
            if proposal.relationship == "competing_root" and proposal.contrasted_hypothesis_id not in hypotheses and proposal.contrasted_hypothesis_id != "H1":
                raise ValueError(f"Unknown competing hypothesis ID: {proposal.contrasted_hypothesis_id!r}")
            if proposal.relationship == "specialization" and proposal.parent_id not in hypotheses and proposal.parent_id != "H1":
                raise ValueError(f"Unknown parent hypothesis ID: {proposal.parent_id!r}")
        for evidence_id in proposal.supported_by + proposal.conflicted_by + proposal.specificity_basis_evidence_ids:
            if evidence_id not in evidence:
                raise ValueError(f"Unknown evidence ID: {evidence_id!r}")
        hypotheses[identifier] = Hypothesis(
            id=identifier, parent_id=parent_id, statement=statement, origin=origin, status=HypothesisStatus.ACTIVE,
            supporting_evidence_ids=proposal.supported_by, conflicting_evidence_ids=proposal.conflicted_by,
            unresolved_issue_ids=[f"{identifier}:U{i}" for i in range(1, len(proposal.unresolved) + 1)],
            specificity_basis=proposal.specificity_basis_evidence_ids,
        )
        for index, description in enumerate(proposal.unresolved, start=1):
            uncertainty_id = f"{identifier}:U{index}"
            uncertainties[uncertainty_id] = Uncertainty(id=uncertainty_id, kind=UncertaintyKind.UNKNOWN, description=description)
    return CaseState(case_id=case_id, title=title, sources=sources, evidence=evidence, hypotheses=hypotheses, uncertainties=uncertainties)


def apply_revision(state: CaseState, response: Any) -> CaseState:
    updated = apply_hypothesis_updates(state, response.hypothesis_updates)
    for update in response.uncertainty_updates:
        for evidence_id in update.basis_evidence_ids:
            updated.get_evidence(evidence_id)
        if update.uncertainty_id not in updated.uncertainties:
            raise KeyError(f"Unknown uncertainty ID: {update.uncertainty_id!r}")
        uncertainty = updated.uncertainties[update.uncertainty_id]
        for evidence_id in update.basis_evidence_ids:
            if evidence_id not in uncertainty.evidence_ids:
                uncertainty.evidence_ids.append(evidence_id)
        if update.transition is UncertaintyTransitionType.OTHER:
            continue
        if update.transition is UncertaintyTransitionType.REFINE:
            uncertainty.description = update.new_description
        elif update.transition in {UncertaintyTransitionType.RESOLVE, UncertaintyTransitionType.REMOVE}:
            updated.uncertainties.pop(update.uncertainty_id)
            for hypothesis in updated.hypotheses.values():
                hypothesis.unresolved_issue_ids = [i for i in hypothesis.unresolved_issue_ids if i != update.uncertainty_id]
    for new_uncertainty in response.new_uncertainties:
        if new_uncertainty.id in updated.uncertainties:
            raise ValueError(f"Duplicate uncertainty ID in revision: {new_uncertainty.id!r}")
        hypothesis = updated.get_hypothesis(new_uncertainty.hypothesis_id)
        for evidence_id in new_uncertainty.basis_evidence_ids:
            updated.get_evidence(evidence_id)
        updated.uncertainties[new_uncertainty.id] = Uncertainty(id=new_uncertainty.id, kind=UncertaintyKind.UNKNOWN, description=new_uncertainty.description, evidence_ids=list(new_uncertainty.basis_evidence_ids))
        if new_uncertainty.id not in hypothesis.unresolved_issue_ids:
            hypothesis.unresolved_issue_ids.append(new_uncertainty.id)
    for proposal in response.new_hypotheses:
        if proposal.id in updated.hypotheses:
            raise ValueError(f"Duplicate hypothesis ID in revision: {proposal.id!r}")
        for uncertainty in [Uncertainty(id=f"{proposal.id}:U{i}", kind=UncertaintyKind.UNKNOWN, description=d) for i, d in enumerate(proposal.unresolved, start=1)]:
            if uncertainty.id in updated.uncertainties:
                raise ValueError(f"Duplicate uncertainty ID in revision: {uncertainty.id!r}")
            updated.uncertainties[uncertainty.id] = uncertainty
        updated.hypotheses[proposal.id] = Hypothesis(id=proposal.id, parent_id=proposal.parent_id, statement=proposal.statement, origin=HypothesisOrigin.AGENT_SUGGESTION, status=HypothesisStatus(proposal.status), supporting_evidence_ids=proposal.supported_by, conflicting_evidence_ids=proposal.conflicted_by, unresolved_issue_ids=[f"{proposal.id}:U{i}" for i in range(1, len(proposal.unresolved) + 1)], specificity_basis=proposal.specificity_basis_evidence_ids)
    updated.revision += 1
    return CaseState.model_validate(updated.model_dump())
