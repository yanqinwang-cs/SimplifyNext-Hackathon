import streamlit as st
from dotenv import load_dotenv
from pathlib import Path

from demo_data import DEMO_MESSAGES
from investigator.environments.case_01 import Case1ControlledEnvironment
from investigator.llm.bedrock import BedrockModelClient
from investigator.services import InvestigationService, InvestigationSession, ModelStructuredOutputError, SessionStatus
from investigator.tracing import InteractiveTrace, InteractiveTraceWriter, new_session_id, utc_now


st.set_page_config(page_title="SimplifyNext — Case 01", page_icon="S", layout="wide")


def initialize_session() -> None:
    if "messages" not in st.session_state:
        st.session_state.messages = [message.copy() for message in DEMO_MESSAGES]
    st.session_state.setdefault("investigation", None)
    st.session_state.setdefault("investigation_service", None)
    st.session_state.setdefault("review_mode", None)
    st.session_state.setdefault("notice", None)
    st.session_state.setdefault("structured_error", None)
    st.session_state.setdefault("structured_raw_output", None)
    st.session_state.setdefault("trace_writer", None)


def _jsonable(value):
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else value


def _persist(event_type: str, payload: dict | None = None, error: Exception | None = None) -> None:
    writer = st.session_state.trace_writer
    if writer is None:
        return
    session = st.session_state.investigation
    values = {}
    if session is not None:
        values.update(
            status=session.status.value,
            current_case_state=_jsonable(session.case_state),
            initial_raw_model_output=session.initial_raw_model_output,
            initial_response=_jsonable(session.initial_response),
            initial_metadata=_jsonable(session.initial_metadata),
        )
    if error is not None:
        values["latest_error"] = {"stage": getattr(error, "stage", event_type), "message": str(error)}
        if payload and "raw_model_output" in payload:
            values["latest_error"]["raw_model_output"] = payload["raw_model_output"]
    writer.update(**values)
    writer.record(event_type, payload)


def _record_model_error(exc: Exception) -> None:
    payload = {"stage": getattr(exc, "stage", "model_call"), "error": str(exc)}
    if getattr(exc, "raw_output", None) is not None:
        payload["raw_model_output"] = exc.raw_output
    _persist("model_error", payload, exc)


def render_hypotheses(session: InvestigationSession) -> None:
    active = [h for h in session.case_state.hypotheses.values() if h.status.value not in {"removed", "rejected", "archived"}]
    if active:
        st.subheader("Active hypotheses")
        for hypothesis in active:
            st.markdown(f"- **{hypothesis.id}** — {hypothesis.statement}")


def render_uncertainties(session: InvestigationSession) -> None:
    unresolved = [
        (identifier, uncertainty)
        for identifier, uncertainty in session.case_state.uncertainties.items()
        if any(identifier in hypothesis.unresolved_issue_ids for hypothesis in session.case_state.hypotheses.values())
    ]
    if unresolved:
        st.subheader("Key uncertainties")
        for identifier, uncertainty in unresolved:
            st.markdown(f"- **{identifier}** — {uncertainty.description}")


def render_evidence(session: InvestigationSession) -> None:
    if not session.case_state.evidence:
        return
    with st.expander("Evidence"):
        for identifier, evidence in session.case_state.evidence.items():
            with st.expander(identifier):
                st.caption(evidence.raw_content[:500])


def render_sidebar(session: InvestigationSession | None) -> None:
    with st.sidebar:
        if session is None:
            st.caption("Start an investigation to inspect its case state.")
            return
        render_hypotheses(session)
        render_uncertainties(session)
        render_evidence(session)


def render_chat() -> None:
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.write(message["content"])
    prompt = st.chat_input("Type a message…")
    if prompt:
        st.session_state.messages.extend([
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": "Message recorded for review. Automated steering is not connected yet."},
        ])
        st.session_state.notice = "Correction or chat input was recorded. Automated reassessment is not connected yet."
        _persist("investigator_correction_recorded", {"text": prompt})
        st.rerun()


def start_investigation() -> None:
    load_dotenv()
    environment = Case1ControlledEnvironment(Path(__file__).resolve().parents[1] / "experiments/investigation_smoke/case_01/artifacts")
    try:
        client = BedrockModelClient()
        model_id = client.model_id
    except Exception as exc:
        trace = InteractiveTrace(session_id=new_session_id(), case_id=environment.case_id, environment_id=environment.environment_id, model_id="unknown-model", started_at=utc_now(), updated_at=utc_now(), status="error", initial_prompt=environment.initial_prompt())
        st.session_state.trace_writer = InteractiveTraceWriter(Path(__file__).resolve().parents[1] / "runs", trace)
        _persist("investigation_started", {})
        _record_model_error(exc)
        st.session_state.notice = f"Could not start the investigation: {exc}"
        return
    trace = InteractiveTrace(session_id=new_session_id(), case_id=environment.case_id, environment_id=environment.environment_id, model_id=model_id, started_at=utc_now(), updated_at=utc_now(), status="starting", initial_prompt=environment.initial_prompt())
    st.session_state.trace_writer = InteractiveTraceWriter(Path(__file__).resolve().parents[1] / "runs", trace)
    _persist("investigation_started", {})
    try:
        service = InvestigationService(client, environment)
        session = service.start_case()
    except ModelStructuredOutputError as exc:
        st.session_state.notice = "The model returned an invalid structured response."
        st.session_state.structured_error = str(exc)
        st.session_state.structured_raw_output = exc.raw_output
        _record_model_error(exc)
        return
    except Exception as exc:
        st.session_state.notice = f"Could not start the investigation: {exc}"
        _record_model_error(exc)
        return
    st.session_state.investigation_service = service
    st.session_state.investigation = session
    st.session_state.notice = None
    st.session_state.structured_error = None
    st.session_state.structured_raw_output = None
    _persist("initial_model_response", {"raw_model_output": session.initial_raw_model_output, "parsed_response": _jsonable(session.initial_response), "metadata": _jsonable(session.initial_metadata)})
    _persist("action_proposed", {"action_id": session.pending_action.action_id})


def render_action_review(session: InvestigationSession, service: InvestigationService) -> None:
    action = session.pending_action
    if action is None:
        return
    with st.expander("Review next action", expanded=True):
        st.markdown(f"**Recommended next step**  \n{action.action_id} — {action.title}")
        st.write(session.action_reason or "This step may help distinguish between the current explanations.")
        columns = st.columns(4)
        if columns[0].button("Approve", use_container_width=True):
            try:
                _persist("human_action_approved", {"action_id": action.action_id})
                service.execute_action(session)
                _persist("action_executed", {"action_id": action.action_id})
                _persist("evidence_released", {"artifact_id": session.pending_release.artifact_id})
                service.propose_revision(session)
                _persist("revision_model_response", {"raw_model_output": session.revision_raw_model_output, "parsed_response": _jsonable(session.pending_revision), "metadata": _jsonable(session.revision_metadata)})
                _persist("revision_proposed", {"action_id": action.action_id})
                st.session_state.notice = "Action executed. A revision is ready for review."
            except Exception as exc:
                if isinstance(exc, ModelStructuredOutputError):
                    st.session_state.notice = "The model returned an invalid structured response."
                    st.session_state.structured_error = str(exc)
                    st.session_state.structured_raw_output = exc.raw_output
                    _record_model_error(exc)
                else:
                    st.session_state.notice = f"Could not prepare the revision: {exc}"
                    _persist("state_error", {"error": str(exc)}, exc)
            st.rerun()
        if columns[1].button("Redirect", use_container_width=True):
            st.session_state.review_mode = "redirect"
            st.rerun()
        if columns[2].button("Correct", use_container_width=True):
            st.session_state.review_mode = "correct"
            st.rerun()
        if columns[3].button("Stop", use_container_width=True):
            service.stop(session)
            _persist("investigation_stopped", {})
            st.session_state.notice = "Investigation stopped. The current state has been preserved."
            st.rerun()
        if st.session_state.review_mode == "redirect":
            replacement = st.selectbox("Redirect to", ["A1", "A2", "A3", "A4"])
            reason = st.text_input("Why redirect?", key="redirect_reason")
            if st.button("Save redirect", type="primary"):
                try:
                    service.set_human_action(session, replacement, reason or None)
                    _persist("human_action_redirected", {"from_action_id": action.action_id, "to_action_id": replacement, "reason": reason or None})
                    st.session_state.review_mode = None
                    st.session_state.notice = f"Action redirected to {replacement}."
                except Exception as exc:
                    st.session_state.notice = str(exc)
                    _persist("state_error", {"error": str(exc)}, exc)
                st.rerun()
        elif st.session_state.review_mode == "correct":
            st.text_area("What should be corrected?", key="correction_text")
            if st.button("Save correction", type="primary"):
                st.session_state.review_mode = None
                st.session_state.notice = "Correction recorded. Automated reassessment from investigator steering is not connected yet."
                _persist("investigator_correction_recorded", {"text": st.session_state.correction_text})
                st.rerun()


def render_revision_review(session: InvestigationSession, service: InvestigationService) -> None:
    revision = session.pending_revision
    if revision is None:
        return
    with st.expander("Review proposed changes", expanded=True):
        st.markdown("**Proposed changes**")
        for update in revision.hypothesis_updates:
            st.markdown(f"- {update.hypothesis_id} — {update.transition.value}")
        for update in revision.uncertainty_updates:
            st.markdown(f"- {update.uncertainty_id} — {update.transition.value}")
        with st.expander("Reasons"):
            for update in revision.hypothesis_updates:
                st.caption(f"{update.hypothesis_id}: {update.reason}")
            for update in revision.uncertainty_updates:
                st.caption(f"{update.uncertainty_id}: {update.reason}")
        columns = st.columns(3)
        if columns[0].button("Approve revision", type="primary", use_container_width=True):
            try:
                _persist("human_revision_approved", {})
                service.apply_revision(session)
                _persist("revision_applied", {})
                service.propose_next_action(session)
                if session.pending_action:
                    _persist("next_action_proposed", {"action_id": session.pending_action.action_id, "raw_model_output": session.next_action_raw_model_output, "metadata": _jsonable(session.next_action_metadata)})
                    st.session_state.notice = "Revision applied. I have prepared the next recommended enquiry."
                else:
                    _persist("next_action_proposed", {"action_id": None})
                    st.session_state.notice = "Investigation cycle complete. No further controlled enquiries are currently available."
            except Exception as exc:
                if isinstance(exc, ModelStructuredOutputError):
                    st.session_state.notice = "The model returned an invalid structured response."
                    st.session_state.structured_error = str(exc)
                    st.session_state.structured_raw_output = exc.raw_output
                    _record_model_error(exc)
                else:
                    st.session_state.notice = f"Could not apply the revision: {exc}"
                    _persist("state_error", {"error": str(exc)}, exc)
            st.rerun()
        if columns[1].button("Correct", use_container_width=True):
            st.session_state.review_mode = "revision_correct"
            st.rerun()
        if columns[2].button("Stop", use_container_width=True):
            service.stop(session)
            _persist("investigation_stopped", {})
            st.session_state.notice = "Investigation stopped. The current state has been preserved."
            st.rerun()
        if st.session_state.review_mode == "revision_correct":
            st.text_area("What should be corrected?", key="revision_correction")
            if st.button("Record correction"):
                st.session_state.notice = "Correction recorded. Automated reassessment is not connected yet."
                _persist("investigator_correction_recorded", {"text": st.session_state.revision_correction})
                st.rerun()


def main() -> None:
    initialize_session()
    session = st.session_state.investigation
    service = st.session_state.investigation_service
    st.title("SimplifyNext")
    st.caption("Academic integrity review — Case 01")
    render_sidebar(session)
    st.subheader("Investigator chat")
    render_chat()
    if session is None:
        st.write("Review the case with a controlled investigation assistant.")
        if st.button("Start investigation", type="primary"):
            with st.spinner("Starting investigation…"):
                start_investigation()
            st.rerun()
    else:
        if session.status is SessionStatus.AWAITING_ACTION_REVIEW:
            render_action_review(session, service)
        elif session.status is SessionStatus.AWAITING_REVISION_REVIEW:
            render_revision_review(session, service)
        elif session.status is SessionStatus.STOPPED:
            st.info("Investigation stopped. The current state remains available for inspection.")
        elif session.status is SessionStatus.PAUSED:
            st.info("Investigation paused. The current state remains available for inspection.")
    if st.session_state.notice:
        st.info(st.session_state.notice)
    if st.session_state.structured_error:
        with st.expander("Show raw model output"):
            if st.session_state.structured_raw_output:
                st.code(st.session_state.structured_raw_output, language="json")
            st.caption(st.session_state.structured_error)


if __name__ == "__main__":
    main()
