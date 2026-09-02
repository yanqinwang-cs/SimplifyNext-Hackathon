from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True)
class SourceRecord:
    source_id: str
    filename: str
    content: str
    metadata: Mapping[str, object] = MappingProxyType({})


class SourceRegistry:
    """Immutable, readable source namespace kept outside the CaseGraph."""

    def __init__(self, records: Mapping[str, SourceRecord]) -> None:
        if set(records) != {record.source_id for record in records.values()}:
            raise ValueError("source mapping keys must match source IDs")
        self._records = MappingProxyType(dict(records))

    @classmethod
    def from_records(cls, records: list[SourceRecord] | tuple[SourceRecord, ...]) -> "SourceRegistry":
        return cls({record.source_id: record for record in records})

    def get(self, source_id: str) -> SourceRecord:
        try:
            return self._records[source_id]
        except KeyError as exc:
            raise ValueError(f"Unknown or non-visible source ID: {source_id!r}") from exc

    def __contains__(self, source_id: str) -> bool:
        return source_id in self._records

    def records(self) -> tuple[SourceRecord, ...]:
        return tuple(self._records.values())
