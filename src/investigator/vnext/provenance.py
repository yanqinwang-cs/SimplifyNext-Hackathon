"""Canonical graph-to-admitted-source provenance resolution."""

from investigator.graph import CaseGraph, EdgeRelation, GraphNodeType


def source_ancestry(graph: CaseGraph, node_id: str) -> set[str]:
    sources: set[str] = set()
    pending = [node_id]
    seen: set[str] = set()
    while pending:
        current = pending.pop()
        if current in seen or current not in graph.nodes:
            continue
        seen.add(current)
        node = graph.nodes[current]
        if node.node_type is GraphNodeType.SOURCE:
            sources.add(node_id)
        sources.update(item for item in node.metadata.get("source_ids", []) if isinstance(item, str))
        pending.extend(edge.target_id for edge in graph.outgoing(current, EdgeRelation.DERIVED_FROM))
    return sources
