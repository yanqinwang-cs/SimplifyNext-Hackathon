import pytest

from investigator.graph import GraphScope, GraphScopeType
from investigator.models import AssessmentSubject, Source, SourceType, SubjectRelationship
from investigator.services.evidence_requests import HumanEvidenceWorkflow
from investigator.state import CaseRepository
from investigator.workspace_agent import WorkspaceAgent, WorkspaceToolAuthorizationError


def make_workflow(tmp_path, *, mode="vnext"):
    workflow = HumanEvidenceWorkflow(CaseRepository(tmp_path / "cases"), run_mode=mode)
    state = workflow.ensure_case("case-01")
    state.subjects = {"A": AssessmentSubject(subject_id="A", display_name="Candidate A"), "B": AssessmentSubject(subject_id="B", display_name="Candidate B")}
    state.subject_relationships = {"AB": SubjectRelationship(relationship_id="AB", subject_ids=["A", "B"], relationship_type="communication")}
    state.sources = {"S1": Source(id="S1", name="Initial", source_type=SourceType.DOCUMENT, content="initial")}
    workflow.repository.save(state)
    return workflow


def test_direct_source_ingestion_is_scoped_and_does_not_create_request_or_graph_node(tmp_path):
    workflow = make_workflow(tmp_path)
    before = workflow.ensure_case("case-01")
    source = workflow.add_direct_source("case-01", display_name="A record", content="record", source_type=SourceType.DOCUMENT, assessment_scope=GraphScope(scope_type=GraphScopeType.SUBJECT, subject_id="A"), expected_case_revision=before.revision)
    state = workflow.ensure_case("case-01")
    assert source.id in state.sources
    assert state.sources[source.id].metadata["assessment_scope"]["subject_id"] == "A"
    assert state.revision == before.revision + 1
    assert state.evidence_request_history == []
    assert state.reasoning_graph is None


def test_direct_source_ingestion_rejects_unknown_scope_identity(tmp_path):
    workflow = make_workflow(tmp_path)
    with pytest.raises(ValueError, match="Unknown subject"):
        workflow.add_direct_source("case-01", display_name="bad", content="record", assessment_scope={"scope_type": "subject", "subject_id": "C"})
    with pytest.raises(ValueError, match="Unknown relationship"):
        workflow.add_direct_source("case-01", display_name="bad", content="record", assessment_scope={"scope_type": "relationship", "relationship_id": "CD"})


def test_vnext_guidance_context_reports_no_assessment_and_read_only_tools(tmp_path):
    workflow = make_workflow(tmp_path)
    context = workflow.get_guidance_context("case-01")
    assert context["assessment"]["state"] == "not_started"
    assert context["assessment"]["assessment_is_stale"] is False
    assert "latest_successful_vnext_run" not in context
    agent = WorkspaceAgent(workflow)
    assert {item["name"] for item in agent.tool_specs()} == {"GET_CASE_GUIDANCE_CONTEXT", "GET_PRODUCT_GUIDE", "LIST_SOURCES", "READ_SOURCE"}
    with pytest.raises(WorkspaceToolAuthorizationError):
        agent.invoke_tool("case-01", {"tool": "ADD_SOURCE", "payload": {"display_name": "x", "content": "x"}})
