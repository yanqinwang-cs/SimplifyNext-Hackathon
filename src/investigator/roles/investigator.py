from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator

from investigator.graph import GraphEdge, GraphNode


class InvestigatorOperation(str, Enum):
    ADD_NODE = "add_node"
    ADD_EDGE = "add_edge"
    MOVE_FOCUS = "move_focus"


class InvestigatorUpdate(BaseModel):
    """Local explorer authority; global maintenance is intentionally absent."""

    model_config = ConfigDict(extra="forbid")
    operation: InvestigatorOperation
    node: GraphNode | None = None
    edge: GraphEdge | None = None
    focus_node_id: str | None = None
    reason: str = ""

    @model_validator(mode="after")
    def validate_operation(self) -> "InvestigatorUpdate":
        if self.operation is InvestigatorOperation.ADD_NODE and self.node is None:
            raise ValueError("ADD_NODE requires a node")
        if self.operation is InvestigatorOperation.ADD_EDGE and self.edge is None:
            raise ValueError("ADD_EDGE requires an edge")
        if self.operation is InvestigatorOperation.MOVE_FOCUS and self.focus_node_id is None:
            raise ValueError("MOVE_FOCUS requires focus_node_id")
        return self
