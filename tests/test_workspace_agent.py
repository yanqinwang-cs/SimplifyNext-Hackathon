from investigator.graph import CaseGraph, GraphNode, GraphNodeType
from investigator.llm import ModelCallMetadata, ModelNativeCall, ModelTextBlock, ModelToolUse
from investigator.services.evidence_requests import HumanEvidenceWorkflow
from investigator.state.case_state import CaseState
from investigator.state.repository import CaseRepository
from investigator.workspace_agent import WorkspaceAgent, WorkspaceChatRequest, WorkspaceSessionStore, WorkspaceToolAuthorizationError


class FakeWorkspaceClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def call_native(self, input_data, tools):
        self.calls.append((input_data, tools))
        return self.responses.pop(0)


def text_response(text):
    return ModelNativeCall(text_blocks=[ModelTextBlock(text=text)], metadata=ModelCallMetadata(provider="fake", model="workspace-test", latency_seconds=0, parse_success=True))


def tool_response(name, arguments=None, call_id="call_001"):
    return ModelNativeCall(tool_uses=[ModelToolUse(call_id=call_id, name=name, arguments=arguments or {})], metadata=ModelCallMetadata(provider="fake", model="workspace-test", latency_seconds=0, parse_success=True))


def make_workflow(tmp_path):
    workflow = HumanEvidenceWorkflow(CaseRepository(tmp_path / "cases"))
    state = CaseState(case_id="case-01", title="Test case", reasoning_graph=CaseGraph(case_id="case-01", nodes={"U1": GraphNode(id="U1", node_type=GraphNodeType.UNCERTAINTY, statement="Open question")}, edges={}), focus_node_id="U1")
    workflow.repository.save(state)
    return workflow


def test_workspace_read_is_deterministic_and_does_not_mutate(tmp_path):
    workflow = make_workflow(tmp_path)
    agent = WorkspaceAgent(workflow, FakeWorkspaceClient([tool_response("GET_CASE_SUMMARY"), text_response("The current case is active and has one unresolved question.")]))
    before = workflow.ensure_case("case-01").model_dump(mode="json")
    result = agent.chat("case-01", "Explain the current case")
    after = workflow.ensure_case("case-01")
    assert result.response.startswith("The current case")
    assert after.reasoning_graph.model_dump(mode="json") == before["reasoning_graph"]
    assert after.revision == before["revision"]


def test_workspace_rejects_semantic_graph_tools(tmp_path):
    try:
        WorkspaceAgent(make_workflow(tmp_path)).invoke_tool("case-01", {"tool": "add_evidence"})
    except (WorkspaceToolAuthorizationError, ValueError):
        pass
    else:
        raise AssertionError("semantic graph mutation must be denied")


def test_workspace_uses_canonical_opus_registry_without_calling_model(tmp_path):
    agent = WorkspaceAgent(make_workflow(tmp_path))
    assert agent.model_spec.name == "anthropic.claude-opus-4-5"
    assert agent.model_spec.invocation_id == "us.anthropic.claude-opus-4-5-20251101-v1:0"
    assert WorkspaceChatRequest(message="Explain the case").message


def test_workspace_latest_failure_uses_two_native_model_turns_and_one_tool(tmp_path):
    workflow = make_workflow(tmp_path)
    state = workflow.ensure_case("case-01")
    state.trace_history.append({"event": "investigator_failed", "failure_category": "PARSE", "committed": False})
    workflow.repository.save(state)
    client = FakeWorkspaceClient([tool_response("GET_LATEST_FAILURE"), text_response("The latest Investigator turn failed during parsing and did not commit a case change.")])
    result = WorkspaceAgent(workflow, client).chat("case-01", "What happened in the latest run?")
    assert len(client.calls) == 2
    assert result.response.endswith("case change.")


def test_workspace_direct_answer_uses_native_text(tmp_path):
    result = WorkspaceAgent(make_workflow(tmp_path), FakeWorkspaceClient([text_response("The investigation is waiting for a human response.")])).chat("case-01", "Tell me something unusual")
    assert result.response == "The investigation is waiting for a human response."


def test_native_tool_arguments_are_typed_and_unknown_fields_rejected(tmp_path):
    agent = WorkspaceAgent(make_workflow(tmp_path))
    assert agent.invoke_tool("case-01", {"tool": "GET_CASE_STATUS", "payload": {}})["caseStatus"] == "ACTIVE"
    try:
        agent.invoke_tool("case-01", {"tool": "GET_CASE_STATUS", "payload": {"unexpected": True}})
    except ValueError as exc:
        assert "Extra inputs are not permitted" in str(exc)
    else:
        raise AssertionError("tool arguments must be schema validated")


def test_exact_native_run_tool_call_executes_once_then_gets_final_model_turn(tmp_path):
    workflow = make_workflow(tmp_path)
    workflow.run_callback = lambda case_id, service: None
    client = FakeWorkspaceClient([tool_response("RUN_INVESTIGATION"), text_response("The investigation has started and is running.")])
    result = WorkspaceAgent(workflow, client).chat("case-01", "RUN_INVESTIGATION")
    assert len(client.calls) == 2
    assert result.response == "The investigation has started and is running."
    assert workflow.get_workspace("case-01")["latestRun"]["run_id"] == "run_000001"


def test_native_unauthorized_tool_is_rejected_before_execution(tmp_path):
    client = FakeWorkspaceClient([tool_response("add_evidence")])
    try:
        WorkspaceAgent(make_workflow(tmp_path), client).chat("case-01", "please mutate the graph")
    except WorkspaceToolAuthorizationError:
        pass
    else:
        raise AssertionError("semantic tool must be rejected")


def test_recover_retains_canonical_state(tmp_path):
    workflow = make_workflow(tmp_path)
    state = workflow.ensure_case("case-01")
    state.clean_checkpoint = {"state": {"revision": 0}, "signature": "not-used"}
    state.revision = 4
    workflow.repository.save(state)
    WorkspaceAgent(workflow).invoke_tool("case-01", {"tool": "RECOVER_FROM_SAFE_STATE"})
    assert workflow.ensure_case("case-01").revision == 4
    assert workflow.ensure_case("case-01").trace_history[-1]["event"] == "workspace_recovery"


def test_last_safe_state_reports_latest_committed_canonical_state(tmp_path):
    workflow = make_workflow(tmp_path)
    state = workflow.ensure_case("case-01")
    state.revision = 4
    workflow.repository.save(state)
    result = WorkspaceAgent(workflow).invoke_tool("case-01", {"tool": "GET_LAST_SAFE_STATE"})
    assert result["revision"] == 4
    assert result["source"] == "latest_committed_safe_state"
    assert result["state"]["revision"] == 4


def test_workspace_failure_is_queryable_separately(tmp_path):
    workflow = make_workflow(tmp_path)
    client = FakeWorkspaceClient([tool_response("GET_LATEST_WORKSPACE_FAILURE"), text_response("The latest Workspace turn failed while processing its requested operation.")])
    result = WorkspaceAgent(workflow, client).chat("case-01", "why?")
    assert len(client.calls) == 2
    assert "latest Workspace turn failed" in result.response


def test_workspace_sends_full_session_history_to_followup(tmp_path):
    workflow = make_workflow(tmp_path)
    workflow.run_callback = lambda case_id, service: None
    client = FakeWorkspaceClient([text_response("Would you like me to run the investigation?"), tool_response("RUN_INVESTIGATION"), text_response("Run started.")])
    agent = WorkspaceAgent(workflow, client)
    agent.chat("case-01", "run the investigation")
    agent.chat("case-01", "yes")
    assert len(client.calls) == 3
    followup_messages = client.calls[1][0]
    assert [message["text"] for message in followup_messages if message.get("role") == "user"][-2:] == ["run the investigation", "yes"]
    assert any(message.get("text") == "Would you like me to run the investigation?" for message in followup_messages)


def test_workspace_session_store_resets_without_affecting_case_state(tmp_path):
    workflow = make_workflow(tmp_path)
    store = WorkspaceSessionStore()
    WorkspaceAgent(workflow, FakeWorkspaceClient([text_response("hello")]), store).chat("case-01", "hello")
    assert store.session("case-01")["chat_history"]
    assert WorkspaceSessionStore().session("case-01")["chat_history"] == []
    assert workflow.ensure_case("case-01").reasoning_graph is not None
