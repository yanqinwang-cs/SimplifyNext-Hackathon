from pydantic import BaseModel, ConfigDict, Field

from investigator.graph import CaseGraph, EdgeRelation, EdgeStatus, GraphNodeType, GraphStatus


class InvestigationFocus(BaseModel):
    model_config = ConfigDict(extra="forbid")
    node_id: str
    recent_node_ids: list[str] = Field(default_factory=list)
    recent_region_node_ids: list[str] = Field(default_factory=list)
    reason: str = ""
    step_index: int = 0

    def moved_to(self, node_id: str, region_node_ids: list[str], reason: str = "") -> "InvestigationFocus":
        return InvestigationFocus(
            node_id=node_id,
            recent_node_ids=[*self.recent_node_ids, node_id],
            recent_region_node_ids=[*self.recent_region_node_ids, *region_node_ids],
            reason=reason,
            step_index=self.step_index + 1,
        )


def investigator_region(graph: CaseGraph, focus: InvestigationFocus, depth: int = 1) -> CaseGraph:
    return graph.neighborhood(focus.node_id, depth=depth)


MAX_ACTIVE_REASONING_NODES = 30


def active_reasoning_view(graph: CaseGraph, focus: InvestigationFocus, *, tenure_node_ids: set[str] | None = None, max_nodes: int = MAX_ACTIVE_REASONING_NODES) -> CaseGraph:
    """Return a deterministic semantic branch view plus bounded lateral context."""
    if focus.node_id not in graph.nodes:
        raise ValueError(f"Unknown graph node ID: {focus.node_id!r}")
    active = {node_id for node_id, node in graph.nodes.items() if node.status is GraphStatus.ACTIVE}
    branch = [focus.node_id]
    seen = {focus.node_id}

    def neighbors_for(identifier: str) -> list[str]:
        found: set[str] = set()
        for edge in graph.edges.values():
            if edge.status is not EdgeStatus.ACTIVE:
                continue
            if edge.target_id == identifier and edge.relation in {EdgeRelation.SUPPORTS, EdgeRelation.DERIVED_FROM, EdgeRelation.DEPENDS_ON, EdgeRelation.TARGETS, EdgeRelation.SPECIALIZES}:
                found.add(edge.source_id)
            if edge.source_id == identifier and edge.relation in {EdgeRelation.TARGETS, EdgeRelation.DEPENDS_ON, EdgeRelation.SPECIALIZES}:
                found.add(edge.target_id)
        return sorted(found)

    for identifier in branch:
        for neighbor in neighbors_for(identifier):
            if neighbor in active and neighbor not in seen:
                seen.add(neighbor)
                branch.append(neighbor)

    alternatives: set[str] = set()
    branch_set = set(branch)
    for identifier in branch:
        for edge in graph.edges.values():
            if edge.status is not EdgeStatus.ACTIVE:
                continue
            if edge.source_id == identifier or edge.target_id == identifier:
                other = edge.target_id if edge.source_id == identifier else edge.source_id
                if other in active and other not in branch_set and graph.nodes[other].node_type in {GraphNodeType.EVIDENCE, GraphNodeType.HYPOTHESIS, GraphNodeType.UNCERTAINTY}:
                    alternatives.add(other)
    tenure = set(tenure_node_ids or set())
    for identifier in tuple(tenure):
        for edge in graph.edges.values():
            if edge.status is EdgeStatus.ACTIVE and (edge.source_id == identifier or edge.target_id == identifier):
                tenure.add(edge.target_id if edge.source_id == identifier else edge.source_id)
    ordered = branch + sorted(alternatives) + sorted(tenure - branch_set - alternatives)
    selected = ordered[:max_nodes]
    nodes = {identifier: graph.nodes[identifier].model_copy(deep=True) for identifier in selected if identifier in active}
    edges = {edge_id: edge.model_copy(deep=True) for edge_id, edge in graph.edges.items() if edge.status is EdgeStatus.ACTIVE and edge.source_id in nodes and edge.target_id in nodes}
    return CaseGraph(case_id=graph.case_id, nodes=nodes, edges=edges)
