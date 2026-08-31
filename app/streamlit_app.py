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
    st.session_state.setdefault("seed_hypothesis", "")


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


def render_evidence(session: InvestigationSession, service: InvestigationService) -> None:
    if not session.case_state.evidence:
        return
    with st.expander("Evidence"):
        for identifier, evidence in session.case_state.evidence.items():
            with st.expander(identifier):
                st.caption(evidence.raw_content[:500])
        with st.expander("Correct evidence"):
            evidence_id = st.selectbox("Evidence item", list(session.case_state.evidence), key="correction_evidence_id")
            current = session.case_state.get_evidence(evidence_id)
            st.caption(current.raw_content[:500])
            corrected = st.text_area("Corrected authoritative content", key="corrected_evidence_content")
            reason = st.text_input("Reason (optional)", key="evidence_correction_reason")
            if st.button("Save evidence correction", type="primary"):
                try:
                    previous = current.raw_content
                    service.correct_evidence(session, evidence_id, corrected, reason or None)
                    _persist("evidence_correction", {"evidence_id": evidence_id, "previous_content": previous, "corrected_content": corrected, "reason": reason or None})
                    st.session_state.notice = "Evidence corrected. Investigation is paused for reassessment."
                except Exception as exc:
                    st.session_state.notice = str(exc)
                    _persist("state_error", {"error": str(exc)}, exc)
                st.rerun()


def render_sidebar(session: InvestigationSession | None) -> None:
    with st.sidebar:
        if session is None:
            st.caption("Start an investigation to inspect its case state.")
            return
        render_hypotheses(session)
        render_uncertainties(session)
        render_evidence(session, st.session_state.investigation_service)


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


def start_investigation(seed_hypothesis: str) -> None:
    load_dotenv()
    environment = Case1ControlledEnvironment(Path(__file__).resolve().parents[1] / "experiments/investigation_smoke/case_01/artifacts")
    try:
        client = BedrockModelClient()
        model_id = client.model_id
    except Exception as exc:
        trace = InteractiveTrace(session_id=new_session_id(), case_id=environment.case_id, environment_id=environment.environment_id, model_id="unknown-model", started_at=utc_now(), updated_at=utc_now(), status="error", initial_prompt=environment.initial_prompt(), investigator_seed_hypothesis=seed_hypothesis)
        st.session_state.trace_writer = InteractiveTraceWriter(Path(__file__).resolve().parents[1] / "runs", trace)
        _persist("investigation_started", {})
        _record_model_error(exc)
        st.session_state.notice = f"Could not start the investigation: {exc}"
        return
    trace = InteractiveTrace(session_id=new_session_id(), case_id=environment.case_id, environment_id=environment.environment_id, model_id=model_id, started_at=utc_now(), updated_at=utc_now(), status="starting", initial_prompt=environment.initial_prompt(), investigator_seed_hypothesis=seed_hypothesis)
    st.session_state.trace_writer = InteractiveTraceWriter(Path(__file__).resolve().parents[1] / "runs", trace)
    _persist("investigation_started", {})
    try:
        service = InvestigationService(client, environment)
        session = service.start_case(seed_hypothesis)
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
    _persist("investigator_seed_hypothesis", {"statement": seed_hypothesis, "hypothesis_id": "H1"})
    try:
        service.advance_until_interrupt(session, on_event=lambda event, payload: _persist(event, payload))
        _persist("autonomous_investigation_advanced", {})
    except Exception as exc:
        st.session_state.notice = f"Investigation paused: {exc}"
        _persist("state_error", {"error": str(exc)}, exc)


def render_action_review(session: InvestigationSession, service: InvestigationService) -> None:
    action = session.pending_action
    if action is None:
        return
    with st.expander("Review next action", expanded=True):
        st.markdown(f"**Recommended next step**  \n{action.action_id} — {action.title}")
        st.write(session.action_reason or "This step may help distinguish between the current explanations.")
        columns = st.columns(5)
        if columns[0].button("Continue investigation", use_container_width=True):
            try:
                service.advance_until_interrupt(session, on_event=lambda event, payload: _persist(event, payload))
                _persist("autonomous_investigation_advanced", {})
                st.session_state.notice = "Investigation advanced."
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
        if columns[3].button("Pause", use_container_width=True):
            service.pause(session)
            _persist("investigation_paused", {})
            st.session_state.notice = "Investigation paused. The current state has been preserved."
            st.rerun()
        if columns[4].button("Stop", use_container_width=True):
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
        removals = [update for update in revision.hypothesis_updates if update.transition.value == "remove"]
        if removals:
            st.warning("Review proposed hypothesis removal")
            for removal in removals:
                hypothesis = session.case_state.get_hypothesis(removal.hypothesis_id)
                st.write(f"**{hypothesis.id}** — {hypothesis.statement}")
                st.caption(f"Reason: {removal.reason}")
                st.caption(f"Evidence basis: {', '.join(removal.add_conflicting_evidence_ids) or 'none'}")
                review_columns = st.columns(2)
                if review_columns[0].button("Confirm removal", key=f"remove-{hypothesis.id}"):
                    try:
                        _persist("human_removal_decision", {"hypothesis_id": hypothesis.id, "decision": "confirm"})
                        service.resolve_hypothesis_removal(session, hypothesis.id, True)
                        st.session_state.notice = "Hypothesis removal applied."
                    except Exception as exc:
                        st.session_state.notice = f"Could not apply removal: {exc}"
                        _persist("state_error", {"error": str(exc)}, exc)
                    st.rerun()
                if review_columns[1].button("Keep hypothesis", key=f"keep-{hypothesis.id}"):
                    try:
                        _persist("human_removal_decision", {"hypothesis_id": hypothesis.id, "decision": "keep"})
                        service.resolve_hypothesis_removal(session, hypothesis.id, False)
                        st.session_state.notice = "Hypothesis retained. Routine updates were applied."
                    except Exception as exc:
                        st.session_state.notice = f"Could not retain hypothesis: {exc}"
                        _persist("state_error", {"error": str(exc)}, exc)
                    st.rerun()
        columns = st.columns(4)
        if not removals and columns[0].button("Continue investigation", type="primary", use_container_width=True):
            try:
                service.advance_until_interrupt(session, on_event=lambda event, payload: _persist(event, payload))
                st.session_state.notice = "Investigation advanced."
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
        if columns[2].button("Pause", use_container_width=True):
            service.pause(session)
            _persist("investigation_paused", {})
            st.session_state.notice = "Investigation paused. The current state has been preserved."
            st.rerun()
        if columns[3].button("Stop", use_container_width=True):
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
        seed = st.text_area("Starting hypothesis / concern", help="Enter one broad explanation worth investigating. Avoid assuming a specific mechanism unless already established.", key="seed_hypothesis")
        if st.button("Start investigation", type="primary"):
            if not seed.strip():
                st.warning("Enter a starting hypothesis or concern before starting.")
            else:
                with st.spinner("Investigating…"):
                    start_investigation(seed)
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
            if st.button("Continue investigation", type="primary"):
                with st.spinner("Investigating…"):
                    try:
                        session.status = SessionStatus.READY
                        service.advance_until_interrupt(session, on_event=lambda event, payload: _persist(event, payload))
                        _persist("autonomous_investigation_advanced", {})
                    except Exception as exc:
                        st.session_state.notice = f"Investigation paused: {exc}"
                        _persist("state_error", {"error": str(exc)}, exc)
                st.rerun()
    if st.session_state.notice:
        st.info(st.session_state.notice)
    if st.session_state.structured_error:
        with st.expander("Show raw model output"):
            if st.session_state.structured_raw_output:
                st.code(st.session_state.structured_raw_output, language="json")
            st.caption(st.session_state.structured_error)


if __name__ == "__main__":
    main()
