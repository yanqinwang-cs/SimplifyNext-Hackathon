import json
from pathlib import Path
import threading
import urllib.error
import urllib.request

import pytest

from investigator.graph import CaseGraph, GraphNode, GraphNodeType
from investigator.http_api import create_server, seed_sample_case, create_case
from investigator.llm import ModelNativeCall, ModelTextBlock, ModelCallMetadata
from investigator.models.assessment import AssessmentContext, AssessmentSubject, SubjectRelationship
from investigator.models.source import Source, SourceType
from investigator.services.evidence_requests import HumanEvidenceWorkflow
from investigator.state import CaseRepository, CaseState
from investigator.vnext import run_input_from_case_state
from investigator.vnext.model import build_prompt
from investigator.vnext.presets import preset_for_case
from investigator.workspace_agent import WorkspaceAgent, WorkspaceToolRequest


class NoCallClient:
    def __init__(self):
        self.calls = []

    def call_native(self, messages, tools):
        self.calls.append((messages, tools))
        return ModelNativeCall(text_blocks=[ModelTextBlock(text="unexpected")], metadata=ModelCallMetadata(provider="fake", model="fake", latency_seconds=0, parse_success=True))


def make_case(tmp_path: Path, *, case_id: str = "case-01", source_text: str = "Observed record") -> HumanEvidenceWorkflow:
    workflow = HumanEvidenceWorkflow(CaseRepository(tmp_path / "cases"), run_mode="vnext")
    state = CaseState(
        case_id=case_id,
        title="Administrative title",
        description="Administrative description",
        assessment_context=AssessmentContext(assessment_id="assessment-1", title="Administrative assessment", assessment_type="exam", venue="Room 1"),
        subjects={"subject_A": AssessmentSubject(subject_id="subject_A", display_name="Candidate A")},
        sources={"S1": Source(id="S1", name="record.md", source_type=SourceType.DOCUMENT, content=source_text, metadata={"internal_marker": "INTERNAL_SOURCE_METADATA"})},
        reasoning_graph=CaseGraph(case_id=case_id, nodes={"U1": GraphNode(id="U1", node_type=GraphNodeType.UNCERTAINTY, statement="Unresolved")}, edges={}),
    )
    workflow.repository.save(state)
    return workflow


def test_product_guide_is_one_source_and_no_argument_tool_is_callable(tmp_path: Path):
    client = NoCallClient()
    workflow = make_case(tmp_path)
    agent = WorkspaceAgent(workflow, client)
    tool_names = {item["name"] for item in agent.tool_specs()}
    assert "GET_PRODUCT_GUIDE" in tool_names
    spec = next(item for item in agent.tool_specs() if item["name"] == "GET_PRODUCT_GUIDE")
    assert spec["inputSchema"].get("properties", {}) == {}
    result = agent.invoke_tool("case-01", WorkspaceToolRequest(tool="GET_PRODUCT_GUIDE", payload={}))
    guide = result["guide"]
    assert guide == Path("src/investigator/help/product_guide.md").read_text(encoding="utf-8")
    assert "Student 1" in guide and ".txt" in guide and ".md" in guide
    assert "NOT_CURRENTLY_SUPPORTED" in guide and "Final institutional judgment remains human" in guide
    assert "create subjects" not in guide.lower()
    assert "source scope" not in guide.lower()
    assert client.calls == []


def test_help_context_is_safe_and_source_tools_are_case_bound(tmp_path: Path):
    workflow = make_case(tmp_path)
    context = WorkspaceAgent(workflow).invoke_tool("case-01", {"tool": "GET_CASE_GUIDANCE_CONTEXT", "payload": {}})
    serialized = json.dumps(context)
    for internal in ("U1", "INTERNAL_SOURCE_METADATA", "subject_A", "S1", "assessment-1"):
        assert internal not in serialized
    assert "Administrative title" in serialized
    assert "Candidate A" in serialized
    assert "record.md" in serialized
    sources = WorkspaceAgent(workflow).invoke_tool("case-01", {"tool": "LIST_SOURCES", "payload": {}})["sources"]
    assert set(sources[0]) == {"sourceHandle", "fileName", "documentFormat"}
    document = WorkspaceAgent(workflow).invoke_tool("case-01", {"tool": "READ_SOURCE", "payload": {"sourceHandle": sources[0]["sourceHandle"]}})
    assert set(document["source"]) == {"sourceHandle", "fileName", "documentFormat", "content"}
    with pytest.raises((KeyError, ValueError)):
        WorkspaceAgent(workflow).invoke_tool("case-01", {"tool": "READ_SOURCE", "payload": {"sourceHandle": "S1"}})
    other = make_case(tmp_path / "other", case_id="case-02")
    with pytest.raises((KeyError, ValueError)):
        WorkspaceAgent(other).invoke_tool("case-02", {"tool": "READ_SOURCE", "payload": {"sourceHandle": sources[0]["sourceHandle"]}})


def test_administrative_fields_do_not_change_investigator_prompt(tmp_path: Path):
    workflow = make_case(tmp_path)
    before = workflow.repository.load("case-01")
    first = build_prompt(run_input_from_case_state(before, preset_for_case(before)))
    after = before.model_copy(deep=True)
    after.title = "Changed title"
    after.description = "Changed description"
    after.assessment_context = AssessmentContext(assessment_id="assessment-2", title="Changed assessment", assessment_type="oral", venue="Room 99")
    second = build_prompt(run_input_from_case_state(after, preset_for_case(after)))
    assert second == first
    for administrative in ("Administrative title", "Administrative description", "Administrative assessment", "Changed title", "Changed description", "Room 99"):
        assert administrative not in first
    assert "APPLICABLE POLICY / CONFIGURED VIOLATIONS" in first
    assert "ADMITTED EVIDENCE SOURCES" in first
    assert "Observed record" in first


def test_semantic_inputs_change_for_evidence_student_and_policy(tmp_path: Path):
    workflow = make_case(tmp_path)
    state = workflow.repository.load("case-01")
    baseline = build_prompt(run_input_from_case_state(state, preset_for_case(state)))
    evidence = state.model_copy(deep=True)
    evidence.sources["S1"].content = "A different observation"
    assert build_prompt(run_input_from_case_state(evidence, preset_for_case(evidence))) != baseline
    student = state.model_copy(deep=True)
    student.subjects["subject_A"].display_name = "Candidate Renamed"
    assert build_prompt(run_input_from_case_state(student, preset_for_case(student))) != baseline
    from investigator.vnext.models import AssessmentRulePreset, ViolationDefinition
    custom = AssessmentRulePreset(preset_id="different-policy", violations=[ViolationDefinition(violation_id="new", label="New policy", rule_text="New rule", prohibited_conduct="New conduct")])
    assert build_prompt(run_input_from_case_state(state, custom)) != baseline


def test_title_update_is_administrative_and_sample_relationship_is_source_backed(tmp_path: Path):
    workflow = make_case(tmp_path)
    state = workflow.repository.load("case-01")
    before_revision = state.revision
    workflow.update_case_title("case-01", "  Renamed case  ")
    updated = workflow.repository.load("case-01")
    assert updated.title == "Renamed case"
    assert updated.revision == before_revision
    assert updated.administrative_revision == 1
    assert updated.administrative_activity == [{"type": "case_name_updated", "human_summary": "Case name updated."}]

    seed_sample_case(workflow, "multi-candidate", "multi-candidate-working")
    sample_state = workflow.repository.load("multi-candidate-working")
    relationship = sample_state.subject_relationships["rel_A_B_adjacent"]
    assert relationship.subject_ids == ["subject_A", "subject_B"]
    assert len(relationship.source_ids) == 1
    assert sample_state.sources[relationship.source_ids[0]].name == "seating_plan.md"
    assert "guilt" not in relationship.description.lower()
    with pytest.raises(Exception):
        workflow.update_case_title("multi-candidate-working", "Changed sample")


def test_unsourced_relationship_is_rejected_from_normal_vnext_input(tmp_path: Path):
    workflow = make_case(tmp_path)
    state = workflow.repository.load("case-01")
    state.subjects["subject_B"] = AssessmentSubject(subject_id="subject_B", display_name="Candidate B")
    state.subject_relationships["AB"] = SubjectRelationship(relationship_id="AB", subject_ids=["subject_A", "subject_B"], relationship_type="adjacent")
    with pytest.raises(ValueError, match="source provenance"):
        run_input_from_case_state(state, preset_for_case(state))


def _request(base: str, method: str, path: str, payload: dict | None = None):
    body = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(base + path, data=body, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def test_case_name_http_update_preserves_substantive_revision_and_sample_read_only(tmp_path: Path):
    workflow = HumanEvidenceWorkflow(CaseRepository(tmp_path / "cases"), run_mode="vnext")
    create_case(workflow, {"title": "Original"})
    server = create_server(tmp_path / "cases", port=0, run_callback=lambda *_: None, run_mode="vnext")
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        status, payload = _request(base, "PATCH", "/api/cases/case-000001", {"title": "  Updated  "})
        assert status == 200 and payload["workspace"]["title"] == "Updated" and payload["workspace"]["caseRevision"] == 0
        status, _ = _request(base, "PATCH", "/api/cases/case-000001", {"title": "   "})
        assert status == 422
        status, _ = _request(base, "PATCH", "/api/cases/case-000001", {"title": "x" * 161})
        assert status == 422
        status, _ = _request(base, "PATCH", "/api/cases/missing", {"title": "Nope"})
        assert status == 404
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)
