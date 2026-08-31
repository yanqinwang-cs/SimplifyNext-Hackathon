from pydantic import BaseModel, Field
from investigator.models.claim import Claim
from investigator.models.conflict import Conflict
from investigator.models.evidence import EvidenceItem
from investigator.models.entity import Entity
from investigator.models.hypothesis import Hypothesis, HypothesisTransformation
from investigator.models.source import Source
from investigator.models.uncertainty import Uncertainty


class CaseState(BaseModel):
    case_id: str
    title: str
    description: str | None = None
    sources: dict[str, Source] = Field(default_factory=dict)
    evidence: dict[str, EvidenceItem] = Field(default_factory=dict)
    entities: dict[str, Entity] = Field(default_factory=dict)
    claims: dict[str, Claim] = Field(default_factory=dict)
    hypotheses: dict[str, Hypothesis] = Field(default_factory=dict)
    transformations: list[HypothesisTransformation] = Field(default_factory=list)
    uncertainties: dict[str, Uncertainty] = Field(default_factory=dict)
    conflicts: dict[str, Conflict] = Field(default_factory=dict)
    revision: int = 0
