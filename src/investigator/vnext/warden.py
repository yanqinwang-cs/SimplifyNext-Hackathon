"""Deterministic vNext graph proposal validation and application."""

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from investigator.graph import CaseGraph
from investigator.roles.coordinator import GraphInvestigationCoordinator
from investigator.roles.focus import InvestigationFocus
from investigator.roles.investigator import (
    AddDerivationCommand,
    AddEvidenceCommand,
    AddPropositionCommand,
    AddSpecializationCommand,
    AddSupportCommand,
    AddConflictCommand,
)
from investigator.models.source import Source
from investigator.vnext.models import InvestigatorProposal


class ProposalValidationIssue(BaseModel):
    """A deterministic, prescriptive description of one proposal defect."""

    model_config = ConfigDict(extra="forbid")

    operation_index: int | None = None
    error_code: str
    reference: str | None = None
    actual_type: str | None = None
    allowed_types: list[str] = Field(default_factory=list)
    problem: str
    required_action: str


class WardenValidationError(ValueError):
    """A typed proposal is incompatible with canonical graph/source state."""

    def __init__(self, message: str, *, issues: list[ProposalValidationIssue] | None = None) -> None:
        super().__init__(message)
        self.issues = issues or []


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
            for operation_index, update in enumerate(proposal.graph_updates):
                try:
                    working.apply_investigator_update(update)
                except Exception as exc:
                    raise self._issue_error(operation_index, update, exc, working) from exc
        except Exception as exc:
            if isinstance(exc, WardenValidationError):
                raise exc
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

    @classmethod
    def _issue_error(cls, operation_index: int, update: object, exc: Exception, coordinator: GraphInvestigationCoordinator) -> WardenValidationError:
        message = str(exc)
        reference = cls._reference_for_error(update, message)
        if "Unknown graph node ID" in message:
            code = "UNRESOLVED_REFERENCE"
            required = (
                "Preserve the intended graph meaning; create the intended node earlier in this proposal "
                "with the appropriate add_* operation and local_ref, then reference that local_ref. "
                "Preserve unrelated valid operations."
            )
        elif "invalid type or status" in message:
            code = "INVALID_REFERENCE_TYPE_OR_STATUS"
            node = coordinator.graph.nodes.get(reference or "")
            if node is None and reference is not None:
                node = next((item for item in coordinator.graph.nodes.values() if item.semantic_key == reference), None)
            actual_type = node.node_type.value if node else None
            allowed = cls._allowed_types_for(update)
            required = (
                f"Do not use {reference!r} for this operation with its current type/status. "
                f"Use an active node of an allowed type ({', '.join(allowed)}), or remove this operation "
                "if no legal basis exists; preserve unrelated valid operations."
            )
            issue = ProposalValidationIssue(
                operation_index=operation_index, error_code=code, reference=reference,
                actual_type=actual_type, allowed_types=allowed, problem=message, required_action=required,
            )
            return WardenValidationError(f"Graph Warden rejected proposal: {message}", issues=[issue])
        elif "outside the active local region" in message:
            code = "INVALID_SAME_TURN_DEPENDENCY"
            required = (
                f"Make {reference!r} available before this operation by creating it earlier in the proposal "
                "or use a legal visible node; preserve unrelated valid operations."
            )
        elif "SPECIALIZES" in message:
            code = "ILLEGAL_RELATION_ENDPOINT"
            required = "Preserve the intended specialization only with active hypothesis parent and child nodes; otherwise remove this relation."
        else:
            code = "ILLEGAL_RELATION_ENDPOINT" if "edge" in message.lower() or "relation" in message.lower() else "GRAPH_CONTRACT_FAILURE"
            required = "Repair only this graph-contract defect using the existing operation contract; preserve unrelated valid operations."
        issue = ProposalValidationIssue(
            operation_index=operation_index, error_code=code, reference=reference,
            problem=message, required_action=required,
        )
        return WardenValidationError(f"Graph Warden rejected proposal: {message}", issues=[issue])

    @staticmethod
    def _reference_for_error(update: object, message: str) -> str | None:
        derived_ids = getattr(update, "derived_from_node_ids", None)
        if isinstance(derived_ids, list) and derived_ids:
            return derived_ids[0]
        for field in (
            "derived_proposition_id", "source_node_id", "target_node_id",
            "child_hypothesis_id", "parent_hypothesis_id",
        ):
            value = getattr(update, field, None)
            if isinstance(value, str) and (value in message or "Unknown graph node ID" in message):
                if value in message:
                    return value
        return getattr(update, "derived_proposition_id", None) or getattr(update, "source_node_id", None)

    @staticmethod
    def _allowed_types_for(update: object) -> list[str]:
        if isinstance(update, AddPropositionCommand):
            return ["evidence", "proposition"]
        if isinstance(update, (AddSupportCommand, AddConflictCommand)):
            return ["evidence", "proposition"]
        if isinstance(update, AddDerivationCommand):
            return ["proposition"]
        if isinstance(update, AddSpecializationCommand):
            return ["hypothesis"]
        return []
