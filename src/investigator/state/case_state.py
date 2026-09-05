from datetime import datetime, timezone
from typing import Any
from pydantic import BaseModel, Field, model_validator
from investigator.models.claim import Claim
from investigator.models.conflict import Conflict
from investigator.models.evidence import EvidenceItem
from investigator.models.entity import Entity
from investigator.models.hypothesis import Hypothesis, HypothesisTransformation
from investigator.models.source import Source
from investigator.models.uncertainty import Uncertainty
from investigator.models.evidence_request import EvidenceRequest
from investigator.graph import CaseGraph
from investigator.models.assessment import AssessmentContext, AssessmentSubject, SubjectRelationship, validate_identity_references


class CaseState(BaseModel):
    case_id: str
    title: str
    description: str | None = None
    case_kind: str = "user"
    sample_id: str | None = None
    assessment_rule_preset_id: str = "academic-integrity-core"
    sources: dict[str, Source] = Field(default_factory=dict)
    assessment_context: AssessmentContext | None = None
    subjects: dict[str, AssessmentSubject] = Field(default_factory=dict)
    subject_relationships: dict[str, SubjectRelationship] = Field(default_factory=dict)
    evidence: dict[str, EvidenceItem] = Field(default_factory=dict)
    entities: dict[str, Entity] = Field(default_factory=dict)
    claims: dict[str, Claim] = Field(default_factory=dict)
    hypotheses: dict[str, Hypothesis] = Field(default_factory=dict)
    transformations: list[HypothesisTransformation] = Field(default_factory=list)
    uncertainties: dict[str, Uncertainty] = Field(default_factory=dict)
    conflicts: dict[str, Conflict] = Field(default_factory=dict)
    evidence_correction_history: list[dict[str, str | None]] = Field(default_factory=list)
    evidence_request_history: list[EvidenceRequest] = Field(default_factory=list)
    case_status: str = "ACTIVE"
    runtime_status: str = "IDLE"
    current_actor: str = "NONE"
    last_error: dict[str, Any] | None = None
    last_trace_step: int | None = None
    last_updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    trace_history: list[dict[str, Any]] = Field(default_factory=list)
    reasoning_graph: CaseGraph | None = None
    focus_node_id: str | None = None
    focus_recent_node_ids: list[str] = Field(default_factory=list)
    focus_recent_region_node_ids: list[str] = Field(default_factory=list)
    clean_checkpoint: dict[str, Any] | None = None
    revision: int = 0
    administrative_revision: int = 0
    administrative_activity: list[dict[str, str]] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_structure(self) -> "CaseState":
        validate_identity_references(self.subjects, self.subject_relationships, self.sources)
        if any(key != item.id for key, item in self.evidence.items()):
            raise ValueError("Evidence dictionary keys must match evidence IDs")
        if any(key != item.id for key, item in self.hypotheses.items()):
            raise ValueError("Hypothesis dictionary keys must match hypothesis IDs")

        hypothesis_ids = set(self.hypotheses)
        evidence_ids = set(self.evidence)
        uncertainty_ids = set(self.uncertainties)
        if any(key != item.id for key, item in self.uncertainties.items()):
            raise ValueError("Uncertainty dictionary keys must match uncertainty IDs")
        for evidence_item in self.evidence.values():
            if evidence_item.source_id not in self.sources:
                raise ValueError(f"Unknown source ID: {evidence_item.source_id!r}")
        for hypothesis in self.hypotheses.values():
            parent_id = hypothesis.parent_hypothesis_id
            if parent_id == hypothesis.id:
                raise ValueError(f"Hypothesis {hypothesis.id!r} cannot be its own parent")
            if parent_id is not None and parent_id not in hypothesis_ids:
                raise ValueError(f"Unknown parent hypothesis ID: {parent_id!r}")
            for field_name in ("supporting_evidence_ids", "conflicting_evidence_ids", "specificity_basis"):
                for evidence_id in getattr(hypothesis, field_name):
                    if evidence_id in hypothesis_ids:
                        raise ValueError(f"Hypothesis ID {evidence_id!r} cannot be used as evidence")
                    if evidence_id not in evidence_ids:
                        raise ValueError(f"Unknown evidence ID: {evidence_id!r}")
            for uncertainty_id in hypothesis.unresolved_issue_ids:
                if uncertainty_id in hypothesis_ids or uncertainty_id not in uncertainty_ids:
                    if uncertainty_id in hypothesis_ids:
                        raise ValueError(f"Hypothesis ID {uncertainty_id!r} cannot be used as uncertainty")
                    raise ValueError(f"Unknown uncertainty ID: {uncertainty_id!r}")

        for hypothesis in self.hypotheses.values():
            seen: set[str] = set()
            current = hypothesis
            while current.parent_hypothesis_id is not None:
                if current.id in seen:
                    raise ValueError("Hypothesis parent cycle detected")
                seen.add(current.id)
                current = self.hypotheses[current.parent_hypothesis_id]
        return self

    def get_evidence(self, evidence_id: str) -> EvidenceItem:
        try:
            return self.evidence[evidence_id]
        except KeyError as exc:
            raise KeyError(f"Unknown evidence ID: {evidence_id!r}") from exc

    def get_hypothesis(self, hypothesis_id: str) -> Hypothesis:
        try:
            return self.hypotheses[hypothesis_id]
        except KeyError as exc:
            raise KeyError(f"Unknown hypothesis ID: {hypothesis_id!r}") from exc

    def children_of(self, hypothesis_id: str) -> list[Hypothesis]:
        self.get_hypothesis(hypothesis_id)
        return [h for h in self.hypotheses.values() if h.parent_hypothesis_id == hypothesis_id]

    def root_hypotheses(self) -> list[Hypothesis]:
        return [h for h in self.hypotheses.values() if h.parent_hypothesis_id is None]
