from app.case_service import default_workspace


def test_default_workspace_has_investigator_surface_and_no_hidden_mechanism():
    workspace = default_workspace()
    text = repr(workspace).lower()
    assert workspace["current_focus"]
    assert workspace["request"]["information_sought"]
    assert "smart-glasses" not in text
    assert "df-26-091" not in text
    assert "external ai service" not in text


def test_contextual_request_actions_update_mock_state():
    from app.case_service import clarify_request, mark_unavailable, partially_fulfil, provide_evidence, redirect_investigation

    workspace = default_workspace()
    provide_evidence(workspace, "wifi-record.pdf", "Uploaded source.")
    assert workspace["request"]["status"] == "fulfilled"
    partially_fulfil(workspace, "partial.pdf", "Handset logs unavailable.", "Partial record.")
    assert workspace["request"]["status"] == "partially_fulfilled"
    mark_unavailable(workspace, "No record exists.")
    assert workspace["request"]["status"] == "unavailable"
    clarify_request(workspace, "Which period should be checked?")
    assert workspace["request"]["status"] == "clarification_needed"
    redirect_investigation(workspace, "Review a different unresolved question.")
    assert workspace["current_line"] == "Review a different unresolved question."
