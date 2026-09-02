import streamlit as st

try:
    from case_service import clarify_request, get_case_workspace, mark_unavailable, partially_fulfil, provide_evidence, redirect_investigation, set_status
except ModuleNotFoundError:
    from app.case_service import clarify_request, get_case_workspace, mark_unavailable, partially_fulfil, provide_evidence, redirect_investigation, set_status

st.set_page_config(page_title="SimplifyNext — Investigator Workspace", page_icon="S", layout="wide")


def initialize_workspace() -> None:
    if "workspace" not in st.session_state:
        st.session_state.workspace = get_case_workspace("CASE-01")
    st.session_state.setdefault("request_panel", None)
    st.session_state.setdefault("notice", None)


def refresh(message: str) -> None:
    st.session_state.notice = message
    st.rerun()


def render_header(workspace: dict) -> None:
    left, status, pause, redirect, stop = st.columns([4.4, 1.2, 1, 1, 1])
    with left:
        st.title("SimplifyNext")
        st.caption(f"{workspace['title']}  ·  {workspace['case_id']}")
    with status:
        st.markdown(f"<div class='status-pill'>{workspace['status']}</div>", unsafe_allow_html=True)
    if pause.button("Pause", use_container_width=True):
        set_status(workspace, "Paused")
        refresh("Case paused. The current state was preserved.")
    if redirect.button("Redirect", use_container_width=True):
        st.session_state.request_panel = "redirect"
    if stop.button("Stop", use_container_width=True):
        set_status(workspace, "Stopped")
        refresh("Case stopped. The current state remains available for review.")


def render_request(workspace: dict) -> None:
    request = workspace["request"]
    st.subheader("Information requested")
    st.markdown(f"<div class='request-card'><div class='eyebrow'>{request['status'].replace('_', ' ').title()}</div><div class='request-title'>{request['information_sought']}</div><div class='label'>Why this matters</div><div>{request['reason']}</div></div>", unsafe_allow_html=True)
    actions = st.columns(5)
    for column, key, label in zip(actions, ("provide", "partial", "unavailable", "clarify", "redirect"), ("Provide evidence", "Partially fulfil", "Mark unavailable", "Clarify request", "Redirect investigation")):
        if column.button(label, use_container_width=True, key=f"request-{key}"):
            st.session_state.request_panel = key
    panel = st.session_state.request_panel
    if panel == "provide":
        with st.container(border=True):
            st.markdown("**Provide evidence**")
            st.text_input("Requested information", value=request["information_sought"], disabled=True, key="provide-request")
            file = st.file_uploader("Upload file", key="provide-file")
            note = st.text_area("Optional note", key="provide-note")
            if st.button("Add to case", type="primary", key="provide-submit"):
                provide_evidence(workspace, file.name if file else "", note)
                st.session_state.request_panel = None
                refresh("Evidence added to the case workspace.")
    elif panel == "partial":
        with st.container(border=True):
            st.markdown("**Partially fulfil request**")
            file = st.file_uploader("Evidence provided", key="partial-file")
            unavailable = st.text_area("What could not be obtained?", key="partial-unavailable")
            note = st.text_area("Investigator note", key="partial-note")
            if st.button("Record partial fulfilment", type="primary", key="partial-submit"):
                partially_fulfil(workspace, file.name if file else "", unavailable, note)
                st.session_state.request_panel = None
                refresh("Partial fulfilment recorded.")
    elif panel == "unavailable":
        with st.container(border=True):
            st.markdown("**Mark information unavailable**")
            reason = st.text_area("Optional reason", key="unavailable-reason")
            if st.button("Record unavailable", type="primary", key="unavailable-submit"):
                mark_unavailable(workspace, reason)
                st.session_state.request_panel = None
                refresh("Information unavailability recorded as workflow context.")
    elif panel == "clarify":
        with st.container(border=True):
            st.markdown("**Clarify request**")
            question = st.text_area("Question for the investigation system", placeholder="What period do you mean by around the assessment?", key="clarify-question")
            if st.button("Record clarification", type="primary", key="clarify-submit"):
                clarify_request(workspace, question)
                st.session_state.request_panel = None
                refresh("Clarification recorded in the activity history.")
    elif panel == "redirect":
        with st.container(border=True):
            st.markdown("**Redirect investigation**")
            focus = st.text_area("New focus", placeholder="Focus instead on a different unresolved question.", key="redirect-focus")
            if st.button("Save redirect", type="primary", key="redirect-submit"):
                redirect_investigation(workspace, focus)
                st.session_state.request_panel = None
                refresh("Human redirection recorded.")


def render_activity(workspace: dict) -> None:
    st.subheader("Activity")
    for entry in reversed(workspace["activity"]):
        st.markdown(f"<div class='activity-row'><span class='activity-time'>{entry['time']}</span><span class='activity-actor'>{entry['actor']}</span><span><strong>{entry['title']}</strong><br>{entry['summary']}</span></div>", unsafe_allow_html=True)


def render_inspection(workspace: dict) -> None:
    with st.expander("Sources"):
        for source in workspace["sources"]:
            st.markdown(f"- {source}")
    with st.expander("Important unresolved questions"):
        for question in workspace["unresolved"]:
            st.markdown(f"- {question}")
    with st.expander("Active explanations"):
        for explanation in workspace["explanations"]:
            st.markdown(f"- {explanation}")
    with st.expander("Case history"):
        st.caption("History is represented in the activity feed for this prototype.")


def main() -> None:
    initialize_workspace()
    workspace = st.session_state.workspace
    st.markdown("""<style>
    .stApp { background: #f7f8fa; color: #172033; }
    .block-container { max-width: 1120px; padding-top: 2rem; }
    h1 { letter-spacing: -0.04em; }
    .status-pill { border: 1px solid #b9c4d3; border-radius: 999px; padding: .35rem .7rem; text-align: center; margin-top: 1.35rem; background: #fff; font-size: .85rem; }
    .request-card { background: #fff; border: 1px solid #d8dee8; border-left: 4px solid #476b9b; padding: 1.1rem 1.25rem; margin-bottom: .9rem; }
    .eyebrow, .label { color: #5d6c80; font-size: .75rem; text-transform: uppercase; letter-spacing: .08em; margin-bottom: .4rem; }
    .request-title { font-size: 1.12rem; margin-bottom: 1rem; }
    .activity-row { display: grid; grid-template-columns: 4rem 9rem 1fr; gap: .7rem; border-top: 1px solid #dfe4eb; padding: .75rem 0; font-size: .9rem; }
    .activity-time { color: #65758b; font-variant-numeric: tabular-nums; }
    .activity-actor { color: #344b68; font-weight: 600; }
    @media (max-width: 760px) { .activity-row { grid-template-columns: 3.5rem 1fr; } .activity-row span:last-child { grid-column: 2; } }
    </style>""", unsafe_allow_html=True)
    render_header(workspace)
    st.divider()
    st.subheader("Current focus")
    st.markdown(f"### {workspace['current_focus']}")
    st.subheader("Current line of enquiry")
    st.write(workspace["current_line"])
    st.subheader("Latest update")
    st.info(workspace["latest_update"])
    render_request(workspace)
    render_activity(workspace)
    render_inspection(workspace)
    if st.session_state.notice:
        st.success(st.session_state.notice)


if __name__ == "__main__":
    main()
