from investigator.graph import CaseGraph, GraphNode, GraphNodeType
from investigator.services.evidence_requests import HumanEvidenceWorkflow
from investigator.state.case_state import CaseState
from investigator.state.repository import CaseRepository
from investigator.workspace_agent import WorkspaceAgent, WorkspaceChatRequest, WorkspaceToolAuthorizationError


def make_workflow(tmp_path):
    workflow = HumanEvidenceWorkflow(CaseRepository(tmp_path / "cases"))
    state = CaseState(case_id="case-01", title="Test case", reasoning_graph=CaseGraph(case_id="case-01", nodes={"U1": GraphNode(id="U1", node_type=GraphNodeType.UNCERTAINTY, statement="Open question")}, edges={}), focus_node_id="U1")
    workflow.repository.save(state)
    return workflow


def test_workspace_read_is_deterministic_and_does_not_mutate(tmp_path):
    workflow = make_workflow(tmp_path)
    agent = WorkspaceAgent(workflow)
    before = workflow.ensure_case("case-01").model_dump(mode="json")
    result = agent.chat("case-01", "Explain the current case")
    after = workflow.ensure_case("case-01")
    assert result.response.startswith("The case is")
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
