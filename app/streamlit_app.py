import streamlit as st
from dotenv import load_dotenv

from demo_data import DEMO_MESSAGES
from investigator.llm.bedrock import BedrockModelClient
from investigator.services import InvestigationService, InvestigationSession, SessionStatus


st.set_page_config(page_title="SimplifyNext — Case 01", page_icon="S", layout="wide")


def initialize_session() -> None:
    if "messages" not in st.session_state:
        st.session_state.messages = [message.copy() for message in DEMO_MESSAGES]
    st.session_state.setdefault("investigation", None)
    st.session_state.setdefault("investigation_service", None)
    st.session_state.setdefault("review_mode", None)
    st.session_state.setdefault("notice", None)


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
        st.rerun()


def start_investigation() -> None:
    load_dotenv()
    try:
        service = InvestigationService(BedrockModelClient())
        session = service.start_case()
    except Exception as exc:
        st.session_state.notice = f"Could not start the investigation: {exc}"
        return
    st.session_state.investigation_service = service
    st.session_state.investigation = session
    st.session_state.notice = None


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
                service.execute_action(session)
                service.propose_revision(session)
                st.session_state.notice = "Action executed. A revision is ready for review."
            except Exception as exc:
                st.session_state.notice = f"Could not prepare the revision: {exc}"
            st.rerun()
        if columns[1].button("Redirect", use_container_width=True):
            st.session_state.review_mode = "redirect"
            st.rerun()
        if columns[2].button("Correct", use_container_width=True):
            st.session_state.review_mode = "correct"
            st.rerun()
        if columns[3].button("Stop", use_container_width=True):
            service.stop(session)
            st.session_state.notice = "Investigation paused."
            st.rerun()
        if st.session_state.review_mode == "redirect":
            replacement = st.selectbox("Redirect to", ["A1", "A2", "A3", "A4"])
            reason = st.text_input("Why redirect?", key="redirect_reason")
            if st.button("Save redirect", type="primary"):
                try:
                    service.set_human_action(session, replacement, reason or None)
                    st.session_state.review_mode = None
                    st.session_state.notice = f"Action redirected to {replacement}."
                except Exception as exc:
                    st.session_state.notice = str(exc)
                st.rerun()
        elif st.session_state.review_mode == "correct":
            st.text_area("What should be corrected?", key="correction_text")
            if st.button("Save correction", type="primary"):
                st.session_state.review_mode = None
                st.session_state.notice = "Correction recorded. Automated reassessment from investigator steering is not connected yet."
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
                service.apply_revision(session)
                service.propose_next_action(session)
                st.session_state.notice = "Revision applied. The next action is ready for review." if session.pending_action else "All available actions are complete."
            except Exception as exc:
                st.session_state.notice = f"Could not apply the revision: {exc}"
            st.rerun()
        if columns[1].button("Correct", use_container_width=True):
            st.session_state.review_mode = "revision_correct"
            st.rerun()
        if columns[2].button("Stop", use_container_width=True):
            service.stop(session)
            st.session_state.notice = "Investigation paused."
            st.rerun()
        if st.session_state.review_mode == "revision_correct":
            st.text_area("What should be corrected?", key="revision_correction")
            if st.button("Record correction"):
                st.session_state.notice = "Correction recorded. Automated reassessment is not connected yet."
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


if __name__ == "__main__":
    main()
