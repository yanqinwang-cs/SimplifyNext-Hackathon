from pydantic import BaseModel, ConfigDict, Field

from investigator.graph import CaseGraph
from investigator.roles.focus import InvestigationFocus


class GraphSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int
    graph: CaseGraph
    focus: InvestigationFocus
    mutation_reason: str = ""


class GraphHistory(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    snapshots: list[GraphSnapshot] = Field(default_factory=list)

    @property
    def current(self) -> GraphSnapshot:
        if not self.snapshots:
            raise ValueError("Graph history is empty")
        return self.snapshots[-1]

    @property
    def previous(self) -> GraphSnapshot:
        if len(self.snapshots) < 2:
            raise ValueError("Graph history has no previous snapshot")
        return self.snapshots[-2]

    def append(self, graph: CaseGraph, focus: InvestigationFocus, reason: str = "") -> GraphSnapshot:
        snapshot = GraphSnapshot(version=len(self.snapshots), graph=graph.model_copy(deep=True), focus=focus.model_copy(deep=True), mutation_reason=reason)
        self.snapshots.append(snapshot)
        return snapshot

    def restore(self, version: int) -> GraphSnapshot:
        snapshot = next((item for item in self.snapshots if item.version == version), None)
        if snapshot is None:
            raise ValueError(f"Unknown graph history version: {version}")
        return snapshot.model_copy(deep=True)
