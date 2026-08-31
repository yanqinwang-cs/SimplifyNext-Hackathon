from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from investigator.llm.base import ModelClient


class SessionStatus(str, Enum):
    READY = "ready"
    AWAITING_ACTION_REVIEW = "awaiting_action_review"
    ACTION_EXECUTED = "action_executed"
    AWAITING_REVISION_REVIEW = "awaiting_revision_review"
    PAUSED = "paused"
    STOPPED = "stopped"
    ERROR = "error"


class InvalidSessionTransition(RuntimeError):
    pass


@dataclass
class InvestigationSession:
    case_state: Any
    status: SessionStatus = SessionStatus.READY
    pending_action: Any | None = None
    pending_release: Any | None = None
    pending_revision: Any | None = None
    completed_action_ids: set[str] = field(default_factory=set)
    action_reason: str | None = None
    initial_case_state: Any | None = None
    initial_response: Any | None = None
    initial_raw_model_output: Any | None = None
    initial_metadata: Any | None = None
    revision_raw_model_output: Any | None = None
    revision_metadata: Any | None = None
    revision_prompt: str | None = None
    unsupported_operations: list[dict[str, Any]] = field(default_factory=list)


class InvestigationService:
    """One bounded semantic investigation operation per method, backed by Case 1 for now."""

    def __init__(self, client: ModelClient) -> None:
        self.client = client

    def start_case(self) -> InvestigationSession:
        from experiments.investigation_smoke.case_01.prompts import initial_prompt
        from experiments.investigation_smoke.case_01.runner import initial_state
        from experiments.investigation_smoke.case_01.schemas import InitialResponse
        from experiments.investigation_smoke.case_01.catalog import get_action

        result = self.client.call(initial_prompt(), InitialResponse)
        response = InitialResponse.model_validate(result.parsed)
        state = initial_state(response)
        session = InvestigationSession(
            case_state=state,
            initial_case_state=state.model_copy(deep=True),
            initial_response=response,
            initial_raw_model_output=result.raw_output,
            initial_metadata=result.metadata,
            pending_action=get_action(response.selected_action_id),
            status=SessionStatus.AWAITING_ACTION_REVIEW,
        )
        return session

    def propose_next_action(self, session: InvestigationSession) -> InvestigationSession:
        self._require_status(session, SessionStatus.READY)
        from experiments.investigation_smoke.case_01.catalog import ENQUIRY_CATALOG, get_action
        from experiments.investigation_smoke.case_01.prompts import next_action_prompt
        from experiments.investigation_smoke.case_01.schemas import NextActionResponse

        available = [a for a in ENQUIRY_CATALOG if a.action_id not in session.completed_action_ids]
        if not available:
            session.status = SessionStatus.PAUSED
            session.pending_action = None
            return session
        state_dump = session.case_state.model_dump(mode="json")
        state_dump["evidence"] = list(state_dump["evidence"].values())
        state_dump["hypotheses"] = list(state_dump["hypotheses"].values())
        state_dump["uncertainties"] = list(state_dump["uncertainties"].values())
        result = self.client.call(
            next_action_prompt(
                "Current Case 01 evidence is represented in the state below.", state_dump,
                [{"action_id": a.action_id, "title": a.title, "definition": a.definition} for a in available],
            ),
            NextActionResponse,
        )
        response = NextActionResponse.model_validate(result.parsed)
        if response.selected_action_id in session.completed_action_ids:
            raise ValueError(f"Action already completed: {response.selected_action_id!r}")
        session.pending_action = get_action(response.selected_action_id)
        session.action_reason = response.why_this_action_now
        session.status = SessionStatus.AWAITING_ACTION_REVIEW
        return session

    def set_human_action(self, session: InvestigationSession, action_id: str, reason: str | None = None) -> InvestigationSession:
        self._require_status(session, SessionStatus.AWAITING_ACTION_REVIEW)
        from experiments.investigation_smoke.case_01.catalog import ENQUIRY_CATALOG, get_action

        action = get_action(action_id)
        if action.action_id in session.completed_action_ids:
            raise ValueError(f"Action already completed: {action.action_id!r}")
        if action not in ENQUIRY_CATALOG:
            raise ValueError(f"Action is not available: {action.action_id!r}")
        session.pending_action = action
        session.action_reason = reason
        return session

    def execute_action(self, session: InvestigationSession) -> InvestigationSession:
        self._require_status(session, SessionStatus.AWAITING_ACTION_REVIEW)
        if session.pending_action is None:
            raise InvalidSessionTransition("Cannot execute without a pending action")
        from experiments.investigation_smoke.case_01.runner import release_selected_artifact

        updated = session.case_state.model_copy(deep=True)
        release = release_selected_artifact(updated, session.pending_action.action_id)
        session.case_state = updated
        session.pending_release = release
        session.completed_action_ids.add(session.pending_action.action_id)
        session.status = SessionStatus.ACTION_EXECUTED
        return session

    def propose_revision(self, session: InvestigationSession) -> InvestigationSession:
        self._require_status(session, SessionStatus.ACTION_EXECUTED)
        if session.pending_action is None or session.pending_release is None:
            raise InvalidSessionTransition("Cannot propose revision without an executed action")
        from experiments.investigation_smoke.case_01.prompts import revision_prompt, visible_case_input
        from experiments.investigation_smoke.case_01.schemas import RevisionResponse

        prompt = revision_prompt(
                visible_case_input(),
                [h.model_dump(mode="json") for h in session.case_state.hypotheses.values()],
                [u.model_dump(mode="json") for u in session.case_state.uncertainties.values()],
                {"action_id": session.pending_action.action_id, "title": session.pending_action.title, "definition": session.pending_action.definition},
                session.pending_release.artifact_id,
                session.pending_release.content,
                [e.model_dump(mode="json") for e in session.case_state.evidence.values()],
            )
        result = self.client.call(prompt, RevisionResponse)
        session.pending_revision = RevisionResponse.model_validate(result.parsed)
        session.revision_prompt = prompt
        session.revision_raw_model_output = result.raw_output
        session.revision_metadata = result.metadata
        session.status = SessionStatus.AWAITING_REVISION_REVIEW
        return session

    def apply_revision(self, session: InvestigationSession) -> InvestigationSession:
        self._require_status(session, SessionStatus.AWAITING_REVISION_REVIEW)
        if session.pending_revision is None:
            raise InvalidSessionTransition("Cannot apply without a pending revision")
        from experiments.investigation_smoke.case_01.runner import apply_revision

        session.case_state = apply_revision(session.case_state, session.pending_revision)
        session.unsupported_operations = [
            {"kind": "hypothesis", **u.model_dump(mode="json")}
            for u in session.pending_revision.hypothesis_updates if u.transition.value == "other"
        ] + [
            {"kind": "uncertainty", **u.model_dump(mode="json")}
            for u in session.pending_revision.uncertainty_updates if u.transition.value == "other"
        ]
        session.pending_revision = None
        session.pending_release = None
        session.pending_action = None
        session.status = SessionStatus.READY
        return session

    def pause(self, session: InvestigationSession) -> InvestigationSession:
        if session.status is SessionStatus.STOPPED:
            raise InvalidSessionTransition("Stopped sessions cannot be paused")
        session.status = SessionStatus.PAUSED
        return session

    def stop(self, session: InvestigationSession) -> InvestigationSession:
        if session.status is SessionStatus.STOPPED:
            raise InvalidSessionTransition("Session is already stopped")
        session.status = SessionStatus.STOPPED
        return session

    @staticmethod
    def _require_status(session: InvestigationSession, expected: SessionStatus) -> None:
        if session.status is SessionStatus.STOPPED:
            raise InvalidSessionTransition("Stopped sessions cannot silently continue")
        if session.status is not expected:
            raise InvalidSessionTransition(f"Expected session status {expected.value}, got {session.status.value}")
