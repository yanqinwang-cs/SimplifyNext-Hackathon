from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class GraphNodeType(str, Enum):
    EVIDENCE = "evidence"
    PROPOSITION = "proposition"
    HYPOTHESIS = "hypothesis"
    UNCERTAINTY = "uncertainty"


class GraphStatus(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class EpistemicStatus(str, Enum):
    UNRESOLVED = "unresolved"
    ESTABLISHED = "established"
    REJECTED = "rejected"


class EdgeRelation(str, Enum):
    SUPPORTS = "supports"
    CONFLICTS = "conflicts"
    SPECIALIZES = "specializes"
    DEPENDS_ON = "depends_on"
    TARGETS = "targets"
    DERIVED_FROM = "derived_from"


class EdgeStatus(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class EdgeStrength(str, Enum):
    DIRECT = "direct"
    CORROBORATIVE = "corroborative"
    INDIRECT = "indirect"
    WEAK = "weak"


class GraphNode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1)
    node_type: GraphNodeType
    statement: str
    status: GraphStatus = GraphStatus.ACTIVE
    metadata: dict[str, Any] = Field(default_factory=dict)


class GraphEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1)
    source_id: str
    target_id: str
    relation: EdgeRelation
    status: EdgeStatus = EdgeStatus.ACTIVE
    strength: EdgeStrength | None = None
    explanation: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
