from collections.abc import Callable
from typing import TypeVar
from investigator.models.claim import Claim
from investigator.models.conflict import Conflict
from investigator.models.evidence import EvidenceItem
from investigator.models.entity import Entity
from investigator.models.hypothesis import Hypothesis, HypothesisStatus, HypothesisTransformation
from investigator.models.source import Source
from investigator.models.uncertainty import Uncertainty
from investigator.state.case_state import CaseState
from investigator.state.repository import CaseRepository

T = TypeVar("T")


class CaseService:
    def __init__(self, repository: CaseRepository) -> None:
        self.repository = repository

    def _mutate(self, case_id: str, action: Callable[[CaseState], T]) -> T:
        state = self.repository.load(case_id)
        result = action(state)
        state.revision += 1
        self.repository.save(state)
        return result

    @staticmethod
    def _add(collection: dict[str, T], item: T, item_id: str) -> T:
        if item_id in collection:
            raise ValueError(f"Duplicate ID: {item_id!r}")
        collection[item_id] = item
        return item

    @staticmethod
    def _require(collection: dict[str, T], item_id: str, label: str) -> T:
        if item_id not in collection:
            raise KeyError(f"Unknown {label} ID: {item_id!r}")
        return collection[item_id]

    def create_case(self, case_id: str, title: str, description: str | None = None) -> CaseState:
        if self.repository.exists(case_id):
            raise ValueError(f"Duplicate case ID: {case_id!r}")
        state = CaseState(case_id=case_id, title=title, description=description)
        self.repository.save(state)
        return state

    def add_source(self, case_id: str, source: Source) -> Source:
        return self._mutate(case_id, lambda s: self._add(s.sources, source, source.id))

    def add_evidence(self, case_id: str, evidence: EvidenceItem) -> EvidenceItem:
        def add(state: CaseState) -> EvidenceItem:
            self._require(state.sources, evidence.source_id, "source")
            return self._add(state.evidence, evidence, evidence.id)
        return self._mutate(case_id, add)

    def add_entity(self, case_id: str, entity: Entity) -> Entity:
        return self._mutate(case_id, lambda s: self._add(s.entities, entity, entity.id))

    def add_claim(self, case_id: str, claim: Claim) -> Claim:
        def add(state: CaseState) -> Claim:
            if claim.asserted_by_source_id:
                self._require(state.sources, claim.asserted_by_source_id, "source")
            for evidence_id in claim.evidence_ids:
                self._require(state.evidence, evidence_id, "evidence")
            return self._add(state.claims, claim, claim.id)
        return self._mutate(case_id, add)

    def add_hypothesis(self, case_id: str, hypothesis: Hypothesis) -> Hypothesis:
        def add(state: CaseState) -> Hypothesis:
            if hypothesis.parent_hypothesis_id:
                self._require(state.hypotheses, hypothesis.parent_hypothesis_id, "hypothesis")
            for evidence_id in (
                hypothesis.supporting_evidence_ids
                + hypothesis.conflicting_evidence_ids
                + hypothesis.specificity_basis
            ):
                self._require(state.evidence, evidence_id, "evidence")
            for issue_id in hypothesis.unresolved_issue_ids:
                self._require(state.uncertainties, issue_id, "uncertainty")
            return self._add(state.hypotheses, hypothesis, hypothesis.id)
        return self._mutate(case_id, add)

    def add_uncertainty(self, case_id: str, uncertainty: Uncertainty) -> Uncertainty:
        def add(state: CaseState) -> Uncertainty:
            for evidence_id in uncertainty.evidence_ids:
                self._require(state.evidence, evidence_id, "evidence")
            return self._add(state.uncertainties, uncertainty, uncertainty.id)
        return self._mutate(case_id, add)

    def add_conflict(self, case_id: str, conflict: Conflict) -> Conflict:
        def add(state: CaseState) -> Conflict:
            for claim_id in conflict.claim_ids:
                self._require(state.claims, claim_id, "claim")
            for evidence_id in conflict.evidence_ids:
                self._require(state.evidence, evidence_id, "evidence")
            return self._add(state.conflicts, conflict, conflict.id)
        return self._mutate(case_id, add)

    def link_supporting_evidence(self, case_id: str, hypothesis_id: str, evidence_id: str) -> None:
        def link(state: CaseState) -> None:
            hypothesis = self._require(state.hypotheses, hypothesis_id, "hypothesis")
            self._require(state.evidence, evidence_id, "evidence")
            if evidence_id not in hypothesis.supporting_evidence_ids:
                hypothesis.supporting_evidence_ids.append(evidence_id)
        self._mutate(case_id, link)

    def link_conflicting_evidence(self, case_id: str, hypothesis_id: str, evidence_id: str) -> None:
        def link(state: CaseState) -> None:
            hypothesis = self._require(state.hypotheses, hypothesis_id, "hypothesis")
            self._require(state.evidence, evidence_id, "evidence")
            if evidence_id not in hypothesis.conflicting_evidence_ids:
                hypothesis.conflicting_evidence_ids.append(evidence_id)
        self._mutate(case_id, link)

    def change_hypothesis_status(self, case_id: str, hypothesis_id: str, status: HypothesisStatus) -> None:
        def change(state: CaseState) -> None:
            self._require(state.hypotheses, hypothesis_id, "hypothesis").status = HypothesisStatus(status)
        self._mutate(case_id, change)

    def add_hypothesis_transformation(self, case_id: str, transformation: HypothesisTransformation) -> HypothesisTransformation:
        def add(state: CaseState) -> HypothesisTransformation:
            self._require(state.hypotheses, transformation.parent_hypothesis_id, "hypothesis")
            self._require(state.hypotheses, transformation.child_hypothesis_id, "hypothesis")
            if any(
                existing.parent_hypothesis_id == transformation.parent_hypothesis_id
                and existing.child_hypothesis_id == transformation.child_hypothesis_id
                for existing in state.transformations
            ):
                raise ValueError("Duplicate hypothesis transformation")
            state.transformations.append(transformation)
            return transformation
        return self._mutate(case_id, add)
