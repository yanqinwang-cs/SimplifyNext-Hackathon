"""Product adapter for clean, finite vNext runs.

This adapter is intentionally separate from the legacy production runner. A
vNext attempt calls the Investigator once and retries only by rebuilding a
clean input; it never resumes an unfinished reasoning graph.
"""

from collections.abc import Callable
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from investigator.llm import BedrockModelClient, ModelClient, ModelParseError
from investigator.services.evidence_requests import HumanEvidenceWorkflow
from investigator.vnext import (
    VNextInvestigationRunner,
    VNextRunInput,
    VNextRunResult,
    VNextRunValidationError,
    WardenValidationError,
    run_input_from_case_state,
)
from investigator.vnext.model import VNextInvestigatorModel, build_corrective_prompt
from investigator.vnext.models import AssessmentRulePreset, InvestigatorAssessment, InvestigatorProposal
from investigator.vnext.presets import preset_for_case


class VNextProductionRunner:
    """Run and persist one vNext assessment with at most one clean retry."""

    def __init__(
        self,
        client: ModelClient | None = None,
        *,
        preset_resolver: Callable[[Any], AssessmentRulePreset] = preset_for_case,
        max_attempts: int = 2,
    ) -> None:
        if max_attempts not in {1, 2}:
            raise ValueError("VNextProductionRunner max_attempts must be 1 or 2")
        self.client = client
        self.preset_resolver = preset_resolver
        self.max_attempts = max_attempts

    def run(self, case_id: str, workflow: HumanEvidenceWorkflow) -> VNextRunResult:
        state = workflow.ensure_case(case_id)
        preset = self.preset_resolver(state)
        client = self.client or BedrockModelClient(
            model_id=os.environ.get("VNEXT_INVESTIGATOR_MODEL_ID") or None
        )
        last_error: Exception | None = None
        for attempt_number in range(1, self.max_attempts + 1):
            state = workflow.repository.load(case_id)
            run_input = run_input_from_case_state(state, preset)
            investigator = VNextInvestigatorModel(client)
            workflow.record_trace(
                case_id,
                {
                    "event": "vnext_attempt_started",
                    "actor": "investigator",
                    "runtime_status": "RUNNING",
                    "attempt_number": attempt_number,
                    "run_id": workflow.current_run_id(case_id),
                    "case_id": case_id,
                    "rule_preset_id": preset.preset_id,
                    "source_ids": sorted(run_input.sources),
                },
            )
            corrective_attempted = False
            first_assessment: InvestigatorAssessment | None = None
            try:
                workflow.record_model_attempt(case_id, correction=attempt_number > 1)
                first_assessment = investigator(run_input)
                try:
                    result = VNextInvestigationRunner(lambda _: first_assessment).run(run_input)
                except WardenValidationError as validation_error:
                    if attempt_number != 1 or not validation_error.issues:
                        raise
                    corrective_attempted = True
                    issues = [issue.model_dump(mode="json") for issue in validation_error.issues]
                    workflow.record_trace(
                        case_id,
                        {
                            "event": "vnext_proposal_validation_failed",
                            "actor": "investigator",
                            "runtime_status": "RUNNING",
                            "attempt_number": 1,
                            "run_id": workflow.current_run_id(case_id),
                            "case_id": case_id,
                            "retry_mode": "corrective",
                            "repairable": True,
                            "validation_issues": issues,
                        },
                    )
                    workflow.record_trace(
                        case_id,
                        {
                            "event": "vnext_corrective_retry_started",
                            "actor": "investigator",
                            "runtime_status": "RUNNING",
                            "attempt_number": 2,
                            "run_id": workflow.current_run_id(case_id),
                            "case_id": case_id,
                            "retry_mode": "corrective",
                            "validation_issues": issues,
                        },
                    )
                    workflow.record_model_attempt(case_id, correction=True)
                    repair_call = client.call(
                        build_corrective_prompt(first_assessment, validation_error.issues),
                        InvestigatorProposal,
                    )
                    repaired_proposal = repair_call.parsed
                    if not isinstance(repaired_proposal, InvestigatorProposal):
                        repaired_proposal = InvestigatorProposal.model_validate(repaired_proposal)
                    investigator.last_call = repair_call
                    repaired_assessment = first_assessment.model_copy(update={"proposal": repaired_proposal})
                    try:
                        result = VNextInvestigationRunner(lambda _: repaired_assessment).run(run_input)
                    except WardenValidationError as second_validation_error:
                        workflow.record_trace(
                            case_id,
                            {
                                "event": "vnext_proposal_validation_failed",
                                "actor": "investigator",
                                "runtime_status": "FAILED",
                                "attempt_number": 2,
                                "run_id": workflow.current_run_id(case_id),
                                "case_id": case_id,
                                "retry_mode": "corrective",
                                "repairable": False,
                                "validation_issues": [
                                    issue.model_dump(mode="json") for issue in second_validation_error.issues
                                ],
                            },
                        )
                        raise
                    workflow.record_trace(
                        case_id,
                        {
                            "event": "vnext_corrective_retry_succeeded",
                            "actor": "investigator",
                            "runtime_status": "RUNNING",
                            "attempt_number": 2,
                            "run_id": workflow.current_run_id(case_id),
                            "case_id": case_id,
                            "retry_mode": "corrective",
                            "validation_issues": issues,
                        },
                    )
                result_attempt_number = 2 if corrective_attempted else attempt_number
                self._persist_success(workflow, case_id, result_attempt_number, result, investigator)
                metadata = investigator.last_call.metadata if investigator.last_call else None
                workflow.record_trace(
                    case_id,
                    {
                        "event": "vnext_completed",
                        "actor": "investigator",
                        "runtime_status": "COMPLETED",
                        "attempt_number": result_attempt_number,
                        "run_id": workflow.current_run_id(case_id),
                        "case_id": case_id,
                        "result": result.model_dump(mode="json"),
                        "model": metadata.model if metadata else None,
                        "input_tokens": metadata.input_tokens if metadata else None,
                        "output_tokens": metadata.output_tokens if metadata else None,
                        "latency_seconds": metadata.latency_seconds if metadata else None,
                        "finish_reason": metadata.finish_reason if metadata else None,
                    },
                )
                workflow.set_runtime(case_id, "COMPLETED", "NONE")
                workflow.record_workspace_event(
                    case_id,
                    {
                        "type": "vnext_run_completed",
                        "run_id": workflow.current_run_id(case_id),
                        "runtime_status": "COMPLETED",
                        "case_revision": workflow.ensure_case(case_id).revision,
                        "human_summary": "The finite vNext assessment completed.",
                    },
                )
                return result
            except Exception as exc:
                last_error = exc
                metadata = investigator.last_call.metadata if investigator.last_call else None
                trace_attempt_number = 2 if corrective_attempted else attempt_number
                workflow.record_trace(
                    case_id,
                    {
                        "event": "vnext_attempt_failed",
                        "actor": "investigator",
                        "runtime_status": "RUNNING" if attempt_number < self.max_attempts else "FAILED",
                        "attempt_number": trace_attempt_number,
                        "run_id": workflow.current_run_id(case_id),
                        "case_id": case_id,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                        "raw_output": getattr(exc, "raw_output", None)
                        or (investigator.last_call.raw_output if investigator.last_call else None),
                        "model": metadata.model if metadata else None,
                        "input_tokens": metadata.input_tokens if metadata else None,
                        "output_tokens": metadata.output_tokens if metadata else None,
                        "latency_seconds": metadata.latency_seconds if metadata else None,
                        "finish_reason": metadata.finish_reason if metadata else None,
                    },
                )
                if corrective_attempted or not self._retryable(exc) or attempt_number == self.max_attempts:
                    raise
        raise AssertionError(f"vNext run ended without a result: {last_error}")

    @staticmethod
    def _retryable(exc: Exception) -> bool:
        return isinstance(exc, (ModelParseError, VNextRunValidationError, WardenValidationError, RuntimeError))

    @staticmethod
    def _persist_success(
        workflow: HumanEvidenceWorkflow,
        case_id: str,
        attempt_number: int,
        result: VNextRunResult,
        investigator: VNextInvestigatorModel,
    ) -> None:
        run_id = workflow.current_run_id(case_id)
        if run_id is None:
            raise RuntimeError("Cannot persist vNext result without an active run")
        directory = workflow.repository.root / case_id / "runs" / run_id
        directory.mkdir(parents=True, exist_ok=True)
        metadata = investigator.last_call.metadata if investigator.last_call else None
        payload = {
            "run_id": run_id,
            "attempt_number": attempt_number,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "model_metadata": metadata.model_dump(mode="json") if metadata else None,
            "result": result.model_dump(mode="json"),
        }
        destination = directory / "vnext_result.json"
        with NamedTemporaryFile("w", encoding="utf-8", dir=directory, delete=False, suffix=".tmp") as handle:
            temporary = Path(handle.name)
            json.dump(payload, handle, indent=2)
            handle.write("\n")
        temporary.replace(destination)
        run_result_path = directory / "run_result.json"
        run_summary = json.loads(run_result_path.read_text(encoding="utf-8"))
        subject_conclusions = {
            item.subject_id: item.furthest_conclusion.statement
            for item in result.subject_assessments
        }
        conclusion = "; ".join(
            f"{subject_id}: {statement}"
            for subject_id, statement in subject_conclusions.items()
        )
        run_summary.update(
            {
                "final_runtime_status": "COMPLETED",
                "outcome_type": "COMPLETED",
                "vnext_status": result.status.value,
                "vnext_result_path": str(destination),
                "vnext_furthest_conclusion": conclusion,
                "vnext_subject_conclusions": subject_conclusions,
                "model": metadata.model if metadata else None,
                "input_tokens": metadata.input_tokens if metadata else None,
                "output_tokens": metadata.output_tokens if metadata else None,
                "latency_seconds": metadata.latency_seconds if metadata else None,
                "finish_reason": metadata.finish_reason if metadata else None,
            }
        )
        run_result_path.write_text(json.dumps(run_summary, indent=2) + "\n", encoding="utf-8")
