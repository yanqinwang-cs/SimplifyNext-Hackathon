from enum import Enum
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


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

    @model_validator(mode="after")
    def validate_id(self) -> "GraphNode":
        if not _valid_id(self.id) or not _valid_node_id(self.id, self.node_type):
            raise ValueError(f"Invalid {self.node_type.value} graph node ID: {self.id!r}")
        return self


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

    @model_validator(mode="after")
    def validate_id(self) -> "GraphEdge":
        if self.id != make_edge_id(self.source_id, self.relation, self.target_id):
            raise ValueError("Graph edge ID must be the canonical source_relation_target form")
        return self


def make_edge_id(source_id: str, relation: EdgeRelation, target_id: str) -> str:
    """Return the stable, readable ID for one semantic edge."""
    return f"{source_id}_{relation.value.upper()}_{target_id}"


def _valid_id(value: str) -> bool:
    return bool(value == value.strip() and "\n" not in value and "\t" not in value and re.fullmatch(r"[A-Z][A-Za-z0-9_.:]*", value))


def _valid_node_id(node_id: str, node_type: GraphNodeType) -> bool:
    patterns = {
        GraphNodeType.EVIDENCE: r"(?:E\d+(?:\.\d+)*|A\d+_RELEASE)",
        GraphNodeType.PROPOSITION: r"P\d+(?:\.\d+)*",
        GraphNodeType.HYPOTHESIS: r"H\d+(?:\.\d+)*",
        GraphNodeType.UNCERTAINTY: r"U\d+(?:\.\d+)*",
    }
    return bool(re.fullmatch(patterns[node_type], node_id))
