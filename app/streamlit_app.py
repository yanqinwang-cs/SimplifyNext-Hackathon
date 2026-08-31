import streamlit as st

from demo_data import (
    ACTIVE_HYPOTHESES,
    CLAIMS_TO_CHECK,
    DEMO_MESSAGES,
    EVIDENCE,
    KEY_UNCERTAINTIES,
    RELEASED_EVIDENCE,
    RECOMMENDED_ACTION,
)


st.set_page_config(page_title="SimplifyNext — Case 01", page_icon="S", layout="wide")

st.markdown(
    """
    <style>
    .block-container { max-width: 1180px; padding-top: 2rem; }
    .app-subtitle { color: #536070; font-size: 1.05rem; margin-top: -0.7rem; }
    </style>
    """,
    unsafe_allow_html=True,
)


def initialize_session() -> None:
    if "messages" not in st.session_state:
        st.session_state.messages = [message.copy() for message in DEMO_MESSAGES]
    if "review_status" not in st.session_state:
        st.session_state.review_status = None
    if "review_action" not in st.session_state:
        st.session_state.review_action = RECOMMENDED_ACTION["id"]
    if "review_note" not in st.session_state:
        st.session_state.review_note = ""


def render_sidebar() -> None:
    with st.sidebar:
        st.subheader("Active hypotheses")
        for identifier, label in ACTIVE_HYPOTHESES:
            st.markdown(f"- **{identifier}** — {label}")

        st.subheader("Key uncertainties")
        for identifier, label in KEY_UNCERTAINTIES:
            st.markdown(f"- **{identifier}** — {label}")

        st.subheader("Claims to check")
        for identifier, label, status in CLAIMS_TO_CHECK:
            st.markdown(f"**{identifier}** — {label}  \n<small>{status}</small>", unsafe_allow_html=True)

        with st.expander("Evidence"):
            for identifier, label in EVIDENCE:
                with st.expander(f"{identifier} — {label}"):
                    st.caption(label)
            if RELEASED_EVIDENCE:
                st.markdown("**Released evidence**")
                for identifier, label in RELEASED_EVIDENCE:
                    st.markdown(f"- {identifier} — {label}")


def render_review_controls() -> None:
    with st.expander("Review next action", expanded=st.session_state.review_status is None):
        st.markdown(f"**Recommended next step**  \n{RECOMMENDED_ACTION['id']} — {RECOMMENDED_ACTION['label']}")
        st.write(RECOMMENDED_ACTION["explanation"])
        st.caption("Review controls are temporary UI placeholders and do not modify case state.")

        actions = st.columns(4)
        if actions[0].button("Approve", use_container_width=True):
            st.session_state.review_status = "Action approved."
        if actions[1].button("Redirect", use_container_width=True):
            st.session_state.review_status = "redirect"
        if actions[2].button("Correct", use_container_width=True):
            st.session_state.review_status = "correct"
        if actions[3].button("Stop", use_container_width=True):
            st.session_state.review_status = "Investigation paused."

        if st.session_state.review_status == "redirect":
            st.session_state.review_action = st.selectbox("Redirect to", ["A1", "A2", "A3", "A4"])
            st.session_state.review_note = st.text_input("Why redirect?", value=st.session_state.review_note)
            if st.button("Save redirect", type="primary"):
                st.session_state.review_status = f"Redirect recorded to {st.session_state.review_action}."
        elif st.session_state.review_status == "correct":
            st.session_state.review_note = st.text_area("What should be corrected?", value=st.session_state.review_note)
            if st.button("Save correction", type="primary"):
                st.session_state.review_status = "Correction recorded."

        if st.session_state.review_status and st.session_state.review_status not in {"redirect", "correct"}:
            st.success(st.session_state.review_status)


def render_chat() -> None:
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.write(message["content"])

    prompt = st.chat_input("Type a message…")
    if prompt:
        st.session_state.messages.append({"role": "user", "content": prompt})
        st.session_state.messages.append({"role": "assistant", "content": "Message recorded for review."})
        st.rerun()


def main() -> None:
    initialize_session()
    st.title("SimplifyNext")
    st.markdown("Academic integrity review — Case 01", unsafe_allow_html=False)
    render_sidebar()
    st.subheader("Investigator chat")
    render_chat()
    render_review_controls()


if __name__ == "__main__":
    main()
