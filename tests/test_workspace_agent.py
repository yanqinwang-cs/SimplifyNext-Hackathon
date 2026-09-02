from investigator.graph import CaseGraph, GraphNode, GraphNodeType
from investigator.services.evidence_requests import HumanEvidenceWorkflow
from investigator.state.case_state import CaseState
from investigator.state.repository import CaseRepository
from investigator.workspace_agent import WorkspaceAgent, WorkspaceChatRequest, WorkspaceToolAuthorizationError, WorkspaceTurn, normalize_workspace_tool_call
from investigator.llm.base import ModelCallMetadata, ModelCallResult


class FakeWorkspaceClient:
    def __init__(self, turns):
        self.turns = list(turns)
        self.calls = []

    def call(self, input_data, output_schema):
        self.calls.append((input_data, output_schema))
        return ModelCallResult(parsed=output_schema.model_validate(self.turns.pop(0)), metadata=ModelCallMetadata(provider="fake", model="workspace-test", latency_seconds=0, parse_success=True))


def make_workflow(tmp_path):
    workflow = HumanEvidenceWorkflow(CaseRepository(tmp_path / "cases"))
    state = CaseState(case_id="case-01", title="Test case", reasoning_graph=CaseGraph(case_id="case-01", nodes={"U1": GraphNode(id="U1", node_type=GraphNodeType.UNCERTAINTY, statement="Open question")}, edges={}), focus_node_id="U1")
    workflow.repository.save(state)
    return workflow


def test_workspace_read_is_deterministic_and_does_not_mutate(tmp_path):
    workflow = make_workflow(tmp_path)
    agent = WorkspaceAgent(workflow, FakeWorkspaceClient([
        {"tool_calls": [{"name": "GET_CASE_SUMMARY", "arguments": {}}]},
        {"response": "The current case is active and has one unresolved question."},
    ]))
    before = workflow.ensure_case("case-01").model_dump(mode="json")
    result = agent.chat("case-01", "Explain the current case")
    after = workflow.ensure_case("case-01")
    assert result.response.startswith("The current case")
    assert after.reasoning_graph.model_dump(mode="json") == before["reasoning_graph"]
    assert after.revision == before["revision"]
    assert after.workspace_chat_history


def test_workspace_rejects_semantic_graph_tools(tmp_path):
    agent = WorkspaceAgent(make_workflow(tmp_path))
    try:
        agent.invoke_tool("case-01", {"tool": "add_evidence"})
    except Exception as exc:
        assert isinstance(exc, (WorkspaceToolAuthorizationError, ValueError))
    else:
        raise AssertionError("semantic graph mutation must be denied")


def test_workspace_uses_canonical_opus_registry_without_calling_model(tmp_path):
    agent = WorkspaceAgent(make_workflow(tmp_path))
    assert agent.model_spec.name == "anthropic.claude-opus-4-5"
    assert agent.model_spec.invocation_id == "us.anthropic.claude-opus-4-5-20251101-v1:0"
    assert WorkspaceChatRequest(message="Explain the case").message


def test_workspace_latest_failure_uses_two_model_turns_and_one_tool(tmp_path):
    workflow = make_workflow(tmp_path)
    state = workflow.ensure_case("case-01")
    state.trace_history.append({"event": "investigator_failed", "failure_category": "PARSE", "committed": False})
    workflow.repository.save(state)
    client = FakeWorkspaceClient([
        {"tool_calls": [{"name": "GET_LATEST_FAILURE", "arguments": {}}]},
        {"response": "The latest Investigator turn failed during parsing and did not commit a case change."},
    ])
    result = WorkspaceAgent(workflow, client).chat("case-01", "What happened in the latest run?")
    assert len(client.calls) == 2
    assert result.response.endswith("case change.")


def test_workspace_direct_answer_does_not_use_canned_fallback(tmp_path):
    client = FakeWorkspaceClient([{ "response": "The investigation is waiting for a human response." }])
    result = WorkspaceAgent(make_workflow(tmp_path), client).chat("case-01", "Tell me something unusual")
    assert result.response == "The investigation is waiting for a human response."
    assert "I can explain the current case" not in result.response


def test_provider_function_tool_call_is_normalized_before_authorization():
    raw = {"id": "call_001", "type": "function", "function": {"name": "RUN_INVESTIGATION", "arguments": "{}"}}
    normalized = normalize_workspace_tool_call(raw)
    assert normalized["name"] == "RUN_INVESTIGATION"
    assert normalized["arguments"] == {}
    assert WorkspaceTurn(tool_calls=[raw]).tool_calls[0].provider_call_id == "call_001"


def test_provider_tool_call_arguments_are_checked_and_native_shape_supported():
    assert normalize_workspace_tool_call({"type": "tool_use", "id": "tool_1", "name": "GET_CASE_STATUS", "input": {}})["name"] == "GET_CASE_STATUS"
    assert normalize_workspace_tool_call({"name": "READ_SOURCE", "arguments": '{"source_id":"S1"}'})["arguments"] == {"source_id": "S1"}
    try:
        normalize_workspace_tool_call({"name": "READ_SOURCE", "arguments": "{bad json"})
    except ValueError as exc:
        assert "not valid JSON" in str(exc)
    else:
        raise AssertionError("malformed tool arguments must fail")


def test_exact_provider_run_tool_call_executes_once_then_gets_final_model_turn(tmp_path):
    workflow = make_workflow(tmp_path)
    workflow.run_callback = lambda case_id, service: None
    client = FakeWorkspaceClient([
        {"tool_calls": [{"id": "call_001", "type": "function", "function": {"name": "RUN_INVESTIGATION", "arguments": "{}"}}]},
        {"response": "The investigation has started and is running."},
    ])
    result = WorkspaceAgent(workflow, client).chat("case-01", "RUN_INVESTIGATION")
    assert len(client.calls) == 2
    assert result.response == "The investigation has started and is running."
    assert workflow.get_workspace("case-01")["latestRun"]["run_id"] == "run_000001"


def test_normalized_unauthorized_tool_is_rejected_before_execution(tmp_path):
    client = FakeWorkspaceClient([{ "tool_calls": [{"type": "function", "function": {"name": "add_evidence", "arguments": "{}"}}] }])
    try:
        WorkspaceAgent(make_workflow(tmp_path), client).chat("case-01", "please mutate the graph")
    except WorkspaceToolAuthorizationError:
        pass
    else:
        raise AssertionError("semantic tool must be rejected after normalization")
