from investigator.graph import CaseGraph, GraphStatus
from investigator.roles.focus import InvestigationFocus, investigator_region
from investigator.roles.history import GraphHistory
from investigator.roles.investigator import InvestigatorOperation, InvestigatorUpdate
from investigator.roles.steward import StewardDecision, StewardOperation


class GraphInvestigationCoordinator:
    """Offline sequential coordinator proving investigator/steward jurisdiction."""

    def __init__(self, graph: CaseGraph, focus: InvestigationFocus) -> None:
        self._require_node(graph, focus.node_id)
        self.graph = graph
        self.focus = focus
        self.history = GraphHistory()
        self.history.append(graph, focus, "initial graph")
        self.stopped = False

    def apply_investigator_update(self, update: InvestigatorUpdate) -> None:
        if self.stopped:
            raise ValueError("Coordinator is stopped")
        if update.operation is InvestigatorOperation.ADD_NODE:
            self.graph.add_node(update.node)
        elif update.operation is InvestigatorOperation.ADD_EDGE:
            self.graph.add_edge(update.edge)
        else:
            self._move_focus(update.focus_node_id, update.reason)
        self.history.append(self.graph, self.focus, update.reason)

    def review_with_steward(self, decision: StewardDecision) -> None:
        if decision.operation is StewardOperation.KEEP_FOCUS:
            pass
        elif decision.operation is StewardOperation.SHIFT_FOCUS:
            destination = self._require_node(self.graph, decision.destination_node_id)
            if destination.status is not GraphStatus.ACTIVE:
                raise ValueError("Steward focus destination must be active")
            self._move_focus(destination.id, decision.reason)
        elif decision.operation is StewardOperation.GENERALIZE:
            if self._require_node(self.graph, decision.target_node_id).status is not GraphStatus.ACTIVE:
                raise ValueError("Steward generalization target must be active")
            ancestor = self.graph.generalize(decision.target_node_id)
            self._move_focus(ancestor.id, decision.reason)
        elif decision.operation is StewardOperation.ARCHIVE:
            self.graph.archive_node(decision.target_node_id, decision.reason)
        elif decision.operation is StewardOperation.REACTIVATE:
            self.graph.reactivate_node(decision.target_node_id, decision.reason)
        elif decision.operation is StewardOperation.STOP_UNRESOLVED:
            for identifier in decision.important_unresolved_ids:
                if self._require_node(self.graph, identifier).node_type.value != "uncertainty":
                    raise ValueError("STOP_UNRESOLVED IDs must identify uncertainty nodes")
            self.stopped = True
        self.history.append(self.graph, self.focus, decision.reason)

    def _move_focus(self, node_id: str, reason: str) -> None:
        self._require_node(self.graph, node_id)
        region = investigator_region(self.graph, self.focus.model_copy(update={"node_id": node_id}), depth=1)
        self.focus = self.focus.moved_to(node_id, sorted(region.nodes), reason)

    @staticmethod
    def _require_node(graph: CaseGraph, node_id: str):
        try:
            return graph.get_node(node_id)
        except KeyError as exc:
            raise ValueError(f"Unknown graph node ID: {node_id!r}") from exc
