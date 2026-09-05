import json
from pathlib import Path
import threading
import urllib.error
import urllib.request

import pytest

from investigator.graph import CaseGraph, GraphNode, GraphNodeType, GraphScope, GraphScopeType, scopes_compatible
from investigator.http_api import create_case, create_server, seed_sample_case
from investigator.models.assessment import AssessmentSubject, SubjectRelationship
from investigator.models.source import Source, SourceType
from investigator.services.evidence_requests import HumanEvidenceWorkflow
from investigator.state import CaseRepository
from investigator.vnext import (
    AssessmentRulePreset,
    AssessmentStatus,
    Confidence,
    FurthestJustifiedConclusion,
    GraphWarden,
    InvestigatorAssessment,
    InvestigatorProposal,
    SubjectAssessment,
    VNextInvestigationRunner,
    VNextRunInput,
    ViolationAssessment,
    ViolationDefinition,
    build_source_applicability,
    deterministic_relationship_id,
    normalize_identifier,
)
from investigator.vnext.warden import WardenValidationError


def students() -> dict[str, AssessmentSubject]:
    return {
        "A": AssessmentSubject(subject_id="A", display_name="Candidate A", candidate_number="BL-041"),
        "B": AssessmentSubject(subject_id="B", display_name="Candidate B", candidate_number="BL-073"),
        "C": AssessmentSubject(subject_id="C", display_name="Candidate C", candidate_number="BL-118"),
    }


def source(source_id: str, name: str, content: str, *, scope: dict | None = None) -> Source:
    metadata = {"assessment_scope": scope} if scope is not None else {}
    return Source(id=source_id, name=name, source_type=SourceType.DOCUMENT, content=content, metadata=metadata)


def preset() -> AssessmentRulePreset:
    return AssessmentRulePreset(
        preset_id="scope-test",
        violations=[ViolationDefinition(violation_id="V1", label="Conduct", rule_text="r", prohibited_conduct="c")],
    )


def proposal(updates: list[dict], relationships: list[dict] | None = None) -> InvestigatorProposal:
    return InvestigatorProposal.model_validate({"graph_updates": updates, "relationship_scopes": relationships or []})


def assessment_for(*, proposal_value: InvestigatorProposal, refs: dict[str, list[str]], statuses: dict[str, AssessmentStatus] | None = None) -> InvestigatorAssessment:
    statuses = statuses or {student_id: AssessmentStatus.NOT_CURRENTLY_SUPPORTED for student_id in students()}
    return InvestigatorAssessment(
        proposal=proposal_value,
        subject_assessments=[
            SubjectAssessment(
                subject_id=student_id,
                violation_assessments=[ViolationAssessment(
                    violation_id="V1",
                    status=statuses[student_id],
                    supporting_node_ids=refs.get(student_id, []),
                    reasoning_summary="bounded assessment",
                    confidence=Confidence.HIGH,
                )],
                furthest_conclusion=FurthestJustifiedConclusion(statement="bounded conclusion", confidence=Confidence.HIGH),
            )
            for student_id in sorted(students())
        ],
    )


def run_input(sources: dict[str, Source]) -> VNextRunInput:
    return VNextRunInput(case_id="case-scope", sources=sources, subjects=students(), rule_preset=preset())


def test_identifier_matching_is_normalized_exact_and_never_creates_students() -> None:
    configured = {"A": AssessmentSubject(subject_id="A", display_name="Candidate A"), "B": AssessmentSubject(subject_id="B", display_name="Candidate B")}
    index = build_source_applicability({
        "S1": source("S1", "candidate-a_report.md", "The record names Candidate A."),
        "S2": source("S2", "candidate-A-and-B.md", "Candidate A and Candidate B are both named."),
        "S3": source("S3", "annex.md", "Candidate Annex only."),
    }, configured)
    assert normalize_identifier("Candidate-A") == "candidate a"
    assert index["S1"].matched_student_ids == ["A"]
    assert index["S2"].matched_student_ids == ["A", "B"]
    assert index["S3"].matched_student_ids == []
    assert index["S3"].classification.value == "case_shared"
    assert set(configured) == {"A", "B"}


def test_duplicate_normalized_configured_identifiers_are_rejected() -> None:
    with pytest.raises(ValueError, match="ambiguous"):
        build_source_applicability({}, {
            "A": AssessmentSubject(subject_id="A", display_name="Candidate-A"),
            "B": AssessmentSubject(subject_id="B", display_name="candidate A"),
        })


def test_single_student_default_and_trusted_subject_scope_are_internal_categories() -> None:
    one = {"A": AssessmentSubject(subject_id="A", display_name="Student 1")}
    default = build_source_applicability({"S1": source("S1", "notes.md", "No configured name")}, one)
    assert default["S1"].classification.value == "single_student_default"
    trusted = build_source_applicability({"S2": source("S2", "private.md", "private", scope={"scope_type": "subject", "subject_id": "A", "relationship_id": None, "relationship_ref": None})}, one)
    assert trusted["S2"].classification.value == "student_specific"
    assert trusted["S2"].basis == "trusted_internal_scope"


def test_relationship_is_run_local_deterministic_and_requires_one_joint_source() -> None:
    sources = {
        "S1": source("S1", "combined.md", "Candidate A and Candidate B were recorded together."),
        "S2": source("S2", "a.md", "Candidate A only."),
        "S3": source("S3", "b.md", "Candidate B only."),
    }
    relationship = {"local_ref": "R1", "student_ids": ["A", "B"], "basis_source_ids": ["S1"]}
    graph_proposal = proposal([
        {"operation": "add_evidence", "local_ref": "ab", "statement": "Jointly recorded material.", "source_ids": ["S1"], "scope": {"scope_type": "relationship", "relationship_ref": "R1"}, "reason": "record joint source"},
    ], [relationship])
    output = assessment_for(
        proposal_value=graph_proposal,
        refs={"A": ["ab"], "B": ["ab"]},
        statuses={"A": AssessmentStatus.SUPPORTED, "B": AssessmentStatus.PARTIALLY_SUPPORTED, "C": AssessmentStatus.NOT_CURRENTLY_SUPPORTED},
    )
    run = VNextInvestigationRunner(lambda _: output).run(run_input(sources))
    relationship_id = deterministic_relationship_id("case-scope", ["A", "B"])
    assert run.relationship_registry[relationship_id].subject_ids == ["A", "B"]
    assert run.metadata.relationship_scope_ids == {"R1": relationship_id}
    assert run_input(sources).subject_relationships == {}

    stitched = proposal([{"operation": "add_evidence", "local_ref": "ab", "statement": "stitched", "source_ids": ["S2"], "scope": {"scope_type": "relationship", "relationship_ref": "R1"}, "reason": "test"}], [
        {"local_ref": "R1", "student_ids": ["A", "B"], "basis_source_ids": ["S2", "S3"]},
    ])
    with pytest.raises(WardenValidationError, match="joint source"):
        GraphWarden(CaseGraph(case_id="case-scope", nodes={"S0": GraphNode(id="S0", node_type=GraphNodeType.SOURCE, statement="source")}, edges={}), sources, subjects=students()).apply(stitched)


@pytest.mark.parametrize("bad_declaration", [
    {"local_ref": "R1", "student_ids": ["A"], "basis_source_ids": ["S1"]},
    {"local_ref": "R1", "student_ids": ["A", "Z"], "basis_source_ids": ["S1"]},
    {"local_ref": "R1", "student_ids": ["A", "B"], "basis_source_ids": ["S9"]},
])
def test_relationship_declarations_reject_bad_participants_or_sources(bad_declaration: dict) -> None:
    sources = {"S1": source("S1", "combined.md", "Candidate A and Candidate B")}
    with pytest.raises((WardenValidationError, ValueError)):
        GraphWarden(CaseGraph(case_id="case-scope", nodes={"S0": GraphNode(id="S0", node_type=GraphNodeType.SOURCE, statement="source")}, edges={}), sources, subjects=students()).apply(proposal([
            {"operation": "add_evidence", "local_ref": "ab", "statement": "joint", "source_ids": ["S1"], "scope": {"scope_type": "relationship", "relationship_ref": "R1"}, "reason": "record"},
        ], [bad_declaration]))


def test_warden_is_atomic_for_invalid_relationship_batch_and_direct_model_id() -> None:
    sources = {"S1": source("S1", "combined.md", "Candidate A and Candidate B")}
    graph = CaseGraph(case_id="case-scope", nodes={"S0": GraphNode(id="S0", node_type=GraphNodeType.SOURCE, statement="source")}, edges={})
    warden = GraphWarden(graph, sources, subjects=students())
    before_graph = graph.model_dump(mode="json")
    with pytest.raises(WardenValidationError):
        warden.apply(proposal([
            {"operation": "add_evidence", "local_ref": "ab", "statement": "joint", "source_ids": ["S1"], "scope": {"scope_type": "relationship", "relationship_ref": "R1"}, "reason": "record"},
            {"operation": "add_support", "source_node_id": "missing", "target_node_id": "missing", "reason": "fail"},
        ], [{"local_ref": "R1", "student_ids": ["A", "B"], "basis_source_ids": ["S1"]}]))
    assert graph.model_dump(mode="json") == before_graph
    assert warden.subject_relationships == {}
    with pytest.raises(WardenValidationError, match="relationship_ref"):
        GraphWarden(graph, sources, subjects=students(), relationship_refs={"R1": "persisted"}, strict_relationship_refs=True).apply(proposal([
            {"operation": "add_hypothesis", "local_ref": "h", "statement": "h", "scope": {"scope_type": "relationship", "relationship_id": "persisted"}, "reason": "test"},
        ]))


def test_directional_scope_matrix_does_not_widen_private_material() -> None:
    relationship = {"AB": SubjectRelationship(relationship_id="AB", subject_ids=["A", "B"], relationship_type="joint", source_ids=["S1"])}
    case = GraphScope(scope_type=GraphScopeType.CASE)
    student_a = GraphScope(scope_type=GraphScopeType.SUBJECT, subject_id="A")
    student_b = GraphScope(scope_type=GraphScopeType.SUBJECT, subject_id="B")
    rel_ab = GraphScope(scope_type=GraphScopeType.RELATIONSHIP, relationship_id="AB")
    assert scopes_compatible(case, student_a, relationship)
    assert scopes_compatible(rel_ab, student_a, relationship)
    assert scopes_compatible(student_a, rel_ab, relationship) is False
    assert scopes_compatible(student_a, student_b, relationship) is False
    assert scopes_compatible(student_a, case, relationship) is False


def test_public_http_upload_cannot_supply_trusted_scope_or_relationship_endpoint(tmp_path: Path) -> None:
    workflow = HumanEvidenceWorkflow(CaseRepository(tmp_path / "cases"), run_mode="vnext")
    create_case(workflow, {"title": "Public case"})
    server = create_server(tmp_path / "cases", port=0, run_mode="vnext")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def request(path: str, payload: dict) -> int:
        body = json.dumps(payload).encode()
        try:
            with urllib.request.urlopen(urllib.request.Request(base + path, data=body, method="POST", headers={"Content-Type": "application/json"})) as response:
                return response.status
        except urllib.error.HTTPError as error:
            return error.code

    try:
        assert request("/api/cases/case-000001/sources", {"fileName": "x.md", "content": "x", "assessment_scope": {"scope_type": "subject", "subject_id": "A"}}) == 422
        assert request("/api/cases/case-000001/relationships", {}) == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_public_samples_keep_their_explicit_student_sets(tmp_path: Path) -> None:
    workflow = HumanEvidenceWorkflow(CaseRepository(tmp_path / "cases"), run_mode="vnext")
    seed_sample_case(workflow, "law-exam", "law-working")
    seed_sample_case(workflow, "multi-candidate", "multi-working")
    law = workflow.repository.load("law-working")
    multi = workflow.repository.load("multi-working")
    assert [item.display_name for item in law.subjects.values()] == ["Candidate A"]
    assert [item.display_name for item in multi.subjects.values()] == ["Candidate A", "Candidate B", "Candidate C", "Candidate D", "Candidate E"]
    assert len(law.sources) == 17
    assert len(multi.sources) == 14
