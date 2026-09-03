from investigator.graph.case_graph import CaseGraph
from investigator.graph.models import (
    EdgeRelation,
    EdgeStatus,
    EdgeStrength,
    EpistemicStatus,
    GraphEdge,
    GraphNode,
    GraphNodeType,
    GraphStatus,
    make_edge_id,
)
from investigator.graph.operations import propagate_epistemic_status
from investigator.graph.specs import OPERATION_CONTRACTS, OPERATION_SPECS, OperationContract, OperationSpec, OperationSpecRegistry, ReferenceSpec
from investigator.graph.scope import GraphScope, GraphScopeType, node_scope, scope_allows_subject, scope_metadata, scopes_compatible

__all__ = [
    "CaseGraph", "EdgeRelation", "EdgeStatus", "EdgeStrength", "EpistemicStatus",
    "GraphEdge", "GraphNode", "GraphNodeType", "GraphStatus", "make_edge_id", "propagate_epistemic_status", "OPERATION_SPECS", "OperationSpec", "OperationSpecRegistry", "OPERATION_CONTRACTS", "OperationContract", "ReferenceSpec", "GraphScope", "GraphScopeType", "node_scope", "scope_allows_subject", "scope_metadata", "scopes_compatible",
]
