from collections import Counter

from pydantic import BaseModel, ConfigDict, Field

from investigator.graph import CaseGraph, EdgeRelation, EdgeStrength, GraphNodeType, GraphStatus
from investigator.roles.focus import InvestigationFocus, investigator_region


class EvidenceProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    direct_count: int = 0
    corroborative_count: int = 0
    indirect_count: int = 0
    weak_count: int = 0
    unique_evidence_ids: list[str] = Field(default_factory=list)
    unique_source_ids: list[str] = Field(default_factory=list)


class RegionHealth(BaseModel):
    model_config = ConfigDict(extra="forbid")
    focus_node_id: str
    node_ids: list[str]
    direct_evidence_profile: EvidenceProfile
    conflict_profile: EvidenceProfile
    unresolved_ids: list[str]
    current_specialization_depth: int
    descendant_count: int
    recent_visit_count: int
    recent_new_support_count: int = 0
    recent_new_conflict_count: int = 0
    shared_dependency_count: int
    active: bool


class TunnelVisionIndicators(BaseModel):
    model_config = ConfigDict(extra="forbid")
    same_root_steps: int
    current_specialization_depth: int
    new_nodes_recent: int
    new_support_edges_recent: int
    new_conflict_edges_recent: int
    neglected_candidate_ids: list[str]


def direct_evidence_profile(graph: CaseGraph, node_ids: set[str], relation: EdgeRelation = EdgeRelation.SUPPORTS) -> EvidenceProfile:
    """Count direct evidence edges only; no mediated or recursive support is inferred."""
    edges = [edge for edge in graph.edges.values() if edge.status.value == "active" and edge.relation is relation and edge.target_id in node_ids]
    counts = Counter(edge.strength for edge in edges)
    evidence_ids = sorted({edge.source_id for edge in edges if graph.nodes[edge.source_id].node_type is GraphNodeType.EVIDENCE})
    sources = sorted({graph.nodes[item].metadata["source_id"] for item in evidence_ids if "source_id" in graph.nodes[item].metadata})
    return EvidenceProfile(direct_count=counts[EdgeStrength.DIRECT], corroborative_count=counts[EdgeStrength.CORROBORATIVE], indirect_count=counts[EdgeStrength.INDIRECT], weak_count=counts[EdgeStrength.WEAK], unique_evidence_ids=evidence_ids, unique_source_ids=sources)


def region_health(graph: CaseGraph, focus: InvestigationFocus, depth: int = 1) -> RegionHealth:
    region = investigator_region(graph, focus, depth)
    node_ids = set(region.nodes)
    unresolved = sorted({edge.source_id for edge in graph.edges.values() if edge.relation is EdgeRelation.TARGETS and edge.source_id in node_ids and graph.nodes[edge.source_id].node_type is GraphNodeType.UNCERTAINTY})
    shared = sum(1 for node in graph.nodes.values() if node.id in node_ids and node.node_type is GraphNodeType.PROPOSITION and len(graph.incoming(node.id, EdgeRelation.DEPENDS_ON)) > 1)
    return RegionHealth(focus_node_id=focus.node_id, node_ids=sorted(node_ids), direct_evidence_profile=direct_evidence_profile(graph, node_ids), conflict_profile=direct_evidence_profile(graph, node_ids, EdgeRelation.CONFLICTS), unresolved_ids=unresolved, current_specialization_depth=len(graph.ancestors(focus.node_id)) if graph.nodes[focus.node_id].node_type is GraphNodeType.HYPOTHESIS else 0, descendant_count=len(graph.descendants(focus.node_id)) if graph.nodes[focus.node_id].node_type is GraphNodeType.HYPOTHESIS else 0, recent_visit_count=focus.recent_node_ids.count(focus.node_id), shared_dependency_count=shared, active=graph.nodes[focus.node_id].status is GraphStatus.ACTIVE)


def neglected_regions(graph: CaseGraph, focus: InvestigationFocus) -> list[str]:
    visited = set(focus.recent_region_node_ids)
    candidates = []
    for node in sorted(graph.nodes.values(), key=lambda item: item.id):
        if node.status is not GraphStatus.ACTIVE or node.id in visited:
            continue
        connected_hypotheses = {edge.target_id for edge in graph.outgoing(node.id, EdgeRelation.SUPPORTS) if graph.nodes[edge.target_id].node_type is GraphNodeType.HYPOTHESIS}
        is_root_hypothesis = node.node_type is GraphNodeType.HYPOTHESIS and not graph.outgoing(node.id, EdgeRelation.SPECIALIZES)
        shared_dependency = node.node_type is GraphNodeType.PROPOSITION and len(graph.incoming(node.id, EdgeRelation.DEPENDS_ON)) > 1
        if node.node_type is GraphNodeType.UNCERTAINTY or len(connected_hypotheses) > 1 or is_root_hypothesis or shared_dependency:
            candidates.append(node.id)
    return candidates


def tunnel_vision_indicators(graph: CaseGraph, focus: InvestigationFocus, recent_graph_node_counts: list[int] | None = None, recent_support_counts: list[int] | None = None, recent_conflict_counts: list[int] | None = None) -> TunnelVisionIndicators:
    roots = []
    for node_id in focus.recent_node_ids:
        if node_id in graph.nodes and graph.nodes[node_id].node_type is GraphNodeType.HYPOTHESIS:
            ancestors = graph.ancestors(node_id)
            roots.append((ancestors[-1].id if ancestors else node_id))
    same_root = 0
    if roots:
        for root in reversed(roots):
            if root != roots[-1]:
                break
            same_root += 1
    depth = len(graph.ancestors(focus.node_id)) if focus.node_id in graph.nodes and graph.nodes[focus.node_id].node_type is GraphNodeType.HYPOTHESIS else 0
    return TunnelVisionIndicators(same_root_steps=same_root, current_specialization_depth=depth, new_nodes_recent=sum(recent_graph_node_counts or []), new_support_edges_recent=sum(recent_support_counts or []), new_conflict_edges_recent=sum(recent_conflict_counts or []), neglected_candidate_ids=neglected_regions(graph, focus))
