from enum import Enum

from pydantic import BaseModel, ConfigDict, model_validator

from investigator.graph import EdgeRelation, GraphEdge, GraphNode, GraphNodeType


class InvestigatorOperation(str, Enum):
    ADD_PROPOSITION = "add_proposition"
    ADD_HYPOTHESIS = "add_hypothesis"
    ADD_UNCERTAINTY = "add_uncertainty"
    ADD_SUPPORT = "add_support"
    ADD_CONFLICT = "add_conflict"
    ADD_DERIVATION = "add_derivation"
    ADD_SPECIALIZATION = "add_specialization"
    MOVE_FOCUS = "move_focus"


class InvestigatorUpdate(BaseModel):
    """Strict local-explorer command surface; evidence and global edges are absent."""

    model_config = ConfigDict(extra="forbid")
    operation: InvestigatorOperation
    node: GraphNode | None = None
    edge: GraphEdge | None = None
    focus_node_id: str | None = None
    reason: str = ""

    @model_validator(mode="after")
    def validate_operation(self) -> "InvestigatorUpdate":
        node_ops = {
            InvestigatorOperation.ADD_PROPOSITION: GraphNodeType.PROPOSITION,
            InvestigatorOperation.ADD_HYPOTHESIS: GraphNodeType.HYPOTHESIS,
            InvestigatorOperation.ADD_UNCERTAINTY: GraphNodeType.UNCERTAINTY,
        }
        edge_ops = {
            InvestigatorOperation.ADD_SUPPORT: EdgeRelation.SUPPORTS,
            InvestigatorOperation.ADD_CONFLICT: EdgeRelation.CONFLICTS,
            InvestigatorOperation.ADD_DERIVATION: EdgeRelation.DERIVED_FROM,
            InvestigatorOperation.ADD_SPECIALIZATION: EdgeRelation.SPECIALIZES,
        }
        if self.operation in node_ops:
            if self.node is None or self.node.node_type is not node_ops[self.operation] or self.edge is not None:
                raise ValueError(f"{self.operation.value} requires only a matching non-evidence node")
        elif self.operation in edge_ops:
            if self.edge is None or self.edge.relation is not edge_ops[self.operation] or self.node is not None:
                raise ValueError(f"{self.operation.value} requires only a matching edge relation")
        elif self.operation is InvestigatorOperation.MOVE_FOCUS:
            if self.focus_node_id is None or self.node is not None or self.edge is not None:
                raise ValueError("move_focus requires only focus_node_id")
        return self
