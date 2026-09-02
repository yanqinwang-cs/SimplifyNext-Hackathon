from __future__ import annotations

from copy import deepcopy
from enum import Enum
import hashlib
import json
from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator

from investigator.graph import CaseGraph, GraphNodeType, GraphStatus
from investigator.models.evidence_request import EvidenceRequest, EvidenceRequestResponse, EvidenceRequestStatus
from investigator.models.source import Source
from investigator.roles.coordinator import GraphInvestigationCoordinator
from investigator.roles.focus import InvestigationFocus, investigator_region
from investigator.roles.investigator import INVESTIGATOR_UPDATE_ADAPTER, InvestigatorUpdate
from investigator.roles.investigator import InvestigatorOperation
from investigator.roles.procedure import INVESTIGATOR_PROCEDURE, STEWARD_PROCEDURE, procedural_contract_errors
from investigator.roles.steward import StewardDecision, StopUnresolvedDecision, StewardReviewContext, StewardRequestEvidenceDecision, StewardRequestOpenDecision


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


class RequestEvidence(_ReasonedStep):
    type: Literal["request_evidence"] = "request_evidence"
    target_uncertainty_id: str
    information_sought: str
    expected_information_value: str

    @field_validator("information_sought", "expected_information_value")
    @classmethod
    def substantive_request_text(cls, value: str) -> str:
        if not value.strip() or "REPLACE_WITH_" in value or "{{" in value:
            raise ValueError("request text must be substantive and must not be a template placeholder")
        return value


class RequestOpen(_ReasonedStep):
    type: Literal["request_open"] = "request_open"
    information_sought: str | None = None
    expected_information_value: str | None = None

    @field_validator("information_sought", "expected_information_value")
    @classmethod
    def substantive_open_request(cls, value: str | None) -> str | None:
        if value is not None and (not value.strip() or value.strip().lower() in {"more information", "additional information", "anything else"}):
            raise ValueError("request_open must state a concrete information need and expected value")
        return value


class RequestStewardReview(_ReasonedStep):
    type: Literal["request_steward_review"] = "request_steward_review"


class LocalExhausted(_ReasonedStep):
    type: Literal["local_exhausted"] = "local_exhausted"


InvestigatorNextStep: TypeAlias = Annotated[
    ContinueLocal | RequestEnquiry | RequestEvidence | RequestOpen | RequestStewardReview | LocalExhausted,
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
    case_revision: int = Field(default=0, ge=0)
    local_graph: CaseGraph
    available_enquiries: list[AvailableEnquiry] = Field(default_factory=list)
    participants: list[dict[str, object]] = Field(default_factory=list)
    tenure_turn_count: int = Field(ge=0)
    max_turns_per_tenure: int = Field(gt=0)
    turns_remaining: int = Field(ge=0)
    in_flight_enquiry: "InFlightEnquiry | None" = None
    recently_released_evidence_ids: list[str] = Field(default_factory=list)
    workflow_feedback: str | None = None
    # Raw source records are an explicit visibility boundary, separate from
    # the semantic graph.  Callers may leave this empty when no source store
    # is attached (for example, generic cycle tests).
    visible_sources: list[Source] = Field(default_factory=list)


class TurnSnapshot(BaseModel):
    """Immutable-by-convention baseline for one Investigator or Steward turn."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")
    case_revision: int = Field(ge=0)
    repository_revision: int | None = Field(default=None, ge=0)
    graph: CaseGraph
    focus: InvestigationFocus
    cycle: InvestigatorCycleState | None = None
    visible_sources: list[Source] = Field(default_factory=list)
    visible_source_signatures: dict[str, str] = Field(default_factory=dict)
    active_reasoning_node_ids: list[str] = Field(default_factory=list)
    new_node_ids: list[str] = Field(default_factory=list)

    @classmethod
    def from_coordinator(cls, coordinator: "InvestigatorCycleCoordinator", visible_sources: list[Source] | None = None, repository_revision: int | None = None) -> "TurnSnapshot":
        graph = deepcopy(coordinator.graph)
        sources = deepcopy(visible_sources or [])
        signatures = {
            source.id: hashlib.sha256(json.dumps(source.model_dump(mode="json"), sort_keys=True).encode()).hexdigest()
            for source in sources
        }
        return cls(
            case_revision=coordinator.cycle.case_revision,
            repository_revision=repository_revision,
            graph=graph,
            focus=coordinator.focus.model_copy(deep=True),
            cycle=coordinator.cycle.model_copy(deep=True),
            visible_sources=sources,
            visible_source_signatures=signatures,
            active_reasoning_node_ids=sorted(coordinator.legal_node_ids()),
            new_node_ids=sorted(coordinator._new_nodes),
        )

    def observation(self, available_enquiries: list[AvailableEnquiry], participants: list[dict[str, object]], max_turns_per_tenure: int) -> InvestigatorObservation:
        cycle = self.cycle
        assert cycle is not None
        return InvestigatorObservation(
            current_focus=self.focus.model_copy(deep=True),
            case_revision=self.case_revision,
            local_graph=deepcopy(self.graph.model_copy(update={"nodes": {key: self.graph.nodes[key] for key in self.active_reasoning_node_ids}, "edges": {key: edge for key, edge in self.graph.edges.items() if edge.source_id in self.active_reasoning_node_ids and edge.target_id in self.active_reasoning_node_ids}})),
            available_enquiries=deepcopy(available_enquiries),
            participants=deepcopy(participants),
            tenure_turn_count=cycle.tenure_turn_count,
            max_turns_per_tenure=max_turns_per_tenure,
            turns_remaining=max(0, max_turns_per_tenure - cycle.tenure_turn_count),
            in_flight_enquiry=deepcopy(cycle.in_flight_enquiry),
            recently_released_evidence_ids=list(cycle.recently_released_evidence_ids),
            workflow_feedback=cycle.workflow_feedback,
            visible_sources=deepcopy(self.visible_sources),
        )


class CycleStatus(str, Enum):
    LOCAL_ACTIVE = "local_active"
    ENQUIRY_IN_FLIGHT = "enquiry_in_flight"
    WAITING_FOR_EVIDENCE = "waiting_for_evidence"
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
    evidence_request: EvidenceRequest | None = None
    evidence_request_history: list[EvidenceRequest] = Field(default_factory=list)
    steward_review_required_after_enquiry: bool = False
    handoff_reason: str | None = None
    case_revision: int = Field(default=0, ge=0)
    recently_released_evidence_ids: list[str] = Field(default_factory=list)
    workflow_feedback: str | None = None


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
    EVIDENCE_REQUEST_ALREADY_PENDING = "EVIDENCE_REQUEST_ALREADY_PENDING"
    INVALID_EVIDENCE_REQUEST = "INVALID_EVIDENCE_REQUEST"


class CycleError(ValueError):
    def __init__(self, code: CycleFailureCode, message: str) -> None:
        self.code = code
        super().__init__(message)


class InvestigatorCycleCoordinator:
    """Deterministic single-writer coordinator for bounded Investigator tenures."""

    def __init__(self, graph: CaseGraph, focus: InvestigationFocus, available_enquiries: list[AvailableEnquiry] | None = None, participants: list[dict[str, object]] | None = None, max_turns_per_tenure: int = 6, review_context: StewardReviewContext | None = None, case_revision: int = 0) -> None:
        if max_turns_per_tenure < 1:
            raise ValueError("max_turns_per_tenure must be positive")
        if focus.node_id not in graph.nodes:
            raise ValueError(f"Unknown graph node ID: {focus.node_id!r}")
        if case_revision < 0:
            raise ValueError("case_revision must be non-negative")
        self.graph = graph
        self.focus = focus
        self.available_enquiries = [AvailableEnquiry.model_validate(item) for item in (available_enquiries or [])]
        self.participants = deepcopy(participants or [])
        self.review_context = review_context
        self.cycle = InvestigatorCycleState(max_turns_per_tenure=max_turns_per_tenure, case_revision=case_revision)
        self._new_nodes: set[str] = set()

    def turn_snapshot(self, visible_sources: list[Source] | None = None, repository_revision: int | None = None) -> TurnSnapshot:
        return TurnSnapshot.from_coordinator(self, visible_sources, repository_revision)

    def observation(self, visible_sources: list[Source] | None = None, snapshot: TurnSnapshot | None = None) -> InvestigatorObservation:
        # The coordinator's active view is also the sole legal existing-node set.
        if snapshot is not None:
            return snapshot.observation(self.available_enquiries, self.participants, self.cycle.max_turns_per_tenure)
        region = self._view_for(self.graph, self.focus, self._new_nodes)
        return InvestigatorObservation(current_focus=self.focus.model_copy(deep=True), case_revision=self.cycle.case_revision, local_graph=deepcopy(region), available_enquiries=deepcopy(self.available_enquiries), participants=deepcopy(self.participants), tenure_turn_count=self.cycle.tenure_turn_count, max_turns_per_tenure=self.cycle.max_turns_per_tenure, turns_remaining=max(0, self.cycle.max_turns_per_tenure - self.cycle.tenure_turn_count), in_flight_enquiry=deepcopy(self.cycle.in_flight_enquiry), recently_released_evidence_ids=list(self.cycle.recently_released_evidence_ids), workflow_feedback=self.cycle.workflow_feedback, visible_sources=deepcopy(visible_sources or []))

    def contract_check(self, observation: InvestigatorObservation, rendered_prompt: str, snapshot: TurnSnapshot | None = None) -> dict[str, object]:
        """Gate the exposed environment before a model call; never mutates state."""
        procedure_errors = procedural_contract_errors()
        schema_operations = {item["const"] for item in TURN_RESPONSE_ADAPTER.json_schema().get("$defs", {}).values() if isinstance(item, dict) for item in item.get("properties", {}).values() if isinstance(item, dict) and "const" in item}
        schema_operations |= {item.value for item in InvestigatorOperation}
        documented = {item.operation for item in INVESTIGATOR_PROCEDURE}
        if procedure_errors or not schema_operations <= documented or not {item.operation for item in STEWARD_PROCEDURE}:
            raise CycleError(CycleFailureCode.TURN_SCHEMA_FAILURE, "CONTRACT CHECK failed: role procedural contract is incomplete or contradictory")
        if "<INVESTIGATOR_PROCEDURE>" not in rendered_prompt or any(operation not in rendered_prompt for operation in schema_operations):
            raise CycleError(CycleFailureCode.TURN_SCHEMA_FAILURE, "CONTRACT CHECK failed: prompt does not expose the canonical Investigator procedure")
        if observation.case_revision != self.cycle.case_revision:
            raise CycleError(CycleFailureCode.TURN_SCHEMA_FAILURE, "CONTRACT CHECK failed: observation revision does not match current case revision")
        legal = self.legal_node_ids()
        exposed = set(observation.local_graph.nodes)
        if exposed != legal or observation.current_focus.node_id not in exposed:
            raise CycleError(CycleFailureCode.TURN_SCHEMA_FAILURE, "CONTRACT CHECK failed: visible graph and legal local graph differ")
        source_ids = [source.id for source in observation.visible_sources]
        if len(source_ids) != len(set(source_ids)) or any(not source.content or source.content not in rendered_prompt for source in observation.visible_sources):
            raise CycleError(CycleFailureCode.TURN_SCHEMA_FAILURE, "CONTRACT CHECK failed: visible source registry/body boundary is inconsistent")
        if snapshot is not None:
            if snapshot.case_revision != observation.case_revision or snapshot.focus.node_id != observation.current_focus.node_id:
                raise CycleError(CycleFailureCode.TURN_SCHEMA_FAILURE, "CONTRACT CHECK failed: snapshot and observation identity differ")
            if set(snapshot.active_reasoning_node_ids) != exposed or set(snapshot.active_reasoning_node_ids) != legal:
                raise CycleError(CycleFailureCode.TURN_SCHEMA_FAILURE, "CONTRACT CHECK failed: snapshot, observation, and legal graph IDs differ")
            if set(source_ids) != {source.id for source in snapshot.visible_sources}:
                raise CycleError(CycleFailureCode.TURN_SCHEMA_FAILURE, "CONTRACT CHECK failed: snapshot and observation source IDs differ")
            if snapshot.cycle is not None and snapshot.cycle.evidence_request != self.cycle.evidence_request:
                raise CycleError(CycleFailureCode.TURN_SCHEMA_FAILURE, "CONTRACT CHECK failed: snapshot and canonical pending request differ")
        if self.cycle.status in {CycleStatus.WAITING_FOR_EVIDENCE, CycleStatus.ENQUIRY_IN_FLIGHT}:
            raise CycleError(CycleFailureCode.TURN_SCHEMA_FAILURE, "CONTRACT CHECK failed: Investigator cannot be called while a request is in flight")
        return {"case_revision": self.cycle.case_revision, "snapshot_graph_node_ids": sorted(snapshot.graph.nodes) if snapshot else sorted(self.graph.nodes), "observation_graph_node_ids": sorted(exposed), "legal_graph_node_ids": sorted(legal), "active_reasoning_node_ids": sorted(snapshot.active_reasoning_node_ids) if snapshot else sorted(legal), "visible_source_ids": sorted(source_ids)}

    def legal_node_ids(self) -> set[str]:
        """IDs in the same active reasoning view used by the Investigator prompt."""
        return set(self._view_for(self.graph, self.focus, self._new_nodes).nodes)

    @staticmethod
    def _view_for(graph: CaseGraph, focus: InvestigationFocus, new_nodes: set[str]) -> CaseGraph:
        recent = set(focus.recent_node_ids) | set(focus.recent_region_node_ids)
        nearby = {node.id for identifier in recent if identifier in graph.nodes for node in graph.neighbors(identifier)}
        allowed = {focus.node_id, *(node.id for node in graph.neighbors(focus.node_id)), *recent, *nearby, *new_nodes, *(node.id for node in graph.nodes.values() if node.node_type is GraphNodeType.SOURCE and node.status is GraphStatus.ACTIVE)}
        return graph.model_copy(update={
            "nodes": {identifier: graph.nodes[identifier] for identifier in allowed},
            "edges": {identifier: edge for identifier, edge in graph.edges.items() if edge.source_id in allowed and edge.target_id in allowed},
        })

    def apply_turn(self, response: InvestigatorTurnResponse | dict, snapshot: TurnSnapshot | None = None) -> InvestigatorCycleState:
        self._require_investigator_active()
        if snapshot is not None and (snapshot.case_revision != self.cycle.case_revision or snapshot.graph.model_dump(mode="json") != self.graph.model_dump(mode="json") or snapshot.focus.model_dump(mode="json") != self.focus.model_dump(mode="json")):
            raise CycleError(CycleFailureCode.TURN_ATOMIC_ROLLBACK, "Investigator turn baseline does not match its canonical snapshot")
        if isinstance(response, dict) and isinstance(response.get("graph_updates"), list) and len(response["graph_updates"]) > 5:
            raise CycleError(CycleFailureCode.TOO_MANY_GRAPH_UPDATES, "An Investigator turn may contain at most five graph updates")
        try:
            response = TURN_RESPONSE_ADAPTER.validate_python(response)
        except Exception as exc:
            raise CycleError(CycleFailureCode.TURN_SCHEMA_FAILURE, f"Invalid InvestigatorTurnResponse: {exc}") from exc
        if len(response.graph_updates) > 5:
            raise CycleError(CycleFailureCode.TOO_MANY_GRAPH_UPDATES, "An Investigator turn may contain at most five graph updates")
        working = GraphInvestigationCoordinator(deepcopy(self.graph), self.focus.model_copy(deep=True))
        working._new_nodes = set(snapshot.new_node_ids if snapshot is not None else self._new_nodes)
        try:
            for update in response.graph_updates:
                working.apply_investigator_update(update)
        except Exception as exc:
            raise CycleError(CycleFailureCode.TURN_ATOMIC_ROLLBACK, f"Investigator turn rolled back after graph update failure: {exc}") from exc
        next_step = response.next_step
        if isinstance(next_step, RequestEnquiry):
            self._validate_enquiry_request(next_step, working)
        elif isinstance(next_step, (RequestEvidence, RequestOpen)):
            self._validate_evidence_request(next_step, working)
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
        elif isinstance(next_step, (RequestEvidence, RequestOpen)):
            request = EvidenceRequest(
                request_id=f"R{len(self.cycle.evidence_request_history) + 1}",
                target_uncertainty_id=getattr(next_step, "target_uncertainty_id", None),
                information_sought=getattr(next_step, "information_sought", None),
                reason=next_step.reason,
                expected_information_value=getattr(next_step, "expected_information_value", None),
                requested_at_revision=self.cycle.case_revision,
            )
            self.cycle.evidence_request = request
            self.cycle.evidence_request_history.append(request.model_copy(deep=True))
            self.cycle.status = CycleStatus.WAITING_FOR_EVIDENCE
            self.cycle.handoff_reason = "REQUESTED_EVIDENCE"
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

    def validate_turn(self, response: InvestigatorTurnResponse | dict, snapshot: TurnSnapshot | None = None) -> None:
        """Validate a complete turn against a cloned coordinator without mutation."""
        scratch = deepcopy(self)
        scratch.apply_turn(response, snapshot=snapshot)

    def validate_steward_decision(self, decision: StewardDecision | dict, based_on_revision: int, review_context: StewardReviewContext | None = None, snapshot: TurnSnapshot | None = None) -> None:
        """Validate a Steward decision against a cloned coordinator without mutation."""
        scratch = deepcopy(self)
        scratch.apply_steward_decision(decision, based_on_revision, review_context, snapshot=snapshot)

    def steward_snapshot(self) -> StewardSnapshot:
        return StewardSnapshot(case_revision=self.cycle.case_revision, graph=deepcopy(self.graph), focus=self.focus.model_copy(deep=True), cycle=self.cycle.model_copy(deep=True), participants=deepcopy(self.participants), review_context=deepcopy(self.review_context))

    def complete_evidence_request(self, response: EvidenceRequestResponse | dict, released_source_ids: list[str] | None = None) -> InvestigatorCycleState:
        if self.cycle.status is not CycleStatus.WAITING_FOR_EVIDENCE or self.cycle.evidence_request is None:
            raise CycleError(CycleFailureCode.INVALID_EVIDENCE_REQUEST, "No human evidence request is pending")
        try:
            parsed = EvidenceRequestResponse.model_validate(response)
        except Exception as exc:
            raise CycleError(CycleFailureCode.INVALID_EVIDENCE_REQUEST, f"Invalid evidence request response: {exc}") from exc
        request = self.cycle.evidence_request
        if parsed.request_id != request.request_id:
            raise CycleError(CycleFailureCode.INVALID_EVIDENCE_REQUEST, "Evidence request response does not match the pending request")
        source_ids = list(released_source_ids if released_source_ids is not None else parsed.released_source_ids)
        if parsed.status == "fulfilled" and not source_ids:
            raise CycleError(CycleFailureCode.INVALID_EVIDENCE_REQUEST, "FULFILLED requires at least one released source")
        if parsed.status == "unavailable" and source_ids:
            raise CycleError(CycleFailureCode.INVALID_EVIDENCE_REQUEST, "UNAVAILABLE cannot release sources")
        completed = request.model_copy(update={"status": EvidenceRequestStatus(parsed.status), "released_source_ids": source_ids, "note": parsed.note})
        self.cycle.workflow_feedback = parsed.note
        self.cycle.evidence_request = None
        self.cycle.evidence_request_history[-1] = completed
        self.cycle.case_revision += 1
        self.cycle.status = CycleStatus.LOCAL_ACTIVE
        self.cycle.handoff_reason = None
        return self.cycle.model_copy(deep=True)

    def apply_steward_decision(self, decision: StewardDecision | dict, based_on_revision: int, review_context: StewardReviewContext | None = None, snapshot: TurnSnapshot | None = None) -> InvestigatorCycleState:
        if self.cycle.status is CycleStatus.ENQUIRY_IN_FLIGHT:
            raise CycleError(CycleFailureCode.STEWARD_WRITE_DURING_IN_FLIGHT, "Steward graph writes are forbidden while an enquiry is in flight")
        if self.cycle.status is not CycleStatus.AWAITING_STEWARD:
            raise CycleError(CycleFailureCode.STEWARD_HANDOFF_PENDING, "Steward decision requires AWAITING_STEWARD")
        if based_on_revision != self.cycle.case_revision:
            raise CycleError(CycleFailureCode.STALE_STEWARD_REVISION, "Steward decision was based on a stale case revision")
        if snapshot is not None and (snapshot.case_revision != self.cycle.case_revision or snapshot.graph.model_dump(mode="json") != self.graph.model_dump(mode="json") or snapshot.focus.model_dump(mode="json") != self.focus.model_dump(mode="json")):
            raise CycleError(CycleFailureCode.STALE_STEWARD_REVISION, "Steward decision baseline does not match its canonical snapshot")
        try:
            parsed = TypeAdapter(StewardDecision).validate_python(decision)
            if isinstance(parsed, (StewardRequestOpenDecision, StewardRequestEvidenceDecision)):
                self._create_human_request(parsed)
                return self.cycle.model_copy(deep=True)
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

    def _create_human_request(self, request: StewardRequestOpenDecision | StewardRequestEvidenceDecision) -> None:
        if self.cycle.evidence_request is not None:
            raise CycleError(CycleFailureCode.EVIDENCE_REQUEST_ALREADY_PENDING, "A human evidence request is already pending")
        if isinstance(request, StewardRequestEvidenceDecision):
            node = self.graph.nodes.get(request.target_uncertainty_id)
            if node is None or node.node_type is not GraphNodeType.UNCERTAINTY or node.status is not GraphStatus.ACTIVE:
                raise CycleError(CycleFailureCode.INVALID_ENQUIRY_TARGET, "Steward targeted request must identify an active uncertainty")
        item = EvidenceRequest(request_id=f"R{len(self.cycle.evidence_request_history) + 1}", target_uncertainty_id=getattr(request, "target_uncertainty_id", None), information_sought=request.information_sought, reason=request.reason, expected_information_value=request.expected_information_value, requested_at_revision=self.cycle.case_revision)
        self.cycle.evidence_request = item
        self.cycle.evidence_request_history.append(item.model_copy(deep=True))
        self.cycle.status = CycleStatus.WAITING_FOR_EVIDENCE
        self.cycle.handoff_reason = "REQUESTED_EVIDENCE"

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

    def _validate_evidence_request(self, request: RequestEvidence, working: GraphInvestigationCoordinator) -> None:
        if self.cycle.evidence_request is not None:
            raise CycleError(CycleFailureCode.EVIDENCE_REQUEST_ALREADY_PENDING, "A human evidence request is already pending")
        if isinstance(request, RequestOpen):
            return
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
        if self.cycle.status is CycleStatus.WAITING_FOR_EVIDENCE:
            raise CycleError(CycleFailureCode.EVIDENCE_REQUEST_ALREADY_PENDING, "No Investigator turn may start while a human evidence request is pending")
        if self.cycle.status is CycleStatus.AWAITING_STEWARD:
            raise CycleError(CycleFailureCode.STEWARD_HANDOFF_PENDING, "No Investigator turn may start while Steward review is pending")
        if self.cycle.status is CycleStatus.STOPPED:
            raise CycleError(CycleFailureCode.INVESTIGATOR_NOT_ACTIVE, "The Investigator cycle is stopped")
