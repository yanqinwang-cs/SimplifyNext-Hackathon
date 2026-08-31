from enum import Enum
from pydantic import BaseModel, Field


class HypothesisOrigin(str, Enum):
    HUMAN = "human"
    AGENT_VARIANT = "agent_variant"
    AGENT_SUGGESTION = "agent_suggestion"


class HypothesisStatus(str, Enum):
    PROPOSED = "proposed"
    ACTIVE = "active"
    DEPRIORITIZED = "deprioritized"
    REJECTED = "rejected"
    ARCHIVED = "archived"


class TransformationType(str, Enum):
    NEGATION = "negation"
    NARROWING = "narrowing"
    BROADENING = "broadening"
    SUBJECT_SUBSTITUTION = "subject_substitution"
    OBJECT_SUBSTITUTION = "object_substitution"
    DECOMPOSITION = "decomposition"


class Hypothesis(BaseModel):
    id: str
    statement: str
    origin: HypothesisOrigin
    status: HypothesisStatus = HypothesisStatus.PROPOSED
    parent_hypothesis_id: str | None = None
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    conflicting_evidence_ids: list[str] = Field(default_factory=list)
    unresolved_issue_ids: list[str] = Field(default_factory=list)


class HypothesisTransformation(BaseModel):
    parent_hypothesis_id: str
    child_hypothesis_id: str
    transformation_type: TransformationType

