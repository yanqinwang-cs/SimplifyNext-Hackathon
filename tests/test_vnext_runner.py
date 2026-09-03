import pytest

from investigator.graph import CaseGraph, GraphNode, GraphNodeType
from investigator.models.source import Source, SourceType
from investigator.state.case_state import CaseState
from investigator.vnext import (
    AssessmentRulePreset,
    AssessmentStatus,
    Confidence,
    FurthestJustifiedConclusion,
    InvestigatorAssessment,
    InvestigatorProposal,
    VNextInvestigationRunner,
    VNextRunInput,
    VNextRunResult,
    VNextRunStatus,
    VNextRunValidationError,
    ViolationAssessment,
    ViolationDefinition,
    WardenValidationError,
    run_input_from_case_state,
)


def source(source_id: str = "S1", content: str = "A record") -> Source:
    return Source(id=source_id, name=f"record-{source_id}", source_type=SourceType.DOCUMENT, content=content)


def preset(*violation_ids: str) -> AssessmentRulePreset:
    return AssessmentRulePreset(
        preset_id="test-preset",
        violations=[
            ViolationDefinition(
                violation_id=identifier,
                label=f"Rule {identifier}",
                rule_text="The configured rule.",
                prohibited_conduct="The prohibited conduct.",
            )
            for identifier in violation_ids
        ],
    )


def assessment(
    violation_ids: list[str],
    *,
    proposal: InvestigatorProposal | None = None,
    statuses: dict[str, AssessmentStatus] | None = None,
    supporting: dict[str, list[str]] | None = None,
    mitigating: dict[str, list[str]] | None = None,
) -> InvestigatorAssessment:
    statuses = statuses or {}
    supporting = supporting or {}
    mitigating = mitigating or {}
    return InvestigatorAssessment(
        proposal=proposal or InvestigatorProposal(),
        violation_assessments=[
            ViolationAssessment(
                violation_id=identifier,
                status=statuses.get(identifier, AssessmentStatus.NOT_CURRENTLY_SUPPORTED),
                supporting_node_ids=supporting.get(identifier, []),
                mitigating_node_ids=mitigating.get(identifier, []),
                unresolved_points=[],
                reasoning_summary="Bounded assessment.",
                confidence=Confidence.MODERATE,
            )
            for identifier in violation_ids
        ],
        furthest_conclusion=FurthestJustifiedConclusion(
            statement="The furthest justified conclusion remains bounded.",
            based_on_violation_ids=[],
            confidence=Confidence.MODERATE,
        ),
    )


def run_input(sources: dict[str, Source], *violation_ids: str) -> VNextRunInput:
    return VNextRunInput(case_id="case-01", sources=sources, rule_preset=preset(*violation_ids))


def test_sparse_run_evaluates_every_violation_once_and_ends() -> None:
    calls: list[VNextRunInput] = []

    def investigator(inputs: VNextRunInput) -> InvestigatorAssessment:
        calls.append(inputs)
        return assessment([item.violation_id for item in inputs.rule_preset.violations])

    result = VNextInvestigationRunner(investigator).run(run_input({}, "V1", "V2", "V3", "V4"))

    assert len(calls) == 1
    assert [item.violation_id for item in result.violation_assessments] == ["V1", "V2", "V3", "V4"]
    assert all(item.status is AssessmentStatus.NOT_CURRENTLY_SUPPORTED for item in result.violation_assessments)
    assert result.status is VNextRunStatus.COMPLETED
    assert "innocent" not in result.violation_assessments[0].reasoning_summary.lower()


def test_obvious_violation_can_stop_at_possession_without_downstream_fact() -> None:
    rule = preset("unauthorized_device")
    proposal = InvestigatorProposal.model_validate(
        {
            "graph_updates": [
                {
                    "operation": "add_evidence",
                    "local_ref": "possession",
                    "statement": "Student possessed prohibited smart eyewear.",
                    "source_ids": ["S1"],
                    "reason": "Record the direct possession observation.",
                }
            ]
        }
    )
    output = assessment(
        ["unauthorized_device"],
        proposal=proposal,
        statuses={"unauthorized_device": AssessmentStatus.SUPPORTED},
        supporting={"unauthorized_device": ["possession"]},
    )
    output = output.model_copy(
        update={
            "furthest_conclusion": FurthestJustifiedConclusion(
                statement="Prohibited device possession is supported; actual information access remains unresolved.",
                based_on_violation_ids=["unauthorized_device"],
                confidence=Confidence.HIGH,
            )
        }
    )
    result = VNextInvestigationRunner(lambda _: output).run(VNextRunInput(case_id="case-01", sources={"S1": source()}, rule_preset=rule))

    assert result.violation_assessments[0].status is AssessmentStatus.SUPPORTED
    assert result.violation_assessments[0].supporting_node_ids[0].startswith("node_")
    assert "unresolved" in result.furthest_conclusion.statement


def test_mitigating_evidence_is_preserved_separately() -> None:
    rule = preset("V1")
    proposal = InvestigatorProposal.model_validate(
        {
            "graph_updates": [
                {"operation": "add_evidence", "local_ref": "support", "statement": "Supporting record.", "source_ids": ["S1"], "reason": "Add support."},
                {"operation": "add_evidence", "local_ref": "mitigation", "statement": "Mitigating record.", "source_ids": ["S2"], "reason": "Add mitigation."},
            ]
        }
    )
    output = assessment(
        ["V1"], proposal=proposal, statuses={"V1": AssessmentStatus.CONFLICTED},
        supporting={"V1": ["support"]}, mitigating={"V1": ["mitigation"]},
    )
    result = VNextInvestigationRunner(lambda _: output).run(
        run_input({"S1": source(), "S2": source("S2", "A mitigating record")}, "V1")
    )
    item = result.violation_assessments[0]
    assert item.status is AssessmentStatus.CONFLICTED
    assert item.supporting_node_ids != item.mitigating_node_ids
    assert all(result.graph.nodes[node_id].node_type is GraphNodeType.EVIDENCE for node_id in item.supporting_node_ids + item.mitigating_node_ids)


def test_assessment_order_is_normalized_to_preset_order() -> None:
    rule = preset("V1", "V2")
    output = assessment(["V2", "V1"])
    result = VNextInvestigationRunner(lambda _: output).run(VNextRunInput(case_id="case-01", rule_preset=rule))
    assert [item.violation_id for item in result.violation_assessments] == ["V1", "V2"]


@pytest.mark.parametrize(
    "items, message",
    [(["V1", "V1"], "duplicate"), (["V1", "V999"], "missing=.*V2.*unknown=.*V999")],
)
def test_violation_sweep_rejects_duplicate_or_unknown_ids(items: list[str], message: str) -> None:
    output = assessment(items)
    with pytest.raises(VNextRunValidationError, match=message):
        VNextInvestigationRunner(lambda _: output).run(VNextRunInput(case_id="case-01", rule_preset=preset("V1", "V2")))


def test_unknown_assessment_node_is_rejected_after_warden() -> None:
    output = assessment(["V1"], supporting={"V1": ["P999"]})
    with pytest.raises(VNextRunValidationError, match="unknown graph node ID"):
        VNextInvestigationRunner(lambda _: output).run(VNextRunInput(case_id="case-01", rule_preset=preset("V1")))


def test_warden_rejection_fails_run_without_partial_result() -> None:
    proposal = InvestigatorProposal.model_validate(
        {"graph_updates": [{"operation": "add_evidence", "statement": "Bad provenance.", "source_ids": ["S999"], "reason": "This should fail."}]}
    )
    output = assessment(["V1"], proposal=proposal)
    with pytest.raises(WardenValidationError, match="unknown raw source ID"):
        VNextInvestigationRunner(lambda _: output).run(VNextRunInput(case_id="case-01", sources={"S1": source()}, rule_preset=preset("V1")))


def test_clean_run_discards_old_reasoning_but_retains_sources() -> None:
    old_state = CaseState(
        case_id="case-01",
        title="Case",
        sources={"S1": source()},
        reasoning_graph=CaseGraph(
            case_id="case-01",
            nodes={"P99": GraphNode(node_type="proposition", id="P99", statement="Stale reasoning")},
        ),
    )
    inputs = run_input_from_case_state(old_state, preset("V1"))
    assert inputs.sources["S1"].content == "A record"
    result = VNextInvestigationRunner(lambda _: assessment(["V1"])).run(inputs)
    assert "P99" not in result.graph.nodes
    assert result.graph.nodes["S1"].node_type is GraphNodeType.SOURCE


def test_two_clean_runs_use_new_persistent_evidence_without_inheriting_graph() -> None:
    first = run_input({"S1": source()}, "V1")
    second = run_input({"S1": source(), "S2": source("S2", "New decisive record")}, "V1")
    runner = VNextInvestigationRunner(lambda inputs: assessment(["V1"]))
    first_result = runner.run(first)
    second_result = runner.run(second)
    assert "S2" not in first_result.graph.nodes
    assert "S2" in second_result.graph.nodes
    assert set(first_result.graph.nodes) == {"S1"}
    assert set(second_result.graph.nodes) == {"S1", "S2"}


def test_duplicate_preset_ids_are_rejected() -> None:
    with pytest.raises(ValueError, match="unique"):
        preset("V1", "V1")


def test_vnext_output_has_no_legacy_control_flow_fields() -> None:
    fields = set(InvestigatorAssessment.model_fields) | set(VNextRunResult.model_fields)
    assert not fields.intersection({"next_step", "request_information", "stop_unresolved", "archive", "move_focus"})
