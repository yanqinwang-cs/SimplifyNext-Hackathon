"""Deterministic vNext graph proposal validation and application."""

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from investigator.graph import CaseGraph
from investigator.roles.coordinator import GraphInvestigationCoordinator
from investigator.roles.focus import InvestigationFocus
from investigator.roles.investigator import AddEvidenceCommand
from investigator.models.source import Source
from investigator.vnext.models import InvestigatorProposal


class WardenValidationError(ValueError):
    """A typed proposal is incompatible with canonical graph/source state."""


class WardenApplyResult(BaseModel):
    """The accepted canonical result of one atomic proposal application."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")
    graph: CaseGraph
    applied_updates: list[dict[str, Any]] = Field(default_factory=list)
    created_node_ids: list[str] = Field(default_factory=list)
    local_ref_resolution: dict[str, str] = Field(default_factory=dict)


class GraphWarden:
    """Validate and atomically apply typed Investigator proposals."""

    def __init__(self, graph: CaseGraph, sources: Mapping[str, Source] | None = None) -> None:
        self.graph = graph
        self.sources = dict(sources or {})

    def apply(self, proposal: InvestigatorProposal) -> WardenApplyResult:
        """Apply a validated proposal, committing only after the whole batch succeeds."""
        if not isinstance(proposal, InvestigatorProposal):
            raise TypeError("GraphWarden.apply requires an InvestigatorProposal")

        self._validate_source_provenance(proposal)
        before = deepcopy(self.graph)
        if not proposal.graph_updates:
            return WardenApplyResult(graph=deepcopy(self.graph))

        try:
            working = GraphInvestigationCoordinator(
                deepcopy(self.graph),
                InvestigationFocus(node_id=next(iter(self.graph.nodes))),
                full_graph_visibility=True,
            )
            for update in proposal.graph_updates:
                working.apply_investigator_update(update)
        except Exception as exc:
            raise WardenValidationError(f"Graph Warden rejected proposal: {exc}") from exc

        accepted_graph = deepcopy(working.graph)
        self.graph = accepted_graph
        created_node_ids = sorted(set(accepted_graph.nodes) - set(before.nodes))
        local_ref_resolution = {
            node.semantic_key: node.id
            for node in accepted_graph.nodes.values()
            if node.id in created_node_ids and node.semantic_key is not None
        }
        return WardenApplyResult(
            graph=deepcopy(accepted_graph),
            applied_updates=[update.model_dump(mode="json") for update in proposal.graph_updates],
            created_node_ids=created_node_ids,
            local_ref_resolution=local_ref_resolution,
        )

    def _validate_source_provenance(self, proposal: InvestigatorProposal) -> None:
        for update in proposal.graph_updates:
            if not isinstance(update, AddEvidenceCommand):
                continue
            for source_id in update.source_ids:
                source = self.sources.get(source_id)
                if source is None:
                    raise WardenValidationError(f"Graph Warden rejected unknown raw source ID: {source_id!r}")
                if source.id != source_id:
                    raise WardenValidationError(f"Graph Warden rejected invalid raw source record: {source_id!r}")
