"""Deterministic vNext graph proposal validation and application."""

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from investigator.graph import CaseGraph, EdgeRelation, GraphNodeType, GraphScope, GraphScopeType, GraphStatus, OperationSpecRegistry, node_scope, scopes_compatible
from investigator.roles.coordinator import GraphInvestigationCoordinator
from investigator.roles.focus import InvestigationFocus
from investigator.roles.investigator import AddEvidenceCommand
from investigator.models.assessment import AssessmentSubject, SubjectRelationship
from investigator.models.source import Source
from investigator.vnext.models import InvestigatorProposal
from investigator.vnext.relationships import deterministic_relationship_id
from investigator.vnext.source_applicability import SourceApplicability, build_source_applicability, source_applies_to_student, source_jointly_identifies


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
    relation: str | None = None
    source: str | None = None
    target: str | None = None
    first_operation_index: int | None = None
    source_scope: dict[str, Any] | None = None
    target_scope: dict[str, Any] | None = None
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
    relationship_registry: dict[str, SubjectRelationship] = Field(default_factory=dict)
    relationship_ref_resolution: dict[str, str] = Field(default_factory=dict)


class GraphWarden:
    """Validate and atomically apply typed Investigator proposals."""

    def __init__(
        self,
        graph: CaseGraph,
        sources: Mapping[str, Source] | None = None,
        *,
        subjects: Mapping[str, AssessmentSubject] | None = None,
        subject_relationships: Mapping[str, SubjectRelationship] | None = None,
        source_applicability: Mapping[str, SourceApplicability] | None = None,
        relationship_refs: Mapping[str, str] | None = None,
        strict_relationship_refs: bool = False,
    ) -> None:
        self.graph = graph
        self.sources = dict(sources or {})
        self.subjects = dict(subjects or {})
        self.subject_relationships = dict(subject_relationships or {})
        self.source_applicability = dict(source_applicability or build_source_applicability(self.sources, self.subjects, self.subject_relationships))
        self.relationship_ref_map = dict(relationship_refs or {
            f"R{index}": relationship_id
            for index, relationship_id in enumerate(sorted(self.subject_relationships), start=1)
        })
        self.strict_relationship_refs = strict_relationship_refs

    def apply(self, proposal: InvestigatorProposal) -> WardenApplyResult:
        """Apply a validated proposal, committing only after the whole batch succeeds."""
        if not isinstance(proposal, InvestigatorProposal):
            raise TypeError("GraphWarden.apply requires an InvestigatorProposal")

        before = deepcopy(self.graph)
        before_relationships = deepcopy(self.subject_relationships)
        before_refs = dict(self.relationship_ref_map)
        working_relationships, working_refs = self._prepare_relationship_registry(proposal)
        self.subject_relationships = working_relationships
        self.relationship_ref_map = working_refs

        try:
            normalized_proposal = self._normalize_relationship_refs(proposal)
            normalized_proposal = self._normalize_compatibility_scopes(normalized_proposal)
            self._validate_source_provenance(normalized_proposal)
            if not normalized_proposal.graph_updates:
                self.graph = deepcopy(self.graph)
                return WardenApplyResult(
                    graph=deepcopy(self.graph),
                    relationship_registry=deepcopy(self.subject_relationships),
                    relationship_ref_resolution={key: value for key, value in self.relationship_ref_map.items()},
                )
            working = GraphInvestigationCoordinator(
                deepcopy(self.graph),
                InvestigationFocus(node_id=next(iter(self.graph.nodes))),
                full_graph_visibility=True,
            )
            preflight_issues = self._preflight_issues(normalized_proposal, working)
            if preflight_issues:
                raise WardenValidationError("Graph Warden rejected proposal preflight", issues=preflight_issues)
            for operation_index, update in enumerate(normalized_proposal.graph_updates):
                try:
                    working.apply_investigator_update(update)
                except Exception as exc:
                    if isinstance(exc, WardenValidationError):
                        raise
                    raise self._issue_error(operation_index, update, exc, working) from exc
        except Exception as exc:
            self.subject_relationships = before_relationships
            self.relationship_ref_map = before_refs
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
            relationship_registry=deepcopy(self.subject_relationships),
            relationship_ref_resolution={key: value for key, value in self.relationship_ref_map.items()},
        )

    def _prepare_relationship_registry(
        self, proposal: InvestigatorProposal
    ) -> tuple[dict[str, SubjectRelationship], dict[str, str]]:
        registry = deepcopy(self.subject_relationships)
        refs = dict(self.relationship_ref_map)
        declarations = proposal.relationship_scopes
        if not declarations:
            return registry, refs
        used_refs = {
            update.scope.relationship_ref
            for update in proposal.graph_updates
            if getattr(update, "scope", None) is not None
            and update.scope.scope_type is GraphScopeType.RELATIONSHIP
            and update.scope.relationship_ref is not None
        }
        declared_sets: dict[tuple[str, ...], str] = {}
        for declaration in declarations:
            if declaration.local_ref in refs:
                raise WardenValidationError(
                    f"Relationship local ref {declaration.local_ref!r} is already allocated",
                    issues=[self._relationship_issue("DUPLICATE_RELATIONSHIP_REF", declaration.local_ref, "Use one unused local relationship_ref.")],
                )
            if declaration.local_ref not in used_refs:
                raise WardenValidationError(
                    f"Relationship declaration {declaration.local_ref!r} is unused",
                    issues=[self._relationship_issue("UNUSED_RELATIONSHIP_DECLARATION", declaration.local_ref, "Use the local_ref in at least one relationship-scoped graph node, or remove the declaration.")],
                )
            unknown_students = sorted(set(declaration.student_ids) - set(self.subjects))
            if unknown_students:
                raise WardenValidationError(
                    f"Relationship declaration {declaration.local_ref!r} contains unknown students: {unknown_students}",
                    issues=[self._relationship_issue("UNKNOWN_RELATIONSHIP_STUDENT", declaration.local_ref, "Use only configured student IDs.")],
                )
            participants = tuple(sorted(declaration.student_ids))
            if participants in declared_sets:
                raise WardenValidationError(
                    f"Relationship declarations {declared_sets[participants]!r} and {declaration.local_ref!r} duplicate the participant set",
                    issues=[self._relationship_issue("DUPLICATE_RELATIONSHIP_PARTICIPANTS", declaration.local_ref, "Declare one relationship scope for each participant set.")],
                )
            declared_sets[participants] = declaration.local_ref
            unknown_sources = sorted(set(declaration.basis_source_ids) - set(self.sources))
            if unknown_sources:
                raise WardenValidationError(
                    f"Relationship declaration {declaration.local_ref!r} contains unknown sources: {unknown_sources}",
                    issues=[self._relationship_issue("UNKNOWN_RELATIONSHIP_SOURCE", declaration.local_ref, "Use only admitted source IDs.")],
                )
            if not any(source_jointly_identifies(self.source_applicability[source_id], set(participants), self.subject_relationships) for source_id in declaration.basis_source_ids):
                raise WardenValidationError(
                    f"Relationship declaration {declaration.local_ref!r} lacks one joint source covering all participants",
                    issues=[self._relationship_issue("MISSING_JOINT_SOURCE", declaration.local_ref, "Use one admitted source that explicitly identifies every proposed participant; do not stitch private sources.")],
                )
            relationship_id = deterministic_relationship_id(self.graph.case_id, participants)
            existing = registry.get(relationship_id)
            if existing is not None and set(existing.subject_ids) != set(participants):
                raise WardenValidationError("Deterministic relationship ID collision")
            if existing is None:
                registry[relationship_id] = SubjectRelationship(
                    relationship_id=relationship_id,
                    subject_ids=list(participants),
                    relationship_type="evidence_backed_provisional",
                    source_ids=list(declaration.basis_source_ids),
                    description="Run-local evidence-backed relationship scope.",
                )
            else:
                registry[relationship_id] = existing.model_copy(update={"source_ids": sorted(set(existing.source_ids) | set(declaration.basis_source_ids))})
            refs[declaration.local_ref] = relationship_id
        return registry, refs

    @staticmethod
    def _relationship_issue(error_code: str, reference: str, required_action: str) -> ProposalValidationIssue:
        return ProposalValidationIssue(error_code=error_code, reference=reference, problem=error_code, required_action=required_action)

    def _normalize_relationship_refs(self, proposal: InvestigatorProposal) -> InvestigatorProposal:
        updates = []
        for update in proposal.graph_updates:
            scope = getattr(update, "scope", None)
            if scope is not None and scope.scope_type is GraphScopeType.RELATIONSHIP:
                if scope.relationship_ref is not None:
                    relationship_id = self.relationship_ref_map.get(scope.relationship_ref)
                    if relationship_id is None:
                        raise WardenValidationError(
                            f"Unknown relationship_ref {scope.relationship_ref!r}",
                            issues=[self._relationship_issue("UNKNOWN_RELATIONSHIP_REF", scope.relationship_ref, "Use an available relationship_ref or remove this scoped node.")],
                        )
                    update = update.model_copy(update={"scope": scope.model_copy(update={"relationship_ref": None, "relationship_id": relationship_id})})
                elif self.strict_relationship_refs and scope.relationship_id is not None:
                    raise WardenValidationError(
                        "Model-facing graph actions must use relationship_ref, not relationship_id",
                        issues=[self._relationship_issue("DIRECT_RELATIONSHIP_ID", scope.relationship_id, "Use a local relationship_ref such as R1.")],
                    )
            updates.append(update)
        return InvestigatorProposal(graph_updates=updates, relationship_scopes=proposal.relationship_scopes)

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
            if update.scope is not None:
                scope_error = self._scope_identity_error(update.scope)
                if scope_error is not None:
                    raise WardenValidationError(
                        f"Graph Warden rejected invalid assessment scope: {scope_error}",
                        issues=[self._relationship_issue("INVALID_SCOPE", update.scope.scope_type.value, "Use a configured student or relationship scope.")],
                    )
            self._validate_evidence_scope(update)

    def _validate_evidence_scope(self, update: AddEvidenceCommand) -> None:
        scope = update.scope
        if scope is None or not self.subjects:
            return
        for source_id in update.source_ids:
            applicability = self.source_applicability[source_id]
            if scope.scope_type is GraphScopeType.CASE:
                if applicability.classification.value in {"student_specific", "multi_student_candidate"} and len(self.subjects) > 1:
                    raise WardenValidationError(
                        f"Graph Warden rejected source {source_id!r} widened to CASE scope",
                        issues=[self._relationship_issue("SOURCE_SCOPE_WIDENING", source_id, "Use the matched student or a validated relationship scope.")],
                    )
            elif scope.scope_type is GraphScopeType.SUBJECT:
                if not source_applies_to_student(applicability, scope.subject_id or "", self.subjects):
                    raise WardenValidationError(
                        f"Graph Warden rejected source {source_id!r} for student {scope.subject_id!r}",
                        issues=[self._relationship_issue("SOURCE_STUDENT_MISMATCH", source_id, "Use only a source applicable to this configured student.")],
                    )
            elif scope.scope_type is GraphScopeType.RELATIONSHIP:
                relationship = self.subject_relationships.get(scope.relationship_id or "")
                if relationship is None or not source_jointly_identifies(applicability, set(relationship.subject_ids), self.subject_relationships):
                    raise WardenValidationError(
                        f"Graph Warden rejected source {source_id!r} for relationship scope",
                        issues=[self._relationship_issue("SOURCE_RELATIONSHIP_MISMATCH", source_id, "Use one joint source that covers every relationship participant.")],
                    )

    def _normalize_compatibility_scopes(self, proposal: InvestigatorProposal) -> InvestigatorProposal:
        if self.subjects:
            return proposal
        compatibility_scope = GraphScope(scope_type=GraphScopeType.SUBJECT, subject_id="case_subject")
        updates = []
        for update in proposal.graph_updates:
            if hasattr(update, "scope") and update.scope is None:
                updates.append(update.model_copy(update={"scope": compatibility_scope}))
            else:
                updates.append(update)
        return InvestigatorProposal(graph_updates=updates)

    def _preflight_issues(
        self, proposal: InvestigatorProposal, coordinator: GraphInvestigationCoordinator
    ) -> list[ProposalValidationIssue]:
        cls = type(self)
        known = dict(coordinator.graph.nodes)
        known.update({node.semantic_key: node for node in coordinator.graph.nodes.values() if node.semantic_key})
        future_refs = {
            update.local_ref: (index, OperationSpecRegistry.contract(update.operation).created_type)
            for index, update in enumerate(proposal.graph_updates)
            if getattr(update, "local_ref", None) and OperationSpecRegistry.contract(update.operation).created_type
        }
        issues: list[ProposalValidationIssue] = []
        relations = cls._existing_relations(coordinator.graph)
        for operation_index, update in enumerate(proposal.graph_updates):
            scope = self._effective_scope(update)
            if hasattr(update, "scope"):
                if update.scope is None and self.subjects:
                    issues.append(ProposalValidationIssue(
                        operation_index=operation_index,
                        field=f"{update.operation}.scope",
                        error_code="MISSING_SCOPE",
                        problem=f"Operation {operation_index} ({update.operation}) creates a semantic node without an explicit assessment scope.",
                        required_action="Add an explicit CASE, SUBJECT, or RELATIONSHIP scope for this node; do not infer scope from prose.",
                    ))
                elif scope is not None:
                    scope_error = self._scope_identity_error(scope)
                    if scope_error is not None:
                        issues.append(ProposalValidationIssue(
                            operation_index=operation_index,
                            field=f"{update.operation}.scope",
                            error_code="INVALID_SCOPE",
                            problem=f"Operation {operation_index} ({update.operation}) uses invalid assessment scope: {scope_error}.",
                            required_action="Use CASE, a configured SUBJECT, or a recorded RELATIONSHIP scope; do not invent identities.",
                        ))
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
                            f"Operation {operation_index} derives proposition "
                            f"{update.derived_proposition_id!r} from itself."
                        ),
                        required_action=(
                            "The proposition cannot be derived from itself. This proposition already declares its "
                            "legal derivation basis through derived_from_node_ids. Remove only this redundant "
                            "add_derivation operation, or replace its source with a distinct legal EVIDENCE or "
                            "PROPOSITION basis if a separate derivation is actually needed. Preserve unrelated "
                            "valid operations."
                        ),
                    ))
            for source, relation, target, field in cls._planned_relations(update, known):
                relation_key = (source, relation.value, target)
                if relation is EdgeRelation.DERIVED_FROM and source == target:
                    continue
                first = relations.get(relation_key)
                if first is not None:
                    first_index, first_description = first
                    operation_name = update.operation
                    required = (
                        f"Remove only this redundant {operation_name} operation. Preserve the operation that already "
                        "creates this semantic relation and preserve unrelated valid operations."
                    )
                    if relation is EdgeRelation.DERIVED_FROM and operation_name == "add_derivation":
                        required = (
                            "Remove only this redundant add_derivation operation. Preserve the add_proposition and "
                            "its existing derived_from_node_ids. Preserve unrelated valid operations."
                        )
                    issues.append(ProposalValidationIssue(
                        operation_index=operation_index,
                        field=field,
                        error_code="DUPLICATE_RELATION",
                        relation=relation.value,
                        source=source,
                        target=target,
                        first_operation_index=first_index,
                        problem=(
                            f"Operation {operation_index} attempts to add {relation.value.upper()}({source}, {target}), "
                            f"but that relation is already created by {first_description}."
                        ),
                        required_action=required,
                    ))
                else:
                    relations[relation_key] = (
                        operation_index,
                        f"operation {operation_index} through {update.operation}.{field}",
                    )
                source_scope = self._reference_scope(source, known, update, scope)
                target_scope = self._reference_scope(target, known, update, scope)
                if source_scope is not None and target_scope is not None and not scopes_compatible(source_scope, target_scope, self.subject_relationships):
                    issues.append(ProposalValidationIssue(
                        operation_index=operation_index,
                        field=field,
                        error_code="INCOMPATIBLE_SCOPE",
                        relation=relation.value,
                        source=source,
                        target=target,
                        source_scope=source_scope.model_dump(mode="json"),
                        target_scope=target_scope.model_dump(mode="json"),
                        problem=(
                            f"Operation {operation_index} attempts {relation.value.upper()} from {source!r} "
                            f"({source_scope.scope_type.value} scope) to {target!r} ({target_scope.scope_type.value} scope), "
                            "but their assessment scopes are incompatible."
                        ),
                        required_action=(
                            "Preserve subject separation; remove this edge or represent genuinely cross-subject "
                            "evidence through an existing relationship containing both subjects. Do not create a "
                            "relationship automatically."
                        ),
                    ))
            created_type = OperationSpecRegistry.contract(update.operation).created_type
            local_ref = getattr(update, "local_ref", None)
            if local_ref and created_type:
                known[local_ref] = type("PlannedNode", (), {"node_type": created_type, "status": GraphStatus.ACTIVE, "semantic_key": local_ref, "scope": scope})()
        return issues

    def _effective_scope(self, update: object) -> GraphScope | None:
        scope = getattr(update, "scope", None)
        if scope is not None:
            return scope
        if not self.subjects and hasattr(update, "scope"):
            return GraphScope(scope_type=GraphScopeType.SUBJECT, subject_id="case_subject")
        return None

    def _scope_identity_error(self, scope: GraphScope) -> str | None:
        if scope.scope_type is GraphScopeType.SUBJECT:
            allowed = set(self.subjects) or {"case_subject"}
            return None if scope.subject_id in allowed else f"unknown subject ID {scope.subject_id!r}"
        if scope.scope_type is GraphScopeType.RELATIONSHIP:
            relationship = self.subject_relationships.get(scope.relationship_id)
            if relationship is None:
                return f"unknown relationship ID {scope.relationship_id!r}"
            unknown_subjects = sorted(set(relationship.subject_ids) - set(self.subjects))
            if unknown_subjects:
                return f"relationship {scope.relationship_id!r} contains unknown subject IDs {unknown_subjects}"
            if len(set(relationship.subject_ids)) < 2:
                return f"relationship {scope.relationship_id!r} must contain at least two subjects"
            return None
        return None

    def _reference_scope(self, reference: str, known: Mapping[str, object], update: object, current_scope: GraphScope | None) -> GraphScope | None:
        node = known.get(reference)
        if node is not None:
            return getattr(node, "scope", node_scope(getattr(node, "metadata", {})))
        if reference in {getattr(update, "local_ref", None), getattr(update, "node_id", None)}:
            return current_scope
        return None

    @staticmethod
    def _existing_relations(graph: CaseGraph) -> dict[tuple[str, str, str], tuple[int | None, str]]:
        result: dict[tuple[str, str, str], tuple[int | None, str]] = {}
        for edge in graph.edges.values():
            if edge.relation is EdgeRelation.DERIVED_FROM:
                source_node, target_node = graph.nodes[edge.target_id], graph.nodes[edge.source_id]
            else:
                source_node, target_node = graph.nodes[edge.source_id], graph.nodes[edge.target_id]
            source = source_node.semantic_key or source_node.id
            target = target_node.semantic_key or target_node.id
            result[(source, edge.relation.value, target)] = (None, f"the canonical graph edge {edge.id}")
        return result

    @staticmethod
    def _planned_relations(
        update: object, known: Mapping[str, object]
    ) -> list[tuple[str, EdgeRelation, str, str]]:
        def identity(reference: str) -> str:
            node = known.get(reference)
            return getattr(node, "semantic_key", None) or getattr(node, "id", None) or reference

        if update.operation == "add_proposition":
            target = getattr(update, "local_ref", None) or getattr(update, "node_id", None)
            if target is None:
                return []
            return [
                (identity(reference), EdgeRelation.DERIVED_FROM, target, "derived_from_node_ids")
                for reference in update.derived_from_node_ids
            ]
        if update.operation == "add_derivation":
            return [(
                identity(update.source_node_id), EdgeRelation.DERIVED_FROM,
                identity(update.derived_proposition_id), "add_derivation",
            )]
        if update.operation in {"add_support", "add_conflict"}:
            relation = EdgeRelation.SUPPORTS if update.operation == "add_support" else EdgeRelation.CONFLICTS
            return [(identity(update.source_node_id), relation, identity(update.target_node_id), update.operation)]
        if update.operation == "add_uncertainty":
            source = getattr(update, "local_ref", None) or getattr(update, "node_id", None)
            return [] if source is None else [(source, EdgeRelation.TARGETS, identity(update.target_node_id), "target_node_id")]
        if update.operation == "add_specialization":
            return [(identity(update.child_hypothesis_id), EdgeRelation.SPECIALIZES, identity(update.parent_hypothesis_id), "add_specialization")]
        return []

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
