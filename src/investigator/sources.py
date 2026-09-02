"""Application-owned raw SourceRegistry boundary."""

import re
from typing import Any

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
        return source
