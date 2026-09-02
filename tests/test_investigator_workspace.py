import inspect
from pathlib import Path

from streamlit.testing.v1 import AppTest

from app.case_service import default_workspace, mark_unavailable, partially_fulfil, provide_evidence
from app import streamlit_app


def test_default_workspace_is_mechanism_neutral() -> None:
    state = default_workspace()
    visible = repr(state).lower()
    assert state.current_focus
    assert state.current_request.information_sought
    assert "smart glasses" not in visible
    assert "df-26-091" not in visible
    assert "external ai service" not in visible


def test_request_actions_update_mock_state_without_backend_calls() -> None:
    state = default_workspace()
    assert provide_evidence(state).current_request.status == "fulfilled"
    assert partially_fulfil(state).current_request.status == "partially_fulfilled"
    assert mark_unavailable(state).current_request.status == "unavailable"


def test_workspace_surface_has_current_request_actions_and_no_generic_chat() -> None:
    source = inspect.getsource(streamlit_app)
    for text in ("Current focus", "Information requested", "Provide evidence", "Partially fulfil", "Unavailable", "Clarify", "Redirect"):
        assert text in source
    assert "Message AI" not in source
    assert "st.chat_input" not in source
    assert "smart glasses" not in source.lower()


def test_context_sections_are_collapsed_by_default() -> None:
    source = inspect.getsource(streamlit_app.render_collapsed_context)
    assert 'st.expander("Unresolved questions")' in source
    assert 'st.expander("Active explanations")' in source
    assert 'st.expander("Case history")' in source
    assert "expanded=True" not in source


def test_all_request_action_panels_open_offline() -> None:
    labels = ["Provide evidence", "Partially fulfil", "Unavailable", "Clarify", "Redirect"]
    for index, label in enumerate(labels, start=2):
        app = AppTest.from_file(Path(__file__).parents[1] / "app/streamlit_app.py").run()
        app.button[index].click().run()
        assert label in [button.label for button in app.button]
        assert len(app.text_area) >= 2
