"""Application-owned raw SourceRegistry boundary."""

import re
from typing import Any

from investigator.graph import GraphNode, GraphNodeType
from investigator.models.source import Source, SourceType
from investigator.state.case_state import CaseState


class SourceRegistry:
    """Register immutable raw sources without creating semantic graph nodes."""

    @staticmethod
    def register_raw_source(state: CaseState, display_name: str, content: str, metadata: dict[str, Any] | None = None) -> Source:
        next_number = max((int(match.group(1)) for key in state.sources if (match := re.fullmatch(r"S(\d+)", key))), default=19) + 1
        source_id = f"S{next_number}"
        source = Source(id=source_id, name=display_name, source_type=SourceType.OTHER, content=content, metadata=dict(metadata or {}))
        if source.id in state.sources:
            raise ValueError(f"Duplicate source ID: {source.id!r}")
        state.sources[source.id] = source
        if state.reasoning_graph is not None:
            state.reasoning_graph.add_node(GraphNode(id=source.id, node_type=GraphNodeType.SOURCE, statement=source.name, semantic_key=source.id, canonical_id=source.id, metadata={"source_type": source.source_type.value, "readable": True}))
        return source
