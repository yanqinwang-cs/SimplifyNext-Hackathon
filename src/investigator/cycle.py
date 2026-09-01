from copy import deepcopy
from enum import Enum
from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator

from investigator.graph import CaseGraph, GraphNodeType, GraphStatus
from investigator.roles.coordinator import GraphInvestigationCoordinator
from investigator.roles.focus import InvestigationFocus, investigator_region
from investigator.roles.investigator import INVESTIGATOR_UPDATE_ADAPTER, InvestigatorUpdate
from investigator.roles.steward import StewardDecision, StopUnresolvedDecision, StewardReviewContext


class EnquiryKind(str, Enum):
    OBTAIN = "obtain"
    REVIEW = "review"
    VERIFY = "verify"
    CLARIFY = "clarify"
    COMPUTE = "compute"


class AvailableEnquiry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action_id: str = Field(pattern=r"^A\d+$")
    kind: EnquiryKind
    description: str
    addressable_uncertainty_ids: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_targets(self) -> "AvailableEnquiry":
        if len(self.addressable_uncertainty_ids) != len(set(self.addressable_uncertainty_ids)):
            raise ValueError("addressable_uncertainty_ids must not contain duplicates")
        return self


class _ReasonedStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str

    @field_validator("reason")
    @classmethod
    def substantive_reason(cls, value: str) -> str:
        if not value.strip() or "REPLACE_WITH_" in value or "{{" in value:
            raise ValueError("reason must be substantive and must not be a template placeholder")
        return value


class ContinueLocal(_ReasonedStep):
    type: Literal["continue_local"] = "continue_local"


class RequestEnquiry(_ReasonedStep):
    type: Literal["request_enquiry"] = "request_enquiry"
    action_id: str = Field(pattern=r"^A\d+$")
    target_uncertainty_id: str
    expected_information_value: str

    @field_validator("expected_information_value")
    @classmethod
    def substantive_information_value(cls, value: str) -> str:
        if not value.strip() or "REPLACE_WITH_" in value or "{{" in value:
            raise ValueError("expected_information_value must be substantive and must not be a template placeholder")
        return value


class RequestStewardReview(_ReasonedStep):
    type: Literal["request_steward_review"] = "request_steward_review"


class LocalExhausted(_ReasonedStep):
    type: Literal["local_exhausted"] = "local_exhausted"


InvestigatorNextStep: TypeAlias = Annotated[
    ContinueLocal | RequestEnquiry | RequestStewardReview | LocalExhausted,
    Field(discriminator="type"),
]
NEXT_STEP_ADAPTER = TypeAdapter(InvestigatorNextStep)


class InvestigatorTurnResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    graph_updates: list[InvestigatorUpdate] = Field(max_length=5)
    next_step: InvestigatorNextStep

    @model_validator(mode="after")
    def continue_requires_graph_work(self) -> "InvestigatorTurnResponse":
        if isinstance(self.next_step, ContinueLocal) and not self.graph_updates:
            raise ValueError("continue_local requires at least one graph update")
        return self


TURN_RESPONSE_ADAPTER = TypeAdapter(InvestigatorTurnResponse)


class InvestigatorObservation(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")
    current_focus: InvestigationFocus
    local_graph: CaseGraph
    available_enquiries: list[AvailableEnquiry] = Field(default_factory=list)
    participants: list[dict[str, object]] = Field(default_factory=list)
    tenure_turn_count: int = Field(ge=0)
    max_turns_per_tenure: int = Field(gt=0)
    turns_remaining: int = Field(ge=0)
    in_flight_enquiry: "InFlightEnquiry | None" = None
    recently_released_evidence_ids: list[str] = Field(default_factory=list)


class CycleStatus(str, Enum):
    LOCAL_ACTIVE = "local_active"
    ENQUIRY_IN_FLIGHT = "enquiry_in_flight"
    AWAITING_STEWARD = "awaiting_steward"
    STOPPED = "stopped"


class InFlightEnquiry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action_id: str
    kind: EnquiryKind
    target_uncertainty_id: str
    focus_node_id: str
    requested_at_revision: int = Field(ge=0)


class EnquiryCompletion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action_id: str
    released_evidence_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_releases(self) -> "EnquiryCompletion":
        if len(self.released_evidence_ids) != len(set(self.released_evidence_ids)):
            raise ValueError("released_evidence_ids must not contain duplicates")
        return self


class InvestigatorCycleState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: CycleStatus = CycleStatus.LOCAL_ACTIVE
    tenure_turn_count: int = Field(default=0, ge=0)
    max_turns_per_tenure: int = Field(default=6, gt=0)
    in_flight_enquiry: InFlightEnquiry | None = None
    steward_review_required_after_enquiry: bool = False
    handoff_reason: str | None = None
    case_revision: int = Field(default=0, ge=0)
    recently_released_evidence_ids: list[str] = Field(default_factory=list)


class StewardSnapshot(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")
    case_revision: int = Field(ge=0)
    graph: CaseGraph
    focus: InvestigationFocus
    cycle: InvestigatorCycleState
    participants: list[dict[str, object]] = Field(default_factory=list)
    review_context: object | None = None


class CycleFailureCode(str, Enum):
    TURN_SCHEMA_FAILURE = "TURN_SCHEMA_FAILURE"
    TOO_MANY_GRAPH_UPDATES = "TOO_MANY_GRAPH_UPDATES"
    GRAPH_UPDATE_FAILURE = "GRAPH_UPDATE_FAILURE"
    TURN_ATOMIC_ROLLBACK = "TURN_ATOMIC_ROLLBACK"
    INVALID_AVAILABLE_ACTION = "INVALID_AVAILABLE_ACTION"
    INVALID_ENQUIRY_TARGET = "INVALID_ENQUIRY_TARGET"
    ENQUIRY_ALREADY_IN_FLIGHT = "ENQUIRY_ALREADY_IN_FLIGHT"
    INVESTIGATOR_NOT_ACTIVE = "INVESTIGATOR_NOT_ACTIVE"
    STEWARD_HANDOFF_PENDING = "STEWARD_HANDOFF_PENDING"
    STALE_STEWARD_REVISION = "STALE_STEWARD_REVISION"
    STEWARD_WRITE_DURING_IN_FLIGHT = "STEWARD_WRITE_DURING_IN_FLIGHT"
    INVALID_ENQUIRY_COMPLETION = "INVALID_ENQUIRY_COMPLETION"


class CycleError(ValueError):
    def __init__(self, code: CycleFailureCode, message: str) -> None:
        self.code = code
        super().__init__(message)


class InvestigatorCycleCoordinator:
    """Deterministic single-writer coordinator for bounded Investigator tenures."""

    def __init__(self, graph: CaseGraph, focus: InvestigationFocus, available_enquiries: list[AvailableEnquiry] | None = None, participants: list[dict[str, object]] | None = None, max_turns_per_tenure: int = 6, review_context: StewardReviewContext | None = None) -> None:
        if max_turns_per_tenure < 1:
            raise ValueError("max_turns_per_tenure must be positive")
        if focus.node_id not in graph.nodes:
            raise ValueError(f"Unknown graph node ID: {focus.node_id!r}")
        self.graph = graph
        self.focus = focus
        self.available_enquiries = [AvailableEnquiry.model_validate(item) for item in (available_enquiries or [])]
        self.participants = deepcopy(participants or [])
        self.review_context = review_context
        self.cycle = InvestigatorCycleState(max_turns_per_tenure=max_turns_per_tenure)
        self._new_nodes: set[str] = set()

    def observation(self) -> InvestigatorObservation:
        region = investigator_region(self.graph, self.focus, depth=1)
        return InvestigatorObservation(current_focus=self.focus.model_copy(deep=True), local_graph=deepcopy(region), available_enquiries=deepcopy(self.available_enquiries), participants=deepcopy(self.participants), tenure_turn_count=self.cycle.tenure_turn_count, max_turns_per_tenure=self.cycle.max_turns_per_tenure, turns_remaining=max(0, self.cycle.max_turns_per_tenure - self.cycle.tenure_turn_count), in_flight_enquiry=deepcopy(self.cycle.in_flight_enquiry), recently_released_evidence_ids=list(self.cycle.recently_released_evidence_ids))

    def apply_turn(self, response: InvestigatorTurnResponse | dict) -> InvestigatorCycleState:
        self._require_investigator_active()
        if isinstance(response, dict) and isinstance(response.get("graph_updates"), list) and len(response["graph_updates"]) > 5:
            raise CycleError(CycleFailureCode.TOO_MANY_GRAPH_UPDATES, "An Investigator turn may contain at most five graph updates")
        try:
            response = TURN_RESPONSE_ADAPTER.validate_python(response)
        except Exception as exc:
            raise CycleError(CycleFailureCode.TURN_SCHEMA_FAILURE, f"Invalid InvestigatorTurnResponse: {exc}") from exc
        if len(response.graph_updates) > 5:
            raise CycleError(CycleFailureCode.TOO_MANY_GRAPH_UPDATES, "An Investigator turn may contain at most five graph updates")
        working = GraphInvestigationCoordinator(deepcopy(self.graph), self.focus.model_copy(deep=True))
        working._new_nodes = set(self._new_nodes)
        try:
            for update in response.graph_updates:
                working.apply_investigator_update(update)
        except Exception as exc:
            raise CycleError(CycleFailureCode.TURN_ATOMIC_ROLLBACK, f"Investigator turn rolled back after graph update failure: {exc}") from exc
        next_step = response.next_step
        if isinstance(next_step, RequestEnquiry):
            self._validate_enquiry_request(next_step, working)
        self.graph, self.focus, self._new_nodes = working.graph, working.focus, working._new_nodes
        self.cycle.tenure_turn_count += 1
        self.cycle.case_revision += 1
        if isinstance(next_step, ContinueLocal):
            if self.cycle.tenure_turn_count >= self.cycle.max_turns_per_tenure:
                self.cycle.status = CycleStatus.AWAITING_STEWARD
                self.cycle.handoff_reason = "TENURE_BUDGET"
            else:
                self.cycle.status = CycleStatus.LOCAL_ACTIVE
                self.cycle.handoff_reason = None
        elif isinstance(next_step, RequestEnquiry):
            action = self._action(next_step.action_id)
            self.cycle.in_flight_enquiry = InFlightEnquiry(action_id=action.action_id, kind=action.kind, target_uncertainty_id=next_step.target_uncertainty_id, focus_node_id=self.focus.node_id, requested_at_revision=self.cycle.case_revision)
            self.cycle.steward_review_required_after_enquiry = self.cycle.tenure_turn_count >= self.cycle.max_turns_per_tenure
            self.cycle.status = CycleStatus.ENQUIRY_IN_FLIGHT
            self.cycle.handoff_reason = None
        elif isinstance(next_step, RequestStewardReview):
            self.cycle.status = CycleStatus.AWAITING_STEWARD
            self.cycle.handoff_reason = "REQUESTED_REVIEW"
        else:
            self.cycle.status = CycleStatus.AWAITING_STEWARD
            self.cycle.handoff_reason = "LOCAL_EXHAUSTED"
        return self.cycle.model_copy(deep=True)

    def complete_enquiry(self, completion: EnquiryCompletion | dict) -> InvestigatorCycleState:
        if self.cycle.status is not CycleStatus.ENQUIRY_IN_FLIGHT or self.cycle.in_flight_enquiry is None:
            raise CycleError(CycleFailureCode.INVALID_ENQUIRY_COMPLETION, "No enquiry is currently in flight")
        try:
            completion = EnquiryCompletion.model_validate(completion)
        except Exception as exc:
            raise CycleError(CycleFailureCode.INVALID_ENQUIRY_COMPLETION, f"Invalid enquiry completion: {exc}") from exc
        if completion.action_id != self.cycle.in_flight_enquiry.action_id:
            raise CycleError(CycleFailureCode.INVALID_ENQUIRY_COMPLETION, "Enquiry completion action_id does not match the in-flight action")
        for identifier in completion.released_evidence_ids:
            node = self.graph.nodes.get(identifier)
            if node is None or node.node_type is not GraphNodeType.EVIDENCE:
                raise CycleError(CycleFailureCode.INVALID_ENQUIRY_COMPLETION, "Released evidence IDs must identify existing evidence nodes")
        self.cycle.recently_released_evidence_ids = list(completion.released_evidence_ids)
        self.cycle.in_flight_enquiry = None
        self.cycle.case_revision += 1
        if self.cycle.steward_review_required_after_enquiry:
            self.cycle.status = CycleStatus.AWAITING_STEWARD
            self.cycle.handoff_reason = "TENURE_BUDGET"
        else:
            self.cycle.status = CycleStatus.LOCAL_ACTIVE
            self.cycle.handoff_reason = None
        self.cycle.steward_review_required_after_enquiry = False
        return self.cycle.model_copy(deep=True)

    def steward_snapshot(self) -> StewardSnapshot:
        return StewardSnapshot(case_revision=self.cycle.case_revision, graph=deepcopy(self.graph), focus=self.focus.model_copy(deep=True), cycle=self.cycle.model_copy(deep=True), participants=deepcopy(self.participants), review_context=deepcopy(self.review_context))

    def apply_steward_decision(self, decision: StewardDecision | dict, based_on_revision: int, review_context: StewardReviewContext | None = None) -> InvestigatorCycleState:
        if self.cycle.status is CycleStatus.ENQUIRY_IN_FLIGHT:
            raise CycleError(CycleFailureCode.STEWARD_WRITE_DURING_IN_FLIGHT, "Steward graph writes are forbidden while an enquiry is in flight")
        if self.cycle.status is not CycleStatus.AWAITING_STEWARD:
            raise CycleError(CycleFailureCode.STEWARD_HANDOFF_PENDING, "Steward decision requires AWAITING_STEWARD")
        if based_on_revision != self.cycle.case_revision:
            raise CycleError(CycleFailureCode.STALE_STEWARD_REVISION, "Steward decision was based on a stale case revision")
        try:
            parsed = TypeAdapter(StewardDecision).validate_python(decision)
            working = GraphInvestigationCoordinator(deepcopy(self.graph), self.focus.model_copy(deep=True))
            working.review_with_steward(parsed, review_context=review_context or self.review_context)
        except CycleError:
            raise
        except Exception as exc:
            raise CycleError(CycleFailureCode.GRAPH_UPDATE_FAILURE, f"Steward decision was rejected: {exc}") from exc
        self.graph, self.focus = working.graph, working.focus
        self.cycle.case_revision += 1
        if isinstance(parsed, StopUnresolvedDecision):
            self.cycle.status = CycleStatus.STOPPED
        else:
            self.cycle.status = CycleStatus.LOCAL_ACTIVE
            self.cycle.tenure_turn_count = 0
            self._new_nodes = set()
        self.cycle.handoff_reason = None
        return self.cycle.model_copy(deep=True)

    def _validate_enquiry_request(self, request: RequestEnquiry, working: GraphInvestigationCoordinator) -> None:
        if self.cycle.in_flight_enquiry is not None:
            raise CycleError(CycleFailureCode.ENQUIRY_ALREADY_IN_FLIGHT, "An enquiry is already in flight")
        action = self._action(request.action_id)
        if request.target_uncertainty_id not in action.addressable_uncertainty_ids:
            raise CycleError(CycleFailureCode.INVALID_ENQUIRY_TARGET, "Target uncertainty is not addressable by the selected action")
        node = working.graph.nodes.get(request.target_uncertainty_id)
        permitted = working._permitted_ids() | working._new_nodes
        if node is None or node.node_type is not GraphNodeType.UNCERTAINTY or node.status is not GraphStatus.ACTIVE or node.id not in permitted:
            raise CycleError(CycleFailureCode.INVALID_ENQUIRY_TARGET, "Target must be an active local uncertainty after this turn's updates")

    def _action(self, action_id: str) -> AvailableEnquiry:
        for action in self.available_enquiries:
            if action.action_id == action_id:
                return action
        raise CycleError(CycleFailureCode.INVALID_AVAILABLE_ACTION, f"Action is not currently available: {action_id!r}")

    def _require_investigator_active(self) -> None:
        if self.cycle.status is CycleStatus.ENQUIRY_IN_FLIGHT:
            raise CycleError(CycleFailureCode.ENQUIRY_ALREADY_IN_FLIGHT, "No Investigator turn may start while an enquiry is in flight")
        if self.cycle.status is CycleStatus.AWAITING_STEWARD:
            raise CycleError(CycleFailureCode.STEWARD_HANDOFF_PENDING, "No Investigator turn may start while Steward review is pending")
        if self.cycle.status is CycleStatus.STOPPED:
            raise CycleError(CycleFailureCode.INVESTIGATOR_NOT_ACTIVE, "The Investigator cycle is stopped")
