from investigator.graph import CaseGraph, GraphNodeType, GraphStatus
from investigator.roles.focus import InvestigationFocus, investigator_region
from investigator.roles.history import GraphHistory
from investigator.roles.investigator import InvestigatorOperation, InvestigatorUpdate
from investigator.roles.steward import ArchiveDecision, GeneralizeDecision, ReactivateDecision, ShiftFocusDecision, StopUnresolvedDecision, StewardDecision, StewardReviewContext


class GraphInvestigationCoordinator:
    """Offline sequential coordinator enforcing Investigator and Steward boundaries."""

    def __init__(self, graph: CaseGraph, focus: InvestigationFocus) -> None:
        self._require_node(graph, focus.node_id)
        self.graph, self.focus = graph, focus
        self._new_nodes: set[str] = set()
        self.history = GraphHistory()
        self.history.append(graph, focus, "initial graph")
        self.stopped = False

    def apply_investigator_update(self, update: InvestigatorUpdate) -> None:
        if self.stopped:
            raise ValueError("Coordinator is stopped")
        if update.operation in {InvestigatorOperation.ADD_PROPOSITION, InvestigatorOperation.ADD_HYPOTHESIS, InvestigatorOperation.ADD_UNCERTAINTY}:
            self._apply_local_node(update)
        elif update.operation in {InvestigatorOperation.ADD_SUPPORT, InvestigatorOperation.ADD_CONFLICT, InvestigatorOperation.ADD_DERIVATION, InvestigatorOperation.ADD_SPECIALIZATION}:
            self._apply_local_edge(update)
        else:
            self._move_focus(update.focus_node_id, update.reason, self._permitted_ids())
        self.history.append(self.graph, self.focus, update.reason)

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

    def _apply_local_node(self, update: InvestigatorUpdate) -> None:
        if update.node is None or update.node.node_type is GraphNodeType.EVIDENCE:
            raise ValueError("Investigator cannot create evidence")
        self.graph.add_node(update.node)
        self._new_nodes.add(update.node.id)

    def _apply_local_edge(self, update: InvestigatorUpdate) -> None:
        edge = update.edge
        permitted = self._permitted_ids()
        if edge is None or not ({edge.source_id, edge.target_id} & permitted) or not ({edge.source_id, edge.target_id} - permitted - self._new_nodes) == set():
            raise ValueError("Investigator edge endpoints must be in the active focus region")
        self.graph.add_edge(edge)
        self._new_nodes.difference_update({edge.source_id, edge.target_id})

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

    def _permitted_ids(self) -> set[str]:
        return {self.focus.node_id, *(node.id for node in self.graph.neighbors(self.focus.node_id))}

    def _move_focus(self, node_id: str, reason: str, permitted: set[str] | None) -> None:
        node = self._require_node(self.graph, node_id)
        if node.status is not GraphStatus.ACTIVE:
            raise ValueError("Investigator focus must be active")
        if permitted is not None and node_id not in permitted:
            raise ValueError("Investigator focus destination is outside the active local region")
        region = investigator_region(self.graph, self.focus.model_copy(update={"node_id": node_id}), depth=1)
        self.focus = self.focus.moved_to(node_id, sorted(region.nodes), reason)

    @staticmethod
    def _require_node(graph: CaseGraph, node_id: str):
        try:
            return graph.get_node(node_id)
        except KeyError as exc:
            raise ValueError(f"Unknown graph node ID: {node_id!r}") from exc
