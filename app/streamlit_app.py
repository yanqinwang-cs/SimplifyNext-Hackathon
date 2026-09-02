"""Compact investigator workspace prototype for the Stage 2 development case."""

import streamlit as st

try:  # Streamlit executes this file with app/ as the import directory.
    from case_service import (
        CaseWorkspaceState,
        clarify_request,
        get_case_workspace,
        mark_unavailable,
        partially_fulfil,
        provide_evidence,
        redirect_investigation,
    )
except ModuleNotFoundError:  # Support importing app.streamlit_app in tests.
    from app.case_service import (
        CaseWorkspaceState,
        clarify_request,
        get_case_workspace,
        mark_unavailable,
        partially_fulfil,
        provide_evidence,
        redirect_investigation,
    )


st.set_page_config(page_title="SimplifyNext — Investigator Workspace", page_icon="S", layout="wide")


def initialize_session() -> None:
    st.session_state.setdefault("workspace", get_case_workspace("case-01"))
    st.session_state.setdefault("active_panel", None)
    st.session_state.setdefault("notice", None)


def _set_workspace(updated: CaseWorkspaceState) -> None:
    st.session_state.workspace = updated
    st.session_state.active_panel = None
    st.session_state.notice = "Update recorded."


def render_styles() -> None:
    st.markdown(
        """
        <style>
        .block-container { max-width: 1120px; padding-top: 2rem; padding-bottom: 2rem; }
        .eyebrow { color:#55708f; font-size:.78rem; font-weight:700; letter-spacing:.08em; text-transform:uppercase; }
        .status { color:#174ea6; background:#e8f0fe; border-radius:999px; padding:.28rem .7rem; font-size:.85rem; font-weight:700; }
        .message { border:1px solid #dce3ed; border-radius:9px; padding:.75rem 1rem; margin:.55rem 0; background:#fff; }
        .message strong { color:#172b4d; }
        .message-meta { color:#75839a; font-size:.8rem; margin-left:.5rem; }
        .assistant-message { border-left:3px solid #2563b8; }
        .focus-label, .request-label { color:#174ea6; font-weight:700; font-size:.95rem; margin-bottom:.35rem; }
        .focus-text { font-size:1.22rem; line-height:1.42; color:#172b4d; }
        .request-text { color:#172b4d; font-size:1.05rem; line-height:1.45; }
        .why { color:#53657d; font-size:.94rem; line-height:1.45; margin-top:.7rem; }
        div.stButton > button { min-height:2.55rem; border-radius:6px; font-weight:650; border:1px solid #2563b8; color:#174ea6; background:#fff; }
        div.stButton > button:hover { border-color:#123f91; color:#123f91; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_header(state: CaseWorkspaceState) -> None:
    left, right = st.columns([5, 2])
    with left:
        st.markdown(f"<div class='eyebrow'>SimplifyNext · {state.case_id}</div>", unsafe_allow_html=True)
        st.title(state.title)
    with right:
        st.markdown(f"<div style='text-align:right; margin-top:1.8rem'><span class='status'>● {state.status}</span></div>", unsafe_allow_html=True)
        source_col, details_col = st.columns(2)
        if source_col.button("Sources", use_container_width=True):
            st.session_state.active_panel = "sources"
        if details_col.button("Details", use_container_width=True):
            st.session_state.active_panel = "details"


def render_sources(state: CaseWorkspaceState) -> None:
    with st.expander("Sources", expanded=True):
        st.caption("Received and visible case materials")
        for source in state.visible_sources:
            st.write(f"• {source}")


def render_details(state: CaseWorkspaceState) -> None:
    with st.expander("Case details", expanded=True):
        st.write(f"**Current line of enquiry**  \n{state.current_line_of_enquiry}")
        st.write(f"**Request status:** {state.current_request.status.replace('_', ' ')}")


def render_history(state: CaseWorkspaceState) -> None:
    for message in state.messages:
        css_class = "assistant-message" if message.role == "simplifynext" else ""
        role = "SimplifyNext" if message.role == "simplifynext" else "Investigator"
        st.markdown(f"<div class='message {css_class}'><strong>{role}</strong><span class='message-meta'>{message.timestamp}</span><br>{message.content}</div>", unsafe_allow_html=True)


def render_latest_update(state: CaseWorkspaceState) -> None:
    request = state.current_request
    st.markdown(
        f"""
        <div class="message assistant-message">
          <strong>SimplifyNext</strong><span class="message-meta">10:22 AM</span>
          <div class="focus-label" style="margin-top:.8rem">Current focus</div>
          <div class="focus-text">{state.current_focus}</div>
          <hr>
          <div class="request-label">Information requested</div>
          <div class="request-text">{request.information_sought}</div>
          <div class="why"><strong>Why this matters</strong><br>{request.reason}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_action_panel(state: CaseWorkspaceState) -> None:
    st.markdown("**Current request**")
    actions = st.columns(5)
    labels = [("Provide evidence", "provide"), ("Partially fulfil", "partial"), ("Unavailable", "unavailable"), ("Clarify", "clarify"), ("Redirect", "redirect")]
    for column, (label, panel) in zip(actions, labels):
        if column.button(label, use_container_width=True, key=f"action-{panel}"):
            st.session_state.active_panel = panel
    panel = st.session_state.active_panel
    if panel == "provide":
        with st.form("provide-evidence"):
            st.caption(state.current_request.information_sought)
            st.file_uploader("Evidence file", key="provide_file")
            note = st.text_area("Optional note", key="provide_note")
            if st.form_submit_button("Submit evidence", type="primary"):
                _set_workspace(provide_evidence(state, note))
                st.rerun()
    elif panel == "partial":
        with st.form("partial-evidence"):
            st.file_uploader("Evidence available", key="partial_file")
            unavailable = st.text_area("What could not be obtained?", key="partial_unavailable")
            if st.form_submit_button("Submit partial fulfilment", type="primary"):
                _set_workspace(partially_fulfil(state, unavailable))
                st.rerun()
    elif panel == "unavailable":
        with st.form("unavailable"):
            reason = st.text_area("Optional reason", key="unavailable_reason")
            if st.form_submit_button("Confirm unavailable", type="primary"):
                _set_workspace(mark_unavailable(state, reason))
                st.rerun()
    elif panel in {"clarify", "redirect"}:
        label = "Clarification question" if panel == "clarify" else "New investigative direction"
        with st.form(panel):
            text = st.text_area(label)
            if st.form_submit_button("Submit", type="primary") and text.strip():
                action = clarify_request if panel == "clarify" else redirect_investigation
                _set_workspace(action(state, text))
                st.rerun()


def render_composer(state: CaseWorkspaceState) -> None:
    with st.form("composer", clear_on_submit=True):
        text = st.text_area("Add note, clarification, or evidence…", label_visibility="collapsed", height=72, placeholder="Add note, clarification, or evidence…")
        attach, submit = st.columns([5, 1])
        with attach:
            st.file_uploader("Attach files", label_visibility="collapsed", key="composer_file")
        with submit:
            if st.form_submit_button("Send", type="primary", use_container_width=True) and text.strip():
                _set_workspace(clarify_request(state, text))
                st.rerun()


def render_collapsed_context(state: CaseWorkspaceState) -> None:
    with st.expander("Unresolved questions"):
        for item in state.unresolved_questions:
            st.write(f"• {item}")
    with st.expander("Active explanations"):
        for item in state.active_explanations:
            st.write(f"• {item}")
    with st.expander("Case history"):
        for item in state.case_history:
            st.write(f"• {item}")


def main() -> None:
    initialize_session()
    state = st.session_state.workspace
    render_styles()
    render_header(state)
    if st.session_state.active_panel == "sources":
        render_sources(state)
    elif st.session_state.active_panel == "details":
        render_details(state)
    render_history(state)
    render_latest_update(state)
    render_action_panel(state)
    render_composer(state)
    render_collapsed_context(state)
    if st.session_state.notice:
        st.info(st.session_state.notice)


if __name__ == "__main__":
    main()
