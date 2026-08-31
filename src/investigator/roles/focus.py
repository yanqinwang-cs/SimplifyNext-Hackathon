from pydantic import BaseModel, ConfigDict, Field

from investigator.graph import CaseGraph


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
