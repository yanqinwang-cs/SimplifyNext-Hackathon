from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from pydantic import ValidationError

from investigator.environments.base import InvestigationEnvironment
from investigator.llm.base import ModelClient, ModelParseError
from investigator.state import apply_revision


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


class ModelStructuredOutputError(ModelParseError):
    """A model response failed JSON/schema validation, retaining raw output."""

    def __init__(self, message: str, *, stage: str, raw_output: Any = None) -> None:
        super().__init__(message, raw_output=raw_output)
        self.stage = stage


@dataclass
class InvestigationSession:
    case_state: Any
    status: SessionStatus = SessionStatus.READY
    pending_action: Any | None = None
    pending_release: Any | None = None
    pending_revision: Any | None = None
    completed_action_ids: set[str] = field(default_factory=set)
    action_reason: str | None = None
    environment_id: str = ""
    initial_case_state: Any | None = None
    initial_response: Any | None = None
    initial_raw_model_output: Any | None = None
    initial_metadata: Any | None = None
    revision_raw_model_output: Any | None = None
    revision_metadata: Any | None = None
    revision_prompt: str | None = None
    unsupported_operations: list[dict[str, Any]] = field(default_factory=list)


class InvestigationService:
    """Advances one bounded semantic operation at a time in an explicit environment."""

    def __init__(self, client: ModelClient, environment: InvestigationEnvironment) -> None:
        self.client = client
        self.environment = environment

    def start_case(self) -> InvestigationSession:
        from investigator.services.contracts import InitialResponse

        try:
            result = self.client.call(self.environment.initial_prompt(), InitialResponse)
            response = InitialResponse.model_validate(result.parsed)
        except Exception as exc:
            self._raise_structured_output_error(exc, "initial_parse", locals().get("result"))
        state = self.environment.build_initial_state(response)
        return InvestigationSession(
            case_state=state,
            initial_case_state=state.model_copy(deep=True),
            initial_response=response,
            initial_raw_model_output=result.raw_output,
            initial_metadata=result.metadata,
            pending_action=self.environment.get_action(response.selected_action_id),
            status=SessionStatus.AWAITING_ACTION_REVIEW,
            environment_id=self.environment.environment_id,
        )

    def propose_next_action(self, session: InvestigationSession) -> InvestigationSession:
        self._require_status(session, SessionStatus.READY)
        from investigator.services.contracts import NextActionResponse

        available = self.environment.available_actions(session.completed_action_ids)
        if not available:
            session.status = SessionStatus.PAUSED
            session.pending_action = None
            return session
        try:
            result = self.client.call(self.environment.next_action_prompt(session, available), NextActionResponse)
            response = NextActionResponse.model_validate(result.parsed)
        except Exception as exc:
            self._raise_structured_output_error(exc, "next_action_parse", locals().get("result"))
        available_ids = {action.action_id for action in available}
        if response.selected_action_id not in available_ids:
            raise ValueError(f"Action is not currently available: {response.selected_action_id!r}")
        session.pending_action = self.environment.get_action(response.selected_action_id)
        session.action_reason = response.why_this_action_now
        session.status = SessionStatus.AWAITING_ACTION_REVIEW
        return session

    def set_human_action(self, session: InvestigationSession, action_id: str, reason: str | None = None) -> InvestigationSession:
        self._require_status(session, SessionStatus.AWAITING_ACTION_REVIEW)
        action = self.environment.get_action(action_id)
        if action.action_id in session.completed_action_ids:
            raise ValueError(f"Action already completed: {action.action_id!r}")
        if action.action_id not in {a.action_id for a in self.environment.available_actions(session.completed_action_ids)}:
            raise ValueError(f"Action is not available: {action.action_id!r}")
        session.pending_action = action
        session.action_reason = reason
        return session

    def execute_action(self, session: InvestigationSession) -> InvestigationSession:
        self._require_status(session, SessionStatus.AWAITING_ACTION_REVIEW)
        if session.pending_action is None:
            raise InvalidSessionTransition("Cannot execute without a pending action")
        updated = session.case_state.model_copy(deep=True)
        release = self.environment.execute_action(updated, session.pending_action.action_id)
        session.case_state = updated
        session.pending_release = release
        session.completed_action_ids.add(session.pending_action.action_id)
        session.status = SessionStatus.ACTION_EXECUTED
        return session

    def propose_revision(self, session: InvestigationSession) -> InvestigationSession:
        self._require_status(session, SessionStatus.ACTION_EXECUTED)
        if session.pending_action is None or session.pending_release is None:
            raise InvalidSessionTransition("Cannot propose revision without an executed action")
        from investigator.services.contracts import RevisionResponse

        prompt = self.environment.revision_prompt(session, session.pending_release)
        try:
            result = self.client.call(prompt, RevisionResponse)
            session.pending_revision = RevisionResponse.model_validate(result.parsed)
        except Exception as exc:
            self._raise_structured_output_error(exc, "revision_parse", locals().get("result"))
        session.revision_prompt = prompt
        session.revision_raw_model_output = result.raw_output
        session.revision_metadata = result.metadata
        session.status = SessionStatus.AWAITING_REVISION_REVIEW
        return session

    def apply_revision(self, session: InvestigationSession) -> InvestigationSession:
        self._require_status(session, SessionStatus.AWAITING_REVISION_REVIEW)
        if session.pending_revision is None:
            raise InvalidSessionTransition("Cannot apply without a pending revision")
        pending = session.pending_revision
        session.case_state = apply_revision(session.case_state, pending)
        session.unsupported_operations = [
            {"kind": "hypothesis", **u.model_dump(mode="json")}
            for u in pending.hypothesis_updates if u.transition.value == "other"
        ] + [
            {"kind": "uncertainty", **u.model_dump(mode="json")}
            for u in pending.uncertainty_updates if u.transition.value == "other"
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

    @staticmethod
    def _raise_structured_output_error(exc: Exception, stage: str, result: Any) -> None:
        if isinstance(exc, ModelParseError):
            raise ModelStructuredOutputError(str(exc), stage=stage, raw_output=exc.raw_output) from exc
        if isinstance(exc, ValidationError):
            raise ModelStructuredOutputError(
                f"Structured model output failed validation: {exc}",
                stage=stage,
                raw_output=getattr(result, "raw_output", None),
            ) from exc
        raise exc
