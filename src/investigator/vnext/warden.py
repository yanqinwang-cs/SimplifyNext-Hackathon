"""Deterministic vNext graph proposal validation and application."""

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from investigator.graph import CaseGraph, GraphNodeType, GraphStatus, OperationSpecRegistry
from investigator.roles.coordinator import GraphInvestigationCoordinator
from investigator.roles.focus import InvestigationFocus
from investigator.roles.investigator import AddEvidenceCommand
from investigator.models.source import Source
from investigator.vnext.models import InvestigatorProposal


class ProposalValidationIssue(BaseModel):
    """A deterministic, prescriptive description of one proposal defect."""

    model_config = ConfigDict(extra="forbid")

    operation_index: int | None = None
    field: str | None = None
    error_code: str
    reference: str | None = None
    actual_type: str | None = None
    actual_status: str | None = None
    allowed_types: list[str] = Field(default_factory=list)
    allowed_statuses: list[str] = Field(default_factory=list)
    construction_operation: str | None = None
    construction_allowed_types: list[str] = Field(default_factory=list)
    available_refs: dict[str, str] = Field(default_factory=dict)
    known_illegal_refs: dict[str, str] = Field(default_factory=dict)
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
            preflight_issues = self._preflight_issues(proposal, working)
            if preflight_issues:
                raise WardenValidationError("Graph Warden rejected proposal preflight", issues=preflight_issues)
            for operation_index, update in enumerate(proposal.graph_updates):
                try:
                    working.apply_investigator_update(update)
                except Exception as exc:
                    if isinstance(exc, WardenValidationError):
                        raise
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
    def _preflight_issues(
        cls, proposal: InvestigatorProposal, coordinator: GraphInvestigationCoordinator
    ) -> list[ProposalValidationIssue]:
        known = dict(coordinator.graph.nodes)
        known.update({node.semantic_key: node for node in coordinator.graph.nodes.values() if node.semantic_key})
        future_refs = {
            update.local_ref: (index, OperationSpecRegistry.contract(update.operation).created_type)
            for index, update in enumerate(proposal.graph_updates)
            if getattr(update, "local_ref", None) and OperationSpecRegistry.contract(update.operation).created_type
        }
        issues: list[ProposalValidationIssue] = []
        for operation_index, update in enumerate(proposal.graph_updates):
            for reference, field, allowed, item_index in cls._reference_specs(update):
                node = known.get(coordinator._resolve_ref(reference)) or known.get(reference)
                field_label = f"{update.operation}.{field}{f'[{item_index}]' if item_index is not None else ''}"
                if node is None:
                    future = future_refs.get(reference)
                    code = "INVALID_SAME_TURN_REFERENCE" if future and future[0] > operation_index else "UNRESOLVED_REFERENCE"
                    construction = "add_proposition" if GraphNodeType.PROPOSITION in allowed else None
                    construction_types = sorted(item.value for item in cls._construction_types(construction))
                    available, illegal = cls._reference_hints(known, allowed)
                    required = f"Create {reference!r} earlier in this proposal with the intended add_* operation, then preserve this reference; otherwise remove only this operation. Preserve unrelated valid operations."
                    if construction:
                        required += f" If creating it as a proposition, derived_from_node_ids may use only: {', '.join(construction_types)}."
                    issues.append(ProposalValidationIssue(
                        operation_index=operation_index, field=field_label, error_code=code, reference=reference,
                        allowed_types=sorted(item.value for item in allowed), construction_operation=construction,
                        construction_allowed_types=construction_types, available_refs=available, known_illegal_refs=illegal,
                        problem=f"Operation {operation_index} ({field_label}) references {reference!r}, but that graph node is unavailable here.",
                        required_action=required,
                    ))
                    continue
                if node.node_type not in allowed:
                    allowed_names = sorted(item.value for item in allowed)
                    issues.append(ProposalValidationIssue(
                        operation_index=operation_index, field=field_label, error_code="INVALID_REFERENCE_TYPE", reference=reference,
                        actual_type=node.node_type.value, actual_status=node.status.value, allowed_types=allowed_names,
                        allowed_statuses=[GraphStatus.ACTIVE.value],
                        problem=f"Operation {operation_index} ({field_label}) references {reference!r}, which has type {node.node_type.value.upper()}; this field accepts {', '.join(name.upper() for name in allowed_names)}.",
                        required_action=f"Replace {reference!r} with an active legal node of type {', '.join(allowed_names)}, or remove only this operation if no legal basis exists. Preserve unrelated valid operations.",
                    ))
                elif node.status is not GraphStatus.ACTIVE:
                    issues.append(ProposalValidationIssue(
                        operation_index=operation_index, field=field_label, error_code="INVALID_REFERENCE_STATUS", reference=reference,
                        actual_type=node.node_type.value, actual_status=node.status.value,
                        allowed_types=sorted(item.value for item in allowed), allowed_statuses=[GraphStatus.ACTIVE.value],
                        problem=f"Operation {operation_index} ({field_label}) references {reference!r} with allowed type {node.node_type.value.upper()} but current status {node.status.value.upper()} is not ACTIVE.",
                        required_action=f"Use an active legal {node.node_type.value} node, correct same-turn dependency ordering if applicable, or remove only this operation. Preserve unrelated valid operations.",
                    ))
            if update.operation == "add_derivation":
                derived_id = coordinator._resolve_ref(update.derived_proposition_id)
                source_id = coordinator._resolve_ref(update.source_node_id)
                derived_node = known.get(derived_id) or known.get(update.derived_proposition_id)
                source_node = known.get(source_id) or known.get(update.source_node_id)
                if derived_node is not None and source_node is not None and derived_id == source_id:
                    issues.append(ProposalValidationIssue(
                        operation_index=operation_index,
                        field="add_derivation",
                        error_code="SELF_DERIVATION",
                        reference=update.source_node_id,
                        actual_type=GraphNodeType.PROPOSITION.value,
                        allowed_types=[GraphNodeType.EVIDENCE.value, GraphNodeType.PROPOSITION.value],
                        problem=(
                            f"Operation {operation_index} (add_derivation) uses proposition "
                            f"{update.derived_proposition_id!r} as its own derivation source."
                        ),
                        required_action=(
                            "Remove only this add_derivation operation, or replace its source with a distinct "
                            "active EVIDENCE or PROPOSITION basis. The proposition's existing derived_from_node_ids "
                            "already record its legal derivation basis; preserve unrelated valid operations."
                        ),
                    ))
            created_type = OperationSpecRegistry.contract(update.operation).created_type
            local_ref = getattr(update, "local_ref", None)
            if local_ref and created_type:
                known[local_ref] = type("PlannedNode", (), {"node_type": created_type, "status": GraphStatus.ACTIVE, "semantic_key": local_ref})()
        return issues

    @staticmethod
    def _construction_types(operation: str | None) -> frozenset[GraphNodeType]:
        return OperationSpecRegistry.allowed_types_for(operation, "derived_from_node_ids") if operation else frozenset()

    @staticmethod
    def _reference_hints(known: Mapping[str, object], allowed: set[GraphNodeType]) -> tuple[dict[str, str], dict[str, str]]:
        available: dict[str, str] = {}
        illegal: dict[str, str] = {}
        for reference, node in known.items():
            node_type = getattr(node, "node_type", None)
            if node_type is None or reference.startswith("node_"):
                continue
            (available if node_type in allowed else illegal)[reference] = node_type.value
        return dict(sorted(available.items())), dict(sorted(illegal.items()))

    @staticmethod
    def _reference_specs(update: object) -> list[tuple[str, str, set[GraphNodeType], int | None]]:
        contract = OperationSpecRegistry.contract(update.operation)
        references: list[tuple[str, str, set[GraphNodeType], int | None]] = []
        for spec in contract.references:
            value = getattr(update, spec.field)
            if spec.itemized:
                references.extend((item, spec.field, set(spec.allowed_types), index) for index, item in enumerate(value))
            else:
                references.append((value, spec.field, set(spec.allowed_types), None))
        return references

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
