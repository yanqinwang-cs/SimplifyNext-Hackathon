import time

import pytest

from investigator.graph import GraphNodeType, GraphScope, GraphScopeType
from investigator.models import AssessmentContext, AssessmentSubject, Source, SourceType, SubjectRelationship
from investigator.llm import ModelCallMetadata, ModelCallResult
from investigator.services.evidence_requests import HumanEvidenceWorkflow
from investigator.state import CaseRepository, CaseState
from investigator.services import vnext_runner as production_runner_module
from investigator.services.vnext_runner import VNextProductionRunner
from investigator.vnext.source_applicability import build_source_applicability
from investigator.vnext.models import FurthestJustifiedConclusion, InvestigatorAssessment, InvestigatorProposal, SubjectAssessment, ViolationAssessment
from investigator.vnext import AssessmentRulePreset, AssessmentStatus, Confidence, ViolationDefinition, VNextRunInput
from investigator.vnext.runner import VNextInvestigationRunner
from investigator.vnext.semantic import (
    InvestigatorSemanticAssessment,
    SemanticItem,
    SemanticItemKind,
    SemanticSubjectAssessment,
    SemanticValidationError,
    SemanticViolationAssessment,
    compile_semantic_assessment,
)
from investigator.vnext.model import build_prompt


def _input() -> VNextRunInput:
    subjects = {
        "subject_A": AssessmentSubject(subject_id="subject_A", display_name="Candidate A"),
        "subject_B": AssessmentSubject(subject_id="subject_B", display_name="Candidate B"),
    }
    sources = {
        "S1": Source(id="S1", name="A record", source_type=SourceType.DOCUMENT, content="Candidate A record"),
        "S2": Source(id="S2", name="B record", source_type=SourceType.DOCUMENT, content="Candidate B record"),
        "S3": Source(id="S3", name="Joint record", source_type=SourceType.DOCUMENT, content="Candidate A and Candidate B record"),
    }
    relationships = {
        "rel_AB": SubjectRelationship(relationship_id="rel_AB", subject_ids=["subject_A", "subject_B"], relationship_type="observed_link", source_ids=["S3"]),
    }
    return VNextRunInput(
        case_id="case-compiler",
        assessment_context=AssessmentContext(assessment_id="assessment-compiler"),
        subjects=subjects,
        subject_relationships=relationships,
        sources=sources,
        rule_preset=AssessmentRulePreset(preset_id="preset", violations=[ViolationDefinition(violation_id="V1", label="Rule", rule_text="Rule", prohibited_conduct="Conduct")]),
    )


def _assessment(*, invalid_support: str | None = None) -> InvestigatorSemanticAssessment:
    support_ref = invalid_support or "e_a"
    return InvestigatorSemanticAssessment(
        semantic_items=[
            SemanticItem(local_ref="e_a", kind=SemanticItemKind.EVIDENCE_STATEMENT, statement="A record says X.", basis_source_ids=["S1"]),
            SemanticItem(local_ref="e_b", kind=SemanticItemKind.EVIDENCE_STATEMENT, statement="B record says Y.", basis_source_ids=["S2"]),
            SemanticItem(local_ref="e_joint", kind=SemanticItemKind.EVIDENCE_STATEMENT, statement="A joint record says Z.", basis_source_ids=["S3"]),
            SemanticItem(local_ref="alt_a", kind=SemanticItemKind.HYPOTHESIS, statement="An alternative explanation for A.", about_subject_ids=["subject_A"]),
        ],
        subject_assessments=[
            SemanticSubjectAssessment(subject_id="subject_A", violation_assessments=[SemanticViolationAssessment(violation_id="V1", status=AssessmentStatus.SUPPORTED, supporting_item_refs=[support_ref], limiting_item_refs=["e_joint"], alternative_item_refs=["alt_a"], reasoning_summary="A bounded semantic conclusion.", confidence=Confidence.MODERATE)], furthest_conclusion={"statement": "The record supports a bounded assessment.", "confidence": "moderate"}),
            SemanticSubjectAssessment(subject_id="subject_B", violation_assessments=[SemanticViolationAssessment(violation_id="V1", status=AssessmentStatus.NOT_CURRENTLY_SUPPORTED, reasoning_summary="The record does not currently support the assessment.", confidence=Confidence.LOW)], furthest_conclusion={"statement": "The record remains insufficient.", "confidence": "low"}),
        ],
    )


def test_valid_semantic_assessment_compiles_and_warden_applies() -> None:
    run_input = _input()
    compiled = compile_semantic_assessment(_assessment(), run_input)
    result = VNextInvestigationRunner(lambda _: compiled).run(run_input)
    assert result.subject_assessments[0].violation_assessments[0].status is AssessmentStatus.SUPPORTED
    assert all(node.node_type is not GraphNodeType.SOURCE for node in result.graph.nodes.values() if node.id.startswith("H"))
    assert any(edge.relation.value == "supports" for edge in result.graph.edges.values())


def test_private_source_cross_student_and_joint_private_stitching_are_rejected() -> None:
    run_input = _input()
    private_misuse = _assessment(invalid_support="e_bad").model_copy(update={
        "semantic_items": _assessment().semantic_items + [SemanticItem(local_ref="e_bad", kind=SemanticItemKind.EVIDENCE_STATEMENT, statement="Misapplied record.", basis_source_ids=["S2"])],
    })
    with pytest.raises(SemanticValidationError, match="legal scope"):
        compile_semantic_assessment(private_misuse, run_input)
    joint_private = _assessment().model_copy(update={
        "semantic_items": _assessment().semantic_items[:2] + [SemanticItem(local_ref="joint", kind=SemanticItemKind.PROPOSITION, statement="Joint conclusion.", about_subject_ids=["subject_A", "subject_B"], basis_item_refs=["e_a", "e_b"])],
    })
    with pytest.raises(SemanticValidationError, match=r"semantic_items\[2\].*Keep separate subject-scoped propositions") as caught:
        compile_semantic_assessment(joint_private, run_input)
    assert "e_a -> subject subject_A" in str(caught.value)
    assert "e_b -> subject subject_B" in str(caught.value)


def test_mixed_private_sources_in_one_evidence_item_require_split() -> None:
    item = SemanticItem(
        local_ref="mixed_observation",
        kind=SemanticItemKind.EVIDENCE_STATEMENT,
        statement="A combined observation.",
        basis_source_ids=["S1", "S2"],
    )
    assessment = _assessment().model_copy(update={"semantic_items": _assessment().semantic_items + [item]})
    with pytest.raises(SemanticValidationError, match=r"semantic_items\[4\].*Split this into separate semantic items") as caught:
        compile_semantic_assessment(assessment, _input())
    assert "S1 -> subject subject_A" in str(caught.value)
    assert "S2 -> subject subject_B" in str(caught.value)


def test_same_subject_sources_can_share_one_evidence_item() -> None:
    run_input = _input()
    same_scope_source = run_input.sources["S1"].model_copy(update={"id": "S1_COPY", "name": "A second record"})
    sources = {**run_input.sources, "S1_COPY": same_scope_source}
    run_input = run_input.model_copy(update={
        "sources": sources,
        "source_applicability": build_source_applicability(sources, run_input.subjects, run_input.subject_relationships),
    })
    item = SemanticItem(
        local_ref="two_a_records",
        kind=SemanticItemKind.EVIDENCE_STATEMENT,
        statement="Two records concern Candidate A.",
        basis_source_ids=["S1", "S1_COPY"],
    )
    assessment = _assessment().model_copy(update={"semantic_items": _assessment().semantic_items + [item]})
    compiled = compile_semantic_assessment(assessment, run_input)
    evidence = next(update for update in compiled.proposal.graph_updates if getattr(update, "local_ref", None) == "two_a_records")
    assert evidence.scope.scope_type is GraphScopeType.SUBJECT
    assert evidence.scope.subject_id == "subject_A"


def test_same_relationship_items_can_form_relationship_proposition() -> None:
    item = SemanticItem(
        local_ref="joint_proposition",
        kind=SemanticItemKind.PROPOSITION,
        statement="The joint record supports a bounded proposition.",
        about_subject_ids=["subject_A", "subject_B"],
        basis_item_refs=["e_joint"],
    )
    assessment = _assessment().model_copy(update={"semantic_items": _assessment().semantic_items + [item]})
    compiled = compile_semantic_assessment(assessment, _input())
    proposition = next(update for update in compiled.proposal.graph_updates if getattr(update, "local_ref", None) == "joint_proposition")
    assert proposition.scope.scope_type is GraphScopeType.RELATIONSHIP


def test_evidence_scope_is_sticky_to_source_not_mentions() -> None:
    run_input = _input()
    source = run_input.sources["S1"].model_copy(update={
        "content": "Candidate A and Candidate B are mentioned in this record.",
        "metadata": {"assessment_scope": {"scope_type": "subject", "subject_id": "subject_A"}},
    })
    sources = {**run_input.sources, "S1": source}
    run_input = run_input.model_copy(update={
        "sources": sources,
        "source_applicability": build_source_applicability(sources, run_input.subjects, run_input.subject_relationships),
    })
    item = SemanticItem(
        local_ref="e_mentions_b",
        kind=SemanticItemKind.EVIDENCE_STATEMENT,
        statement="The record describes an event involving Candidate B.",
        basis_source_ids=["S1"],
    )
    assessment = _assessment().model_copy(update={
        "semantic_items": _assessment().semantic_items + [item],
        "subject_assessments": [
            _assessment().subject_assessments[0].model_copy(update={
                "violation_assessments": [
                    _assessment().subject_assessments[0].violation_assessments[0].model_copy(update={"supporting_item_refs": ["e_mentions_b"]})
                ]
            }),
            _assessment().subject_assessments[1],
        ],
    })
    compiled = compile_semantic_assessment(assessment, run_input)
    evidence = next(update for update in compiled.proposal.graph_updates if getattr(update, "local_ref", None) == "e_mentions_b")
    assert evidence.scope.scope_type is GraphScopeType.SUBJECT
    assert evidence.scope.subject_id == "subject_A"


def test_evidence_scope_cannot_be_widened_to_mentioned_student() -> None:
    assessment = _assessment().model_copy(update={
        "subject_assessments": [
            _assessment().subject_assessments[0],
            _assessment().subject_assessments[1].model_copy(update={
                "violation_assessments": [
                    _assessment().subject_assessments[1].violation_assessments[0].model_copy(update={"supporting_item_refs": ["e_a"], "status": AssessmentStatus.SUPPORTED})
                ]
            }),
        ]
    })
    with pytest.raises(SemanticValidationError, match="legal scope"):
        compile_semantic_assessment(assessment, _input())


def test_relationship_scoped_source_is_inherited_by_evidence_item() -> None:
    compiled = compile_semantic_assessment(_assessment(), _input())
    evidence = next(update for update in compiled.proposal.graph_updates if getattr(update, "local_ref", None) == "e_joint")
    assert evidence.scope.scope_type is GraphScopeType.RELATIONSHIP


def test_hypothesis_cannot_be_supporting_material() -> None:
    assessment = _assessment().model_copy(update={
        "subject_assessments": [_assessment().subject_assessments[0].model_copy(update={"violation_assessments": [_assessment().subject_assessments[0].violation_assessments[0].model_copy(update={"supporting_item_refs": ["alt_a"]})]}), _assessment().subject_assessments[1]],
    })
    with pytest.raises(SemanticValidationError, match="Required kind"):
        compile_semantic_assessment(assessment, _input())


def test_duplicate_semantic_definition_reports_both_locations() -> None:
    item = SemanticItem(local_ref="alt_a", kind=SemanticItemKind.HYPOTHESIS, statement="Alternative.", about_subject_ids=["subject_A"])
    assessment = _assessment().model_copy(update={"semantic_items": _assessment().semantic_items + [item]})
    with pytest.raises(SemanticValidationError, match=r"semantic_items\[3\].*semantic_items\[4\]"):
        compile_semantic_assessment(assessment, _input())


def test_same_semantic_definition_can_be_referenced_multiple_times() -> None:
    assessment = _assessment().model_copy(update={
        "subject_assessments": [
            _assessment().subject_assessments[0].model_copy(update={
                "violation_assessments": [
                    _assessment().subject_assessments[0].violation_assessments[0].model_copy(update={
                        "alternative_item_refs": ["alt_a", "alt_a"],
                    })
                ]
            }),
            _assessment().subject_assessments[1],
        ]
    })
    compiled = compile_semantic_assessment(assessment, _input())
    assert len(compiled.subject_assessments[0].alternative_explanations) == 1


def test_unknown_reference_reports_exact_location() -> None:
    assessment = _assessment().model_copy(update={
        "subject_assessments": [
            _assessment().subject_assessments[0].model_copy(update={
                "violation_assessments": [
                    _assessment().subject_assessments[0].violation_assessments[0].model_copy(update={"alternative_item_refs": ["missing"]})
                ]
            }),
            _assessment().subject_assessments[1],
        ]
    })
    with pytest.raises(SemanticValidationError, match=r"subject_assessments\[0\]\.violation_assessments\[0\]\.alternative_item_refs\[0\]"):
        compile_semantic_assessment(assessment, _input())


def test_wrong_kind_reference_reports_definition_and_required_kind() -> None:
    assessment = _assessment().model_copy(update={
        "subject_assessments": [
            _assessment().subject_assessments[0].model_copy(update={
                "violation_assessments": [
                    _assessment().subject_assessments[0].violation_assessments[0].model_copy(update={"alternative_item_refs": ["e_a"]})
                ]
            }),
            _assessment().subject_assessments[1],
        ]
    })
    with pytest.raises(SemanticValidationError, match=r"semantic_items\[0\].*Actual kind: evidence_statement.*Required kind: hypothesis"):
        compile_semantic_assessment(assessment, _input())


def test_basis_reference_resolves_through_symbol_table_without_array_order_requirement() -> None:
    assessment = _assessment().model_copy(update={
        "semantic_items": [
            SemanticItem(local_ref="p_a", kind=SemanticItemKind.PROPOSITION, statement="A derived proposition.", about_subject_ids=["subject_A"], basis_item_refs=["e_a"]),
            *_assessment().semantic_items,
        ]
    })
    compiled = compile_semantic_assessment(assessment, _input())
    assert any(getattr(update, "local_ref", None) == "p_a" for update in compiled.proposal.graph_updates)


def test_semantic_schema_has_only_one_definition_table() -> None:
    assert "alternative_explanations" not in InvestigatorSemanticAssessment.model_fields


def test_unscoped_upload_uses_identifier_permissions_not_case_trust(tmp_path) -> None:
    workflow = HumanEvidenceWorkflow(CaseRepository(tmp_path / "cases"), run_mode="vnext")
    state = CaseState(case_id="case-01", title="Upload boundary")
    state.subjects = {
        "subject_A": AssessmentSubject(subject_id="subject_A", display_name="Candidate A"),
        "subject_B": AssessmentSubject(subject_id="subject_B", display_name="Candidate B"),
    }
    workflow.repository.save(state)
    source = workflow.add_direct_source("case-01", display_name="candidate-a.md", content="Candidate A record", source_type=SourceType.DOCUMENT)
    assert "assessment_scope" not in source.metadata
    applicability = build_source_applicability(workflow.ensure_case("case-01").sources, state.subjects, {})[source.id]
    assert applicability.permitted_subject_ids == ["subject_A"]
    assert applicability.case_shared_allowed is False


def test_trusted_subject_source_does_not_widen_from_identifier_mentions() -> None:
    source = Source(
        id="S4",
        name="trusted.md",
        source_type=SourceType.DOCUMENT,
        content="Candidate A and Candidate B are mentioned.",
        metadata={"assessment_scope": {"scope_type": "subject", "subject_id": "subject_A"}},
    )
    applicability = build_source_applicability({"S4": source}, _input().subjects, {})["S4"]
    assert applicability.identifier_mentions == ["subject_A", "subject_B"]
    assert applicability.permitted_subject_ids == ["subject_A"]


def test_multiple_identifier_mentions_do_not_create_student_permissions() -> None:
    source = Source(
        id="S5",
        name="ambiguous.md",
        source_type=SourceType.DOCUMENT,
        content="Candidate A and Candidate B are mentioned.",
    )
    applicability = build_source_applicability({"S5": source}, _input().subjects, {})["S5"]
    assert applicability.identifier_mentions == ["subject_A", "subject_B"]
    assert applicability.permitted_subject_ids == []
    assert applicability.case_shared_allowed is False


def test_limiting_material_is_not_compiled_as_conflict() -> None:
    compiled = compile_semantic_assessment(_assessment(), _input())
    operations = compiled.proposal.graph_updates
    assert not any(getattr(item, "operation", None) == "add_conflict" and getattr(item, "source_node_id", None) == "e_joint" for item in operations)


def test_semantic_prompt_excludes_graph_programming_contract() -> None:
    prompt = build_prompt(_input()).lower()
    for forbidden in ("graph node", "graph scope", "relationship_ref", "operations", "operationspec", "warden", "edge direction"):
        assert forbidden not in prompt


def test_semantic_prompt_states_sticky_evidence_scope_contract() -> None:
    prompt = build_prompt(_input())
    assert "inherit legal scope from their cited source" in prompt
    assert "who is mentioned in a source does not change" in prompt
    assert "cannot combine incompatible private student scopes" in prompt
    assert "create separate semantic items for A and B" in prompt
    assert "Do not summarize separate A and B sources into one evidence_statement" in prompt


def test_semantic_validation_retry_constraint_is_concrete() -> None:
    error = SemanticValidationError(
        "Evidence statement 'ev_a' cites source scope subject subject_A but is used for subject_B",
        retry_constraint="Evidence statement 'ev_a' cites a source scoped to subject subject_A. Do not declare this evidence statement as multi-student.",
    )
    constraints = VNextProductionRunner._semantic_validation_retry_constraints(error)
    assert any("ev_a" in constraint and "subject subject_A" in constraint for constraint in constraints)
    assert not any("prior model" in constraint.lower() for constraint in constraints)


def test_semantic_scope_failure_gets_one_clean_retry_with_concrete_constraint(tmp_path) -> None:
    run_input = _input()
    mixed_item = SemanticItem(
        local_ref="mixed_retry_item",
        kind=SemanticItemKind.EVIDENCE_STATEMENT,
        statement="A mixed private observation.",
        basis_source_ids=["S1", "S2"],
    )
    invalid = _assessment().model_copy(update={
        "semantic_items": _assessment().semantic_items + [mixed_item],
        "subject_assessments": [
            _assessment().subject_assessments[0].model_copy(update={
                "violation_assessments": [
                    _assessment().subject_assessments[0].violation_assessments[0].model_copy(update={"supporting_item_refs": ["mixed_retry_item"]})
                ]
            }),
            _assessment().subject_assessments[1],
        ]
    })

    class Client:
        def __init__(self) -> None:
            self.responses = [invalid, _assessment()]
            self.prompts: list[str] = []

        def call(self, prompt, schema):
            self.prompts.append(str(prompt))
            response = self.responses.pop(0)
            return ModelCallResult(
                parsed=schema.model_validate(response.model_dump(mode="python")),
                metadata=ModelCallMetadata(provider="offline", model="fixture", latency_seconds=0.001, parse_success=True),
                raw_output=response.model_dump(mode="json"),
            )

    workflow = HumanEvidenceWorkflow(CaseRepository(tmp_path / "cases"), run_mode="vnext")
    state = CaseState(case_id="case-01", title="Semantic scope retry")
    state.subjects = run_input.subjects
    state.subject_relationships = run_input.subject_relationships
    state.sources = run_input.sources
    workflow.repository.save(state)
    client = Client()
    workflow.run_callback = VNextProductionRunner(client, preset_resolver=lambda _: run_input.rule_preset).run
    workflow.start_run("case-01")
    for _ in range(100):
        if workflow.ensure_case("case-01").runtime_status in {"COMPLETED", "FAILED"}:
            break
        time.sleep(0.01)

    assert workflow.ensure_case("case-01").runtime_status == "COMPLETED"
    assert len(client.prompts) == 2
    assert "Split them into separate semantic items" in client.prompts[1]
    validation = next(item for item in workflow.get_traces("case-01") if item["event"] == "vnext_semantic_validation_failed")
    assert any("Split them into separate semantic items" in item for item in validation["retry_constraints"])


def test_normal_semantic_warden_failure_is_terminal_without_model_retry(tmp_path, monkeypatch) -> None:
    workflow = HumanEvidenceWorkflow(CaseRepository(tmp_path / "cases"), run_mode="vnext")
    state = CaseState(case_id="case-01", title="Warden boundary")
    state.subjects = {"subject_A": AssessmentSubject(subject_id="subject_A", display_name="Candidate A")}
    state.sources = {"S1": Source(id="S1", name="record.md", source_type=SourceType.DOCUMENT, content="Candidate A record")}
    workflow.repository.save(state)

    class SemanticClient:
        calls = 0

        def call(self, prompt, schema):
            self.calls += 1
            response = InvestigatorSemanticAssessment(
                subject_assessments=[SemanticSubjectAssessment(
                    subject_id="subject_A",
                    violation_assessments=[SemanticViolationAssessment(violation_id="V1", status=AssessmentStatus.NOT_CURRENTLY_SUPPORTED, reasoning_summary="Insufficient record.", confidence=Confidence.LOW)],
                    furthest_conclusion={"statement": "No finding is currently supported.", "confidence": "low"},
                )],
            )
            return ModelCallResult(parsed=schema.model_validate(response.model_dump()), metadata=ModelCallMetadata(provider="offline", model="fixture", latency_seconds=0.001, parse_success=True), raw_output=response.model_dump(mode="json"))

    invalid = InvestigatorAssessment(
        proposal=InvestigatorProposal(graph_updates=[{"operation": "add_derivation", "derived_proposition_id": "p_missing", "source_node_id": "e_missing", "reason": "Invalid fixture."}]),
        subject_assessments=[SubjectAssessment(subject_id="subject_A", violation_assessments=[ViolationAssessment(violation_id="V1", status=AssessmentStatus.NOT_CURRENTLY_SUPPORTED, reasoning_summary="Insufficient record.", confidence=Confidence.LOW)], furthest_conclusion=FurthestJustifiedConclusion(statement="No finding is currently supported.", confidence=Confidence.LOW))],
    )
    monkeypatch.setattr(production_runner_module, "compile_semantic_assessment", lambda *_args, **_kwargs: invalid)
    client = SemanticClient()
    workflow.run_callback = VNextProductionRunner(client, preset_resolver=lambda _: _input().rule_preset).run
    workflow.start_run("case-01")
    for _ in range(100):
        if workflow.ensure_case("case-01").runtime_status == "FAILED":
            break
        import time
        time.sleep(0.01)
    assert workflow.ensure_case("case-01").runtime_status == "FAILED"
    assert client.calls == 1


def test_kind_specific_union_rejects_wrong_fields_and_unknown_kind() -> None:
    with pytest.raises(Exception):
        InvestigatorSemanticAssessment.model_validate({
            "semantic_items": [{
                "local_ref": "e1",
                "kind": "evidence_statement",
                "statement": "A source statement.",
                "basis_source_ids": ["S1"],
                "basis_item_refs": ["p1"],
            }],
            "subject_assessments": [],
        })
    with pytest.raises(Exception):
        InvestigatorSemanticAssessment.model_validate({
            "semantic_items": [{
                "local_ref": "u1",
                "kind": "uncertainty",
                "statement": "An unresolved point.",
            }],
            "subject_assessments": [],
        })


def test_proposition_and_hypothesis_contracts_are_strict() -> None:
    with pytest.raises(Exception):
        SemanticItem(
            local_ref="p1",
            kind=SemanticItemKind.PROPOSITION,
            statement="A proposition.",
            about_subject_ids=["subject_A"],
            basis_source_ids=["S1"],
            basis_item_refs=["e_a"],
        )
    with pytest.raises(Exception):
        SemanticItem(
            local_ref="h1",
            kind=SemanticItemKind.HYPOTHESIS,
            statement="A hypothesis.",
            about_subject_ids=["subject_A"],
            basis_item_refs=["e_a"],
        )


def test_multi_student_hypothesis_requires_and_accepts_existing_relationship() -> None:
    assessment = _assessment().model_copy(update={
        "semantic_items": _assessment().semantic_items + [SemanticItem(
            local_ref="h_joint",
            kind=SemanticItemKind.HYPOTHESIS,
            statement="A relationship-level alternative explanation.",
            about_subject_ids=["subject_A", "subject_B"],
        )],
    })
    compiled = compile_semantic_assessment(assessment, _input())
    hypothesis = next(update for update in compiled.proposal.graph_updates if getattr(update, "local_ref", None) == "h_joint")
    assert hypothesis.scope.scope_type is GraphScopeType.RELATIONSHIP


def test_unresolved_points_compile_to_deterministic_uncertainty_nodes() -> None:
    assessment = _assessment().model_copy(update={
        "subject_assessments": [
            _assessment().subject_assessments[0].model_copy(update={
                "violation_assessments": [
                    _assessment().subject_assessments[0].violation_assessments[0].model_copy(update={
                        "unresolved_points": ["Whether the record reflects direct access."],
                    })
                ]
            }),
            _assessment().subject_assessments[1],
        ]
    })
    compiled = compile_semantic_assessment(assessment, _input())
    uncertainty = next(update for update in compiled.proposal.graph_updates if getattr(update, "operation", None) == "add_uncertainty")
    assert uncertainty.target_node_id == "evaluation_subject_a_v1"
    assert uncertainty.node_id == "U1"


def test_violation_rules_require_support_and_conflict_but_allow_limiting_support() -> None:
    conflicted = _assessment().model_copy(update={
        "subject_assessments": [
            _assessment().subject_assessments[0].model_copy(update={
                "violation_assessments": [
                    _assessment().subject_assessments[0].violation_assessments[0].model_copy(update={
                        "status": AssessmentStatus.CONFLICTED,
                        "conflicting_item_refs": ["e_joint"],
                    })
                ]
            }),
            _assessment().subject_assessments[1],
        ]
    })
    compile_semantic_assessment(conflicted, _input())

    supported_material = _assessment().model_copy(update={
        "subject_assessments": [
            _assessment().subject_assessments[0],
            _assessment().subject_assessments[1].model_copy(update={
                "violation_assessments": [
                    _assessment().subject_assessments[1].violation_assessments[0].model_copy(update={
                        "status": AssessmentStatus.NOT_CURRENTLY_SUPPORTED,
                        "supporting_item_refs": ["e_b"],
                        "limiting_item_refs": ["e_joint"],
                    })
                ]
            }),
        ]
    })
    compile_semantic_assessment(supported_material, _input())
