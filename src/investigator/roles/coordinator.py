from copy import deepcopy
import hashlib

from investigator.graph import CaseGraph, EdgeRelation, GraphEdge, GraphNode, GraphNodeType, GraphStatus, OperationSpecRegistry, make_edge_id
from investigator.roles.focus import InvestigationFocus, investigator_region
from investigator.roles.history import GraphHistory
from investigator.roles.investigator import AddConflictCommand, AddDerivationCommand, AddEvidenceCommand, AddHypothesisCommand, AddPropositionCommand, AddSpecializationCommand, AddSupportCommand, AddUncertaintyCommand, INVESTIGATOR_UPDATE_ADAPTER, InvestigatorUpdate, MoveFocusCommand
from investigator.roles.steward import ArchiveDecision, GeneralizeDecision, ReactivateDecision, ShiftFocusDecision, StopUnresolvedDecision, StewardDecision, StewardReviewContext


class GraphInvestigationCoordinator:
    """Offline sequential coordinator enforcing Investigator and Steward boundaries."""

    def __init__(self, graph: CaseGraph, focus: InvestigationFocus, *, full_graph_visibility: bool = False) -> None:
        self._require_node(graph, focus.node_id)
        self.graph, self.focus = graph, focus
        self.full_graph_visibility = full_graph_visibility
        self._new_nodes: set[str] = set()
        self._turn_refs: dict[str, str] = {}
        self.history = GraphHistory()
        self.history.append(graph, focus, "initial graph")
        self.stopped = False

    def apply_investigator_update(self, update: InvestigatorUpdate) -> None:
        update = INVESTIGATOR_UPDATE_ADAPTER.validate_python(update)
        if self.stopped:
            raise ValueError("Coordinator is stopped")
        candidate = deepcopy(self.graph)
        # Keep newly created IDs available for later local relation commands;
        # provenance edges must not consume that locality allowance.
        new_nodes = set(self._new_nodes)
        if isinstance(update, AddEvidenceCommand):
            self._apply_add_evidence(candidate, new_nodes, update)
        elif isinstance(update, AddPropositionCommand):
            self._apply_add_proposition(candidate, new_nodes, update)
        elif isinstance(update, AddHypothesisCommand):
            self._apply_add_hypothesis(candidate, new_nodes, update)
        elif isinstance(update, AddUncertaintyCommand):
            self._apply_add_uncertainty(candidate, new_nodes, update)
        elif isinstance(update, (AddSupportCommand, AddConflictCommand)):
            self._apply_relation(candidate, new_nodes, update)
        elif isinstance(update, AddDerivationCommand):
            self._apply_derivation(candidate, new_nodes, update)
        elif isinstance(update, AddSpecializationCommand):
            self._apply_specialization(candidate, new_nodes, update)
        elif isinstance(update, MoveFocusCommand):
            self._move_focus(self._resolve_ref(update.focus_node_id), update.reason, self._permitted_ids())
        if not isinstance(update, MoveFocusCommand):
            self.graph = candidate
        self._new_nodes = new_nodes
        self.history.append(self.graph, self.focus, update.reason)

    def _apply_add_evidence(self, graph: CaseGraph, new_nodes: set[str], update: AddEvidenceCommand) -> None:
        node_id = self._allocate_node_id(graph, GraphNodeType.EVIDENCE, update.node_id, update.local_ref, update.statement)
        graph.add_node(GraphNode(id=node_id, node_type=GraphNodeType.EVIDENCE, statement=update.statement, semantic_key=update.local_ref, canonical_id=node_id, metadata={"source_ids": list(update.source_ids)}))
        self._remember_ref(update.local_ref, node_id)
        new_nodes.add(node_id)

    def _apply_add_proposition(self, graph: CaseGraph, new_nodes: set[str], update: AddPropositionCommand) -> None:
        node_id = self._allocate_node_id(graph, GraphNodeType.PROPOSITION, update.node_id, update.local_ref, update.statement)
        permitted = self._permitted_ids() | new_nodes
        allowed = OperationSpecRegistry.allowed_types_for(update.operation, "derived_from_node_ids")
        sources = [self._require_local_active(graph, self._resolve_ref(identifier), set(allowed), permitted) for identifier in update.derived_from_node_ids]
        graph.add_node(GraphNode(id=node_id, node_type=GraphNodeType.PROPOSITION, statement=update.statement, semantic_key=update.local_ref, canonical_id=node_id))
        for source in sources:
            graph.add_edge(GraphEdge(id=make_edge_id(node_id, EdgeRelation.DERIVED_FROM, source.id), source_id=node_id, target_id=source.id, relation=EdgeRelation.DERIVED_FROM, explanation=update.reason))
        self._remember_ref(update.local_ref, node_id)
        new_nodes.add(node_id)

    def _apply_add_hypothesis(self, graph: CaseGraph, new_nodes: set[str], update: AddHypothesisCommand) -> None:
        node_id = self._allocate_node_id(graph, GraphNodeType.HYPOTHESIS, update.node_id, update.local_ref, update.statement)
        graph.add_node(GraphNode(id=node_id, node_type=GraphNodeType.HYPOTHESIS, statement=update.statement, semantic_key=update.local_ref, canonical_id=node_id))
        self._remember_ref(update.local_ref, node_id)
        new_nodes.add(node_id)

    def _apply_add_uncertainty(self, graph: CaseGraph, new_nodes: set[str], update: AddUncertaintyCommand) -> None:
        node_id = self._allocate_node_id(graph, GraphNodeType.UNCERTAINTY, update.node_id, update.local_ref, update.statement)
        permitted = self._permitted_ids() | new_nodes
        allowed = OperationSpecRegistry.allowed_types_for(update.operation, "target_node_id")
        target = self._require_local_active(graph, self._resolve_ref(update.target_node_id), set(allowed), permitted)
        graph.add_node(GraphNode(id=node_id, node_type=GraphNodeType.UNCERTAINTY, statement=update.statement, semantic_key=update.local_ref, canonical_id=node_id))
        graph.add_edge(GraphEdge(id=make_edge_id(node_id, EdgeRelation.TARGETS, target.id), source_id=node_id, target_id=target.id, relation=EdgeRelation.TARGETS, explanation=update.reason))
        self._remember_ref(update.local_ref, node_id)
        new_nodes.add(node_id)

    def _apply_relation(self, graph: CaseGraph, new_nodes: set[str], update: AddSupportCommand | AddConflictCommand) -> None:
        permitted = self._permitted_ids() | new_nodes
        source_types = OperationSpecRegistry.allowed_types_for(update.operation, "source_node_id")
        target_types = OperationSpecRegistry.allowed_types_for(update.operation, "target_node_id")
        source = self._require_local_active(graph, self._resolve_ref(update.source_node_id), set(source_types), permitted)
        target = self._require_local_active(graph, self._resolve_ref(update.target_node_id), set(target_types), permitted)
        relation = EdgeRelation.SUPPORTS if isinstance(update, AddSupportCommand) else EdgeRelation.CONFLICTS
        graph.add_edge(GraphEdge(id=make_edge_id(source.id, relation, target.id), source_id=source.id, target_id=target.id, relation=relation, strength=update.strength, explanation=update.reason))

    def _apply_derivation(self, graph: CaseGraph, new_nodes: set[str], update: AddDerivationCommand) -> None:
        permitted = self._permitted_ids() | new_nodes
        proposition_types = OperationSpecRegistry.allowed_types_for(update.operation, "derived_proposition_id")
        source_types = OperationSpecRegistry.allowed_types_for(update.operation, "source_node_id")
        proposition = self._require_local_active(graph, self._resolve_ref(update.derived_proposition_id), set(proposition_types), permitted)
        source = self._require_local_active(graph, self._resolve_ref(update.source_node_id), set(source_types), permitted)
        graph.add_edge(GraphEdge(id=make_edge_id(proposition.id, EdgeRelation.DERIVED_FROM, source.id), source_id=proposition.id, target_id=source.id, relation=EdgeRelation.DERIVED_FROM, explanation=update.reason))

    def _apply_specialization(self, graph: CaseGraph, new_nodes: set[str], update: AddSpecializationCommand) -> None:
        permitted = self._permitted_ids() | new_nodes
        child_types = OperationSpecRegistry.allowed_types_for(update.operation, "child_hypothesis_id")
        parent_types = OperationSpecRegistry.allowed_types_for(update.operation, "parent_hypothesis_id")
        child = self._require_local_active(graph, self._resolve_ref(update.child_hypothesis_id), set(child_types), permitted)
        parent = self._require_local_active(graph, self._resolve_ref(update.parent_hypothesis_id), set(parent_types), permitted)
        graph.add_edge(GraphEdge(id=make_edge_id(child.id, EdgeRelation.SPECIALIZES, parent.id), source_id=child.id, target_id=parent.id, relation=EdgeRelation.SPECIALIZES, explanation=update.reason))

    def _require_local_active(self, graph: CaseGraph, identifier: str, types: set[GraphNodeType], permitted: set[str]) -> GraphNode:
        node = self._require_node(graph, identifier)
        if identifier not in permitted:
            raise ValueError("Investigator graph reference is outside the active local region")
        if node.status is not GraphStatus.ACTIVE or node.node_type not in types:
            raise ValueError("Investigator graph reference has an invalid type or status")
        return node

    @staticmethod
    def _require_new_id(graph: CaseGraph, identifier: str) -> None:
        if identifier in graph.nodes:
            raise ValueError(f"Duplicate graph node ID: {identifier!r}")

    def _allocate_node_id(self, graph: CaseGraph, node_type: GraphNodeType, node_id: str | None, local_ref: str | None, statement: str) -> str:
        if node_id is not None:
            self._require_new_id(graph, node_id)
            return node_id
        if local_ref in self._turn_refs:
            raise ValueError(f"Duplicate turn-local reference: {local_ref!r}")
        digest = hashlib.sha256(f"{graph.case_id}:{node_type.value}:{local_ref or ''}:{statement}".encode()).hexdigest()[:16]
        candidate = f"node_{digest}"
        suffix = 1
        while candidate in graph.nodes:
            candidate = f"node_{digest}{suffix:x}"
            suffix += 1
        return candidate

    def _remember_ref(self, local_ref: str | None, node_id: str) -> None:
        if local_ref is not None:
            self._turn_refs[local_ref] = node_id

    def _resolve_ref(self, identifier: str) -> str:
        return self._turn_refs.get(identifier, identifier)

    def review_with_steward(self, decision: StewardDecision, review_context: StewardReviewContext | None = None) -> None:
        if self.stopped:
            raise ValueError("Coordinator is stopped")
        if decision.operation != "stop_unresolved" and review_context is not None:
            raise ValueError("review_context is valid only for STOP_UNRESOLVED")
        if decision.operation == "keep_focus":
            pass
        elif isinstance(decision, ShiftFocusDecision):
            destination = self._require_node(self.graph, decision.destination_node_id)
            if destination.status is not GraphStatus.ACTIVE:
                raise ValueError("Steward focus destination must be active")
            self._move_focus(destination.id, decision.reason, None)
        elif isinstance(decision, GeneralizeDecision):
            target = self._require_node(self.graph, decision.target_node_id)
            if target.status is not GraphStatus.ACTIVE:
                raise ValueError("Steward generalization target must be active")
            self._move_focus(self.graph.generalize(target.id).id, decision.reason, None)
        elif isinstance(decision, ArchiveDecision):
            self._archive(decision)
        elif isinstance(decision, ReactivateDecision):
            self.graph.reactivate_node(decision.target_node_id, decision.reason)
        elif isinstance(decision, StopUnresolvedDecision):
            if review_context is None:
                raise ValueError("STOP_UNRESOLVED requires trusted review_context")
            self._stop_unresolved(decision, review_context)
        self.history.append(self.graph, self.focus, decision.reason)

    def _permitted_ids(self) -> set[str]:
        return self.legal_node_ids()

    def legal_node_ids(self) -> set[str]:
        """The single existing-node boundary shared by Investigator view and validators."""
        if self.full_graph_visibility:
            return set(self.graph.nodes)
        recent = set(self.focus.recent_node_ids) | set(self.focus.recent_region_node_ids)
        nearby = {node.id for identifier in recent if identifier in self.graph.nodes for node in self.graph.neighbors(identifier)}
        return {self.focus.node_id, *(node.id for node in self.graph.neighbors(self.focus.node_id)), *recent, *nearby, *self._new_nodes, *(node.id for node in self.graph.nodes.values() if node.node_type is GraphNodeType.SOURCE and node.status is GraphStatus.ACTIVE)}

    def active_reasoning_view(self) -> CaseGraph:
        """Return the exact graph workspace whose IDs are legal for Investigator references."""
        if self.full_graph_visibility:
            return deepcopy(self.graph)
        allowed = self.legal_node_ids()
        return self.graph.model_copy(update={
            "nodes": {identifier: self.graph.nodes[identifier] for identifier in allowed},
            "edges": {identifier: edge for identifier, edge in self.graph.edges.items() if edge.source_id in allowed and edge.target_id in allowed},
        })

    def _move_focus(self, node_id: str, reason: str, permitted: set[str] | None) -> None:
        node = self._require_node(self.graph, node_id)
        if node.status is not GraphStatus.ACTIVE:
            raise ValueError("Investigator focus must be active")
        if permitted is not None and node_id not in permitted:
            raise ValueError("Investigator focus destination is outside the active local region")
        region = investigator_region(self.graph, self.focus.model_copy(update={"node_id": node_id}), depth=1)
        self.focus = self.focus.moved_to(node_id, sorted(region.nodes), reason)

    def _archive(self, decision: ArchiveDecision) -> None:
        target = self._require_node(self.graph, decision.target_node_id)
        if target.id == self.focus.node_id:
            if decision.destination_node_id is None:
                raise ValueError("Archiving current focus requires destination_node_id")
            destination = self._require_node(self.graph, decision.destination_node_id)
            if destination.id == target.id or destination.status is not GraphStatus.ACTIVE:
                raise ValueError("Archive destination must be a different active node")
            self.graph.archive_node(target.id, decision.reason)
            self._move_focus(destination.id, decision.reason, None)
        else:
            self.graph.archive_node(target.id, decision.reason)

    def _stop_unresolved(self, decision: StopUnresolvedDecision, context: StewardReviewContext) -> None:
        if not context.global_frontier_assessed:
            raise ValueError("STOP_UNRESOLVED requires an assessed global frontier")
        if context.neglected_candidate_node_ids or context.obvious_useful_region_remains:
            raise ValueError("STOP_UNRESOLVED is blocked by a remaining useful graph region")
        if context.materially_usable_action_ids:
            raise ValueError("STOP_UNRESOLVED is blocked by materially usable actions")
        if context.local_exhaustion_required and not context.local_frontier_exhausted:
            raise ValueError("STOP_UNRESOLVED requires an exhausted local frontier")
        for identifier in decision.important_unresolved_ids:
            node = self._require_node(self.graph, identifier)
            if node.node_type is not GraphNodeType.UNCERTAINTY or node.status is not GraphStatus.ACTIVE:
                raise ValueError("STOP_UNRESOLVED IDs must identify active uncertainty nodes")
            if identifier not in context.active_unresolved_ids:
                raise ValueError("STOP_UNRESOLVED IDs must be listed by trusted active_unresolved_ids")
        self.stopped = True

    @staticmethod
    def _require_node(graph: CaseGraph, node_id: str) -> GraphNode:
        try:
            return graph.get_node(node_id)
        except KeyError as exc:
            raise ValueError(f"Unknown graph node ID: {node_id!r}") from exc
