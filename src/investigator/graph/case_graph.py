from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from investigator.graph.models import EdgeRelation, EdgeStatus, GraphEdge, GraphNode, GraphNodeType, GraphStatus, make_edge_id
from investigator.graph.specs import OperationSpecRegistry


class CaseGraph(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str
    nodes: dict[str, GraphNode] = Field(default_factory=dict)
    edges: dict[str, GraphEdge] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_graph(self) -> "CaseGraph":
        if any(key != node.id for key, node in self.nodes.items()):
            raise ValueError("Graph node dictionary keys must match node IDs")
        if any(key != edge.id for key, edge in self.edges.items()):
            raise ValueError("Graph edge dictionary keys must match edge IDs")
        for edge in self.edges.values():
            if edge.source_id not in self.nodes or edge.target_id not in self.nodes:
                raise ValueError(f"Dangling graph edge endpoint: {edge.id!r}")
            if edge.source_id == edge.target_id:
                if edge.relation is EdgeRelation.DERIVED_FROM:
                    raise ValueError(f"DERIVED_FROM self-reference is not allowed: {edge.id!r}")
                raise ValueError(f"Graph self-edge is not allowed: {edge.id!r}")
            source = self.nodes[edge.source_id].node_type
            target = self.nodes[edge.target_id].node_type
            if not OperationSpecRegistry.allows(edge.relation, source, target):
                raise ValueError(f"Invalid endpoint types for {edge.relation.value}: {source.value} -> {target.value}")
        active_parent_by_child: dict[str, str] = {}
        for edge in self.edges.values():
            if edge.relation is EdgeRelation.SPECIALIZES and edge.status is EdgeStatus.ACTIVE:
                if edge.source_id in active_parent_by_child and active_parent_by_child[edge.source_id] != edge.target_id:
                    raise ValueError(f"Hypothesis {edge.source_id!r} has more than one active specialization parent")
                active_parent_by_child[edge.source_id] = edge.target_id
        self._ensure_no_specialization_cycle()
        return self

    def get_node(self, node_id: str) -> GraphNode:
        try:
            return self.nodes[node_id]
        except KeyError as exc:
            raise KeyError(f"Unknown graph node ID: {node_id!r}") from exc

    def get_edge(self, edge_id: str) -> GraphEdge:
        try:
            return self.edges[edge_id]
        except KeyError as exc:
            raise KeyError(f"Unknown graph edge ID: {edge_id!r}") from exc

    def add_node(self, node: GraphNode) -> "CaseGraph":
        if node.id in self.nodes:
            raise ValueError(f"Duplicate graph node ID: {node.id!r}")
        self.nodes[node.id] = node
        return self

    def add_edge(self, edge: GraphEdge) -> "CaseGraph":
        if edge.id in self.edges:
            raise ValueError(f"Duplicate graph edge ID: {edge.id!r}")
        candidate = self.model_copy(deep=True)
        candidate.edges[edge.id] = edge
        candidate.validate_graph()
        self.edges[edge.id] = edge
        return self

    def neighbors(self, node_id: str, active_only: bool = True) -> list[GraphNode]:
        self.get_node(node_id)
        edges = [edge for edge in self.edges.values() if not active_only or edge.status is EdgeStatus.ACTIVE]
        ids = {edge.source_id for edge in edges if edge.target_id == node_id}
        ids.update(edge.target_id for edge in edges if edge.source_id == node_id)
        return [self.nodes[identifier] for identifier in sorted(ids) if not active_only or self.nodes[identifier].status is GraphStatus.ACTIVE]

    def incoming(self, node_id: str, relation: EdgeRelation | None = None, active_only: bool = True) -> list[GraphEdge]:
        self.get_node(node_id)
        return [edge for edge in sorted(self.edges.values(), key=lambda item: item.id) if edge.target_id == node_id and (relation is None or edge.relation is relation) and (not active_only or edge.status is EdgeStatus.ACTIVE)]

    def outgoing(self, node_id: str, relation: EdgeRelation | None = None, active_only: bool = True) -> list[GraphEdge]:
        self.get_node(node_id)
        return [edge for edge in sorted(self.edges.values(), key=lambda item: item.id) if edge.source_id == node_id and (relation is None or edge.relation is relation) and (not active_only or edge.status is EdgeStatus.ACTIVE)]

    def ancestors(self, node_id: str, relation: EdgeRelation = EdgeRelation.SPECIALIZES) -> list[GraphNode]:
        self.get_node(node_id)
        result, current = [], node_id
        while True:
            edges = self.outgoing(current, relation)
            if relation is EdgeRelation.SPECIALIZES and len(edges) > 1:
                raise ValueError(f"Hypothesis {current!r} has more than one active specialization parent")
            if not edges:
                return result
            current = edges[0].target_id
            result.append(self.nodes[current])

    def descendants(self, node_id: str, relation: EdgeRelation = EdgeRelation.SPECIALIZES) -> list[GraphNode]:
        self.get_node(node_id)
        result, pending = [], [node_id]
        while pending:
            current = pending.pop(0)
            children = [edge.source_id for edge in self.incoming(current, relation)]
            for child in children:
                result.append(self.nodes[child])
                pending.append(child)
        return result

    def neighborhood(self, node_id: str, depth: int = 1, relations: set[EdgeRelation] | None = None, node_types: set[GraphNodeType] | None = None, active_only: bool = True) -> "CaseGraph":
        if depth < 0:
            raise ValueError("Neighborhood depth cannot be negative")
        self.get_node(node_id)
        included = {node_id}
        frontier = {node_id}
        for _ in range(depth):
            next_frontier = set()
            for edge in self.edges.values():
                if relations and edge.relation not in relations or active_only and edge.status is EdgeStatus.ARCHIVED:
                    continue
                if edge.source_id in frontier:
                    next_frontier.add(edge.target_id)
                if edge.target_id in frontier:
                    next_frontier.add(edge.source_id)
            included.update(next_frontier)
            frontier = next_frontier
        nodes = {identifier: node for identifier, node in self.nodes.items() if identifier in included and (not active_only or node.status is GraphStatus.ACTIVE) and (node_types is None or node.node_type in node_types)}
        edges = {identifier: edge for identifier, edge in self.edges.items() if edge.source_id in nodes and edge.target_id in nodes and (not active_only or edge.status is EdgeStatus.ACTIVE) and (relations is None or edge.relation in relations)}
        return CaseGraph(case_id=self.case_id, nodes=nodes, edges=edges)

    def active_subgraph(self) -> "CaseGraph":
        nodes = {identifier: node for identifier, node in self.nodes.items() if node.status is GraphStatus.ACTIVE}
        edges = {identifier: edge for identifier, edge in self.edges.items() if edge.status is EdgeStatus.ACTIVE and edge.source_id in nodes and edge.target_id in nodes}
        return CaseGraph(case_id=self.case_id, nodes=nodes, edges=edges)

    def specialize(self, parent_id: str, child_node: GraphNode, basis_evidence_ids: Iterable[str] = ()) -> "CaseGraph":
        parent = self.get_node(parent_id)
        if parent.node_type is not GraphNodeType.HYPOTHESIS or child_node.node_type is not GraphNodeType.HYPOTHESIS:
            raise ValueError("SPECIALIZES requires hypothesis parent and child")
        added = child_node.id not in self.nodes
        if added:
            self.add_node(child_node)
        elif self.get_node(child_node.id).node_type is not GraphNodeType.HYPOTHESIS:
            raise ValueError("SPECIALIZES child must be a hypothesis")
        edge_id = make_edge_id(child_node.id, EdgeRelation.SPECIALIZES, parent_id)
        metadata = {"basis_evidence_ids": list(basis_evidence_ids)}
        try:
            self.add_edge(GraphEdge(id=edge_id, source_id=child_node.id, target_id=parent_id, relation=EdgeRelation.SPECIALIZES, metadata=metadata))
        except Exception:
            if added:
                self.nodes.pop(child_node.id, None)
            raise
        return self

    def generalize(self, node_id: str, archive_child: bool = False) -> GraphNode:
        ancestors = self.ancestors(node_id)
        if not ancestors:
            raise ValueError(f"No existing ancestor to generalize from {node_id!r}")
        if archive_child:
            self.archive_node(node_id, "Generalized focus to existing ancestor")
        return ancestors[0]

    def archive_node(self, node_id: str, reason: str | None = None) -> "CaseGraph":
        node = self.get_node(node_id)
        node.status = GraphStatus.ARCHIVED
        if reason is not None:
            node.metadata["archive_reason"] = reason
        return self

    def reactivate_node(self, node_id: str, reason: str | None = None) -> "CaseGraph":
        node = self.get_node(node_id)
        node.status = GraphStatus.ACTIVE
        if reason is not None:
            node.metadata["reactivation_reason"] = reason
        return self

    def archive_edge(self, edge_id: str) -> "CaseGraph":
        self.get_edge(edge_id).status = EdgeStatus.ARCHIVED
        return self

    def reactivate_edge(self, edge_id: str) -> "CaseGraph":
        self.get_edge(edge_id).status = EdgeStatus.ACTIVE
        return self

    @classmethod
    def from_case_state(cls, case_state: Any) -> "CaseGraph":
        from investigator.models import HypothesisStatus

        nodes: dict[str, GraphNode] = {}
        for evidence_id, evidence in sorted(case_state.evidence.items()):
            nodes[evidence_id] = GraphNode(id=evidence_id, node_type=GraphNodeType.EVIDENCE, statement=evidence.raw_content, metadata={"source_id": evidence.source_id, "kind": evidence.kind.value, **evidence.metadata})
        for hypothesis_id, hypothesis in sorted(case_state.hypotheses.items()):
            lifecycle = GraphStatus.ARCHIVED if hypothesis.status in {HypothesisStatus.REMOVED, HypothesisStatus.REJECTED, HypothesisStatus.ARCHIVED} else GraphStatus.ACTIVE
            nodes[hypothesis_id] = GraphNode(id=hypothesis_id, node_type=GraphNodeType.HYPOTHESIS, statement=hypothesis.statement, status=lifecycle, metadata={"origin": hypothesis.origin.value, "hypothesis_status": hypothesis.status.value})
        for uncertainty_id, uncertainty in sorted(case_state.uncertainties.items()):
            nodes[uncertainty_id] = GraphNode(id=uncertainty_id, node_type=GraphNodeType.UNCERTAINTY, statement=uncertainty.description, metadata={"kind": uncertainty.kind.value, "evidence_ids": list(uncertainty.evidence_ids)})
        edges: dict[str, GraphEdge] = {}
        for hypothesis_id, hypothesis in sorted(case_state.hypotheses.items()):
            if hypothesis.parent_hypothesis_id:
                edge = GraphEdge(id=make_edge_id(hypothesis_id, EdgeRelation.SPECIALIZES, hypothesis.parent_hypothesis_id), source_id=hypothesis_id, target_id=hypothesis.parent_hypothesis_id, relation=EdgeRelation.SPECIALIZES)
                edges[edge.id] = edge
            for evidence_id in hypothesis.supporting_evidence_ids:
                edge = GraphEdge(id=make_edge_id(evidence_id, EdgeRelation.SUPPORTS, hypothesis_id), source_id=evidence_id, target_id=hypothesis_id, relation=EdgeRelation.SUPPORTS)
                edges[edge.id] = edge
            for evidence_id in hypothesis.conflicting_evidence_ids:
                edge = GraphEdge(id=make_edge_id(evidence_id, EdgeRelation.CONFLICTS, hypothesis_id), source_id=evidence_id, target_id=hypothesis_id, relation=EdgeRelation.CONFLICTS)
                edges[edge.id] = edge
            for uncertainty_id in hypothesis.unresolved_issue_ids:
                edge = GraphEdge(id=make_edge_id(uncertainty_id, EdgeRelation.TARGETS, hypothesis_id), source_id=uncertainty_id, target_id=hypothesis_id, relation=EdgeRelation.TARGETS)
                edges[edge.id] = edge
        return cls(case_id=case_state.case_id, nodes=nodes, edges=edges)

    def _ensure_no_specialization_cycle(self) -> None:
        parent_by_child = {edge.source_id: edge.target_id for edge in self.edges.values() if edge.relation is EdgeRelation.SPECIALIZES and edge.status is EdgeStatus.ACTIVE}
        for node_id in parent_by_child:
            seen: set[str] = set()
            current = node_id
            while current in parent_by_child:
                if current in seen:
                    raise ValueError("SPECIALIZES cycle detected")
                seen.add(current)
                current = parent_by_child[current]
