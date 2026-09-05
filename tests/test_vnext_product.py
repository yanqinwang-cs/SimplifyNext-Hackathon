import json
import time
from pathlib import Path

import pytest

from investigator.graph import GraphNodeType
from investigator.llm import ModelCallMetadata, ModelCallResult, ModelParseError
from investigator.models.source import Source, SourceType
from investigator.services.evidence_requests import HumanEvidenceWorkflow
from investigator.services.vnext_runner import VNextProductionRunner
from investigator.state import CaseRepository
from investigator.vnext import (
    AssessmentStatus,
    Confidence,
    FurthestJustifiedConclusion,
    InvestigatorAssessment,
    InvestigatorProposal,
    ViolationAssessment,
)


class SequenceClient:
    def __init__(self, responses: list[object]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[object, type[object]]] = []

    def call(self, prompt: object, schema: type[object]) -> ModelCallResult:
        self.calls.append((prompt, schema))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        parsed = response if isinstance(response, schema) else schema.model_validate(response)
        return ModelCallResult(
            parsed=parsed,
            metadata=ModelCallMetadata(
                provider="offline",
                model="fixture-model",
                input_tokens=12,
                output_tokens=8,
                latency_seconds=0.001,
                parse_success=True,
                finish_reason="stop",
            ),
            raw_output=parsed.model_dump(mode="json"),
        )


def source(source_id: str = "S1", content: str = "A source record") -> Source:
    return Source(id=source_id, name=source_id, source_type=SourceType.DOCUMENT, content=content)


def assessment(*, supported: bool = False, proposal: InvestigatorProposal | None = None) -> InvestigatorAssessment:
    return InvestigatorAssessment(
        proposal=proposal or InvestigatorProposal(),
        violation_assessments=[
            ViolationAssessment(
                violation_id=violation_id,
                status=AssessmentStatus.SUPPORTED if supported and violation_id == "unauthorized_device" else AssessmentStatus.NOT_CURRENTLY_SUPPORTED,
                supporting_node_ids=["device"] if supported and violation_id == "unauthorized_device" else [],
                mitigating_node_ids=[],
                unresolved_points=[],
                reasoning_summary="Bounded fixture assessment.",
                confidence=Confidence.HIGH,
            )
            for violation_id in (
                "unauthorized_device",
                "unauthorized_external_communication",
                "unauthorized_assistance",
                "prohibited_collaboration",
            )
        ],
        furthest_conclusion=FurthestJustifiedConclusion(
            statement="The configured assessment remains bounded.",
            based_on_violation_ids=["unauthorized_device"] if supported else [],
            confidence=Confidence.HIGH,
        ),
    )


def start_vnext(tmp_path: Path, client: SequenceClient) -> HumanEvidenceWorkflow:
    workflow = HumanEvidenceWorkflow(CaseRepository(tmp_path / "cases"), run_mode="vnext")
    state = workflow.ensure_case("case-01")
    state.sources["S1"] = source()
    workflow.repository.save(state)
    workflow.run_callback = VNextProductionRunner(client).run
    workflow.start_run("case-01")
    for _ in range(100):
        if workflow.get_workspace("case-01")["runtimeStatus"] in {"COMPLETED", "FAILED"}:
            break
        time.sleep(0.01)
    return workflow


def test_product_vnext_success_persists_result_and_never_requests_or_uses_steward(tmp_path: Path) -> None:
    proposal = InvestigatorProposal.model_validate(
        {"graph_updates": [{"operation": "add_evidence", "local_ref": "device", "statement": "Device present.", "source_ids": ["S1"], "reason": "Record the source observation."}]}
    )
    client = SequenceClient([assessment(supported=True, proposal=proposal)])
    workflow = start_vnext(tmp_path, client)

    workspace = workflow.get_workspace("case-01")
    run = workspace["runs"][0]
    artifact = tmp_path / "cases" / "case-01" / "runs" / run["run_id"]
    assert workspace["runtimeStatus"] == "COMPLETED"
    assert len(client.calls) == 1
    assert workspace["pendingEvidenceRequest"] is None
    assert run["vnext_status"] == "completed"
    assert run["model_calls"] == 1
    assert run["proposal_correction_calls"] == 0
    assert run["clean_execution_retries"] == 0
    assert (artifact / "vnext_result.json").is_file()
    assert json.loads((artifact / "vnext_result.json").read_text())["result"]["status"] == "completed"
    assert workflow.get_traces("case-01")[-1]["event"] == "vnext_completed"


def test_sparse_product_vnext_completes_with_zero_graph_updates(tmp_path: Path) -> None:
    client = SequenceClient([assessment()])
    workflow = start_vnext(tmp_path, client)
    result = json.loads((tmp_path / "cases" / "case-01" / "runs" / "run_000001" / "vnext_result.json").read_text())
    assert workflow.get_workspace("case-01")["runtimeStatus"] == "COMPLETED"
    assert result["result"]["metadata"]["proposal_update_count"] == 0
    run = workflow.get_workspace("case-01")["runs"][0]
    assert run["model_calls"] == 1
    assert run["proposal_correction_calls"] == 0
    assert run["clean_execution_retries"] == 0
    assert workflow.get_workspace("case-01")["pendingEvidenceRequest"] is None


def test_product_retry_rebuilds_clean_attempt_and_preserves_failure_trace(tmp_path: Path) -> None:
    valid = assessment()
    client = SequenceClient([ModelParseError("malformed", raw_output='{"bad": true}'), valid])
    workflow = start_vnext(tmp_path, client)
    traces = workflow.get_traces("case-01")
    assert workflow.get_workspace("case-01")["runtimeStatus"] == "COMPLETED"
    assert len(client.calls) == 2
    assert [trace["attempt_number"] for trace in traces if trace["event"] == "vnext_attempt_failed"] == [1]
    assert traces[-1]["event"] == "vnext_completed"
    assert traces[-1]["attempt_number"] == 2
    failed = next(trace for trace in traces if trace["event"] == "vnext_attempt_failed")
    assert failed["raw_output"] == '{"bad": true}'
    run = workflow.get_workspace("case-01")["runs"][0]
    assert run["model_calls"] == 2
    assert run["proposal_correction_calls"] == 0
    assert run["clean_execution_retries"] == 1


def test_product_retry_failure_is_failed_without_successful_result(tmp_path: Path) -> None:
    client = SequenceClient([ModelParseError("first", raw_output="one"), ModelParseError("second", raw_output="two")])
    workflow = start_vnext(tmp_path, client)
    workspace = workflow.get_workspace("case-01")
    run = workspace["runs"][0]
    artifact = tmp_path / "cases" / "case-01" / "runs" / run["run_id"]
    assert workspace["runtimeStatus"] == "FAILED"
    assert len(client.calls) == 2
    assert not (artifact / "vnext_result.json").exists()
    assert [trace["attempt_number"] for trace in workflow.get_traces("case-01") if trace["event"] == "vnext_attempt_failed"] == [1, 2]
    assert run["model_calls"] == 2
    assert run["proposal_correction_calls"] == 0
    assert run["clean_execution_retries"] == 1


def test_missing_preset_fails_without_model_retry(tmp_path: Path) -> None:
    client = SequenceClient([assessment(), assessment()])
    workflow = HumanEvidenceWorkflow(CaseRepository(tmp_path / "cases"), run_mode="vnext")
    state = workflow.ensure_case("case-01")
    state.assessment_rule_preset_id = "missing-preset"
    workflow.repository.save(state)
    workflow.run_callback = VNextProductionRunner(client).run
    with pytest.raises(ValueError, match="Unknown vNext assessment rule preset"):
        workflow.start_run("case-01")
    assert workflow.get_workspace("case-01")["runtimeStatus"] == "IDLE"
    assert workflow.get_runs("case-01") == []
    assert client.calls == []


def test_clean_rerun_has_distinct_history_and_does_not_use_prior_reasoning_graph(tmp_path: Path) -> None:
    client = SequenceClient([assessment(), assessment()])
    workflow = start_vnext(tmp_path, client)
    first_run_id = workflow.get_workspace("case-01")["runs"][0]["run_id"]
    state = workflow.repository.load("case-01")
    state.sources["S2"] = source("S2", "New human-supplied evidence")
    workflow.repository.save(state)
    workflow.start_run("case-01")
    for _ in range(100):
        if workflow.get_workspace("case-01")["runs"][-1]["final_runtime_status"] == "COMPLETED":
            break
        time.sleep(0.01)
    runs = workflow.get_workspace("case-01")["runs"]
    assert len(client.calls) == 2
    assert [run["run_id"] for run in runs] == [first_run_id, "run_000002"]
    second_result = json.loads((tmp_path / "cases" / "case-01" / "runs" / "run_000002" / "vnext_result.json").read_text())
    assert set(second_result["result"]["graph"]["nodes"]) == {"S1", "S2"}
    assert all(node["node_type"] == "source" for node in second_result["result"]["graph"]["nodes"].values())


def test_legacy_workflow_mode_remains_available(tmp_path: Path) -> None:
    workflow = HumanEvidenceWorkflow(CaseRepository(tmp_path / "cases"), run_mode="legacy")
    assert workflow.run_mode == "legacy"
    with pytest.raises(ValueError, match="vnext.*legacy"):
        HumanEvidenceWorkflow(CaseRepository(tmp_path / "other"), run_mode="unsupported")
