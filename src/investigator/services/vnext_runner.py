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

from investigator.llm import BedrockModelClient, ModelClient, ModelParseError, failure_category, is_provider_timeout, redact_sensitive_text
from investigator.services.evidence_requests import HumanEvidenceWorkflow
from investigator.vnext import (
    VNextInvestigationRunner,
    VNextRunInput,
    VNextRunResult,
    VNextRunValidationError,
    WardenFailureClass,
    WardenValidationError,
    classify_warden_failure,
    run_input_from_case_state,
)
from investigator.vnext.model import VNextInvestigatorModel, build_corrective_prompt, build_prompt
from investigator.vnext.models import AssessmentRulePreset, AssessmentStatus, Confidence, FurthestJustifiedConclusion, InvestigatorAssessment, InvestigatorProposal, SubjectAssessment, ViolationAssessment
from investigator.vnext.semantic import InvestigatorSemanticAssessment, SemanticValidationError, compile_semantic_assessment
from investigator.vnext.source_applicability import source_applicability_snapshot
from investigator.vnext.presets import preset_for_case
from investigator.runtime_settings import effective_model
from investigator.reporting import build_report_record


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
        state = workflow.repository.require_case(case_id)
        admitted_state = state.model_copy(deep=True)
        preset = self.preset_resolver(state)
        substantive_sources = {key: value for key, value in state.sources.items() if (value.content or "").strip()}
        model_spec = effective_model("investigator") if substantive_sources else None
        client = None if not substantive_sources else (self.client or BedrockModelClient(model_id=model_spec.invocation_id, region=model_spec.region))
        logical_model = model_spec.name if model_spec else None
        workflow.record_run_model(case_id, logical_model)
        last_error: Exception | None = None
        retry_constraints: list[str] = []
        model_call_number = 0

        def call_model(
            investigator: VNextInvestigatorModel,
            prompt: str,
            output_schema: type[Any],
            *,
            attempt_number: int,
            call_kind: str,
        ) -> Any:
            nonlocal model_call_number
            model_call_number += 1
            call_number = model_call_number
            run_id = workflow.current_run_id(case_id)

            def record_started(exact_prompt: str) -> None:
                workflow.record_trace(case_id, {
                    "event": "vnext_model_call_started",
                    "actor": "investigator",
                    "runtime_status": "RUNNING",
                    "model_call_number": call_number,
                    "attempt_number": attempt_number,
                    "call_kind": call_kind,
                    "model": logical_model,
                    "run_id": run_id,
                    "case_id": case_id,
                    "prompt": exact_prompt,
                })

            def record_completed(call: Any) -> None:
                metadata = call.metadata
                workflow.record_trace(case_id, {
                    "event": "vnext_model_call_completed",
                    "actor": "investigator",
                    "runtime_status": "RUNNING",
                    "model_call_number": call_number,
                    "attempt_number": attempt_number,
                    "call_kind": call_kind,
                    "model": logical_model,
                    "run_id": run_id,
                    "case_id": case_id,
                    "raw_output": call.raw_output,
                    "parsed_output": call.parsed.model_dump(mode="json"),
                    "provider": metadata.provider,
                    "input_tokens": metadata.input_tokens,
                    "output_tokens": metadata.output_tokens,
                    "latency_seconds": metadata.latency_seconds,
                    "finish_reason": metadata.finish_reason,
                    "parse_success": metadata.parse_success,
                })

            def record_failed(exc: Exception) -> None:
                metadata = getattr(exc, "metadata", None)
                workflow.record_trace(case_id, {
                    "event": "vnext_model_call_failed",
                    "actor": "investigator",
                    "runtime_status": "RUNNING" if self._retryable(exc) and attempt_number < self.max_attempts else "FAILED",
                    "model_call_number": call_number,
                    "attempt_number": attempt_number,
                    "call_kind": call_kind,
                    "model": logical_model,
                    "run_id": run_id,
                    "case_id": case_id,
                    "technical_error_type": type(exc).__name__,
                    "failure_category": failure_category(exc),
                    "error": redact_sensitive_text(str(exc)),
                    "raw_output": getattr(exc, "raw_output", None),
                    "provider": metadata.provider if metadata else None,
                    "input_tokens": metadata.input_tokens if metadata else None,
                    "output_tokens": metadata.output_tokens if metadata else None,
                    "latency_seconds": metadata.latency_seconds if metadata else None,
                    "finish_reason": metadata.finish_reason if metadata else None,
                    "parse_success": False,
                })

            return investigator.call_prompt(
                prompt,
                output_schema,
                on_started=record_started,
                on_completed=record_completed,
                on_failed=record_failed,
            )

        def record_result_validation_failure(exc: VNextRunValidationError, attempt_number: int) -> None:
            workflow.record_trace(case_id, {
                "event": "vnext_result_validation_failed",
                "actor": "investigator",
                "runtime_status": "FAILED",
                "attempt_number": attempt_number,
                "model_call_number": model_call_number or None,
                "run_id": workflow.current_run_id(case_id),
                "case_id": case_id,
                "validation_stage": "post_apply",
                "technical_error_type": type(exc).__name__,
                "failure_category": failure_category(exc),
                "error": redact_sensitive_text(str(exc)),
            })

        for attempt_number in range(1, self.max_attempts + 1):
            run_input = run_input_from_case_state(
                admitted_state,
                preset,
                retry_constraints=retry_constraints,
                human_inputs={"zero_evidence": not substantive_sources},
            )
            if not substantive_sources:
                run_input = run_input.model_copy(update={"sources": {}})
            investigator = VNextInvestigatorModel(client) if client is not None else None
            workflow.record_trace(
                case_id,
                {
                    "event": "vnext_attempt_started",
                    "actor": "investigator",
                    "runtime_status": "RUNNING",
                    "attempt_number": attempt_number,
                    "run_id": workflow.current_run_id(case_id),
                    "case_id": case_id,
                    "model": logical_model,
                    "rule_preset_id": preset.preset_id,
                    "source_ids": sorted(run_input.sources),
                },
            )
            corrective_attempted = False
            first_assessment: InvestigatorAssessment | None = None
            semantic_path = substantive_sources
            try:
                if not substantive_sources:
                    first_assessment = self._zero_evidence_assessment(run_input)
                else:
                    workflow.record_model_attempt(
                        case_id,
                        kind="initial" if attempt_number == 1 else "clean_execution_retry",
                    )
                    semantic_assessment = call_model(
                        investigator,
                        build_prompt(run_input),
                        InvestigatorSemanticAssessment,
                        attempt_number=attempt_number,
                        call_kind="semantic_initial" if attempt_number == 1 else "semantic_clean_execution_retry",
                    )
                    workflow.record_trace(case_id, {
                        "event": "vnext_semantic_compilation_started",
                        "actor": "investigator",
                        "runtime_status": "RUNNING",
                        "attempt_number": attempt_number,
                        "run_id": workflow.current_run_id(case_id),
                        "case_id": case_id,
                        "evidence_use_map": source_applicability_snapshot(run_input.source_applicability),
                    })
                    try:
                        first_assessment = compile_semantic_assessment(semantic_assessment, run_input, preset=preset)
                    except SemanticValidationError as semantic_error:
                        workflow.record_trace(case_id, {
                            "event": "vnext_semantic_validation_failed",
                            "actor": "investigator",
                            "runtime_status": "RUNNING" if attempt_number < self.max_attempts else "FAILED",
                            "attempt_number": attempt_number,
                            "run_id": workflow.current_run_id(case_id),
                            "case_id": case_id,
                            "failure_category": "SEMANTIC_VALIDATION",
                            "error": redact_sensitive_text(str(semantic_error)),
                            "evidence_use_map": source_applicability_snapshot(run_input.source_applicability),
                        })
                        raise
                    workflow.record_trace(case_id, {
                        "event": "vnext_semantic_compilation_completed",
                        "actor": "investigator",
                        "runtime_status": "RUNNING",
                        "attempt_number": attempt_number,
                        "run_id": workflow.current_run_id(case_id),
                        "case_id": case_id,
                        "compiled_proposal": first_assessment.proposal.model_dump(mode="json"),
                    })
                try:
                    result = VNextInvestigationRunner(lambda _: first_assessment).run(run_input)
                except WardenValidationError as validation_error:
                    if semantic_path:
                        workflow.record_trace(case_id, {
                            "event": "vnext_warden_validation_failed",
                            "actor": "investigator",
                            "runtime_status": "FAILED",
                            "attempt_number": attempt_number,
                            "run_id": workflow.current_run_id(case_id),
                            "case_id": case_id,
                            "validation_issues": [issue.model_dump(mode="json") for issue in validation_error.issues],
                            "implementation_defect": True,
                        })
                        raise
                    failure_class = classify_warden_failure(validation_error)
                    if failure_class is WardenFailureClass.SEMANTIC_AFFECTING:
                        issues = [issue.model_dump(mode="json") for issue in validation_error.issues]
                        workflow.record_trace(
                            case_id,
                            {
                                "event": "vnext_proposal_validation_failed",
                                "actor": "investigator",
                                "runtime_status": "RUNNING" if attempt_number < self.max_attempts else "FAILED",
                                "attempt_number": attempt_number,
                                "run_id": workflow.current_run_id(case_id),
                                "case_id": case_id,
                                "retry_mode": "clean_execution",
                                "repairable": False,
                                "failure_class": failure_class.value,
                                "validation_issues": issues,
                                "model_call_number": model_call_number or None,
                                "validation_stage": "warden",
                            },
                        )
                        workflow.record_trace(
                            case_id,
                            {
                                "event": "vnext_semantic_scope_retry_required",
                                "actor": "investigator",
                                "runtime_status": "RUNNING" if attempt_number < self.max_attempts else "FAILED",
                                "attempt_number": attempt_number,
                                "run_id": workflow.current_run_id(case_id),
                                "case_id": case_id,
                                "retry_mode": "clean_execution",
                                "failure_class": failure_class.value,
                                "error_codes": sorted({issue.error_code for issue in validation_error.issues}),
                                "issue_count": len(validation_error.issues),
                            },
                        )
                        if attempt_number < self.max_attempts:
                            retry_constraints = self._semantic_retry_constraints(validation_error)
                            workflow.record_trace(case_id, {
                                "event": "vnext_retry_decision",
                                "actor": "investigator",
                                "runtime_status": "RUNNING",
                                "attempt_number": attempt_number,
                                "model_call_number": model_call_number,
                                "run_id": workflow.current_run_id(case_id),
                                "case_id": case_id,
                                "retry_mode": "clean_execution",
                                "reason_class": "semantic_affecting",
                                "next_attempt_number": attempt_number + 1,
                            })
                            workflow.record_trace(
                                case_id,
                                {
                                    "event": "vnext_clean_retry_started",
                                    "actor": "investigator",
                                    "runtime_status": "RUNNING",
                                    "attempt_number": attempt_number + 1,
                                    "run_id": workflow.current_run_id(case_id),
                                    "case_id": case_id,
                                    "retry_mode": "clean_execution",
                                    "failure_class": failure_class.value,
                                    "retry_constraints": list(retry_constraints),
                                },
                            )
                            continue
                        workflow.record_trace(case_id, {
                            "event": "vnext_retry_decision",
                            "actor": "investigator",
                            "runtime_status": "FAILED",
                            "attempt_number": attempt_number,
                            "model_call_number": model_call_number,
                            "run_id": workflow.current_run_id(case_id),
                            "case_id": case_id,
                            "retry_mode": "none",
                            "reason_class": "semantic_affecting",
                            "terminal": True,
                        })
                        raise
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
                            "model_call_number": model_call_number,
                            "validation_stage": "warden",
                        },
                    )
                    workflow.record_trace(case_id, {
                        "event": "vnext_retry_decision",
                        "actor": "investigator",
                        "runtime_status": "RUNNING",
                        "attempt_number": 1,
                        "model_call_number": model_call_number,
                        "run_id": workflow.current_run_id(case_id),
                        "case_id": case_id,
                        "retry_mode": "proposal_correction",
                        "reason_class": "structural_repairable",
                        "next_attempt_number": 2,
                    })
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
                    workflow.record_model_attempt(case_id, kind="proposal_correction")
                    repaired_proposal = call_model(
                        investigator,
                        build_corrective_prompt(first_assessment, validation_error.issues),
                        InvestigatorProposal,
                        attempt_number=2,
                        call_kind="proposal_correction",
                    )
                    if not isinstance(repaired_proposal, InvestigatorProposal):
                        repaired_proposal = InvestigatorProposal.model_validate(repaired_proposal)
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
                                "model_call_number": model_call_number,
                                "validation_stage": "warden",
                            },
                        )
                        workflow.record_trace(case_id, {
                            "event": "vnext_retry_decision",
                            "actor": "investigator",
                            "runtime_status": "FAILED",
                            "attempt_number": 2,
                            "model_call_number": model_call_number,
                            "run_id": workflow.current_run_id(case_id),
                            "case_id": case_id,
                            "retry_mode": "none",
                            "reason_class": "structural_repairable",
                            "terminal": True,
                        })
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
                except VNextRunValidationError as result_error:
                    record_result_validation_failure(result_error, attempt_number)
                    raise
                result_attempt_number = 2 if corrective_attempted else attempt_number
                self._persist_success(workflow, case_id, result_attempt_number, result, investigator, logical_model)
                metadata = investigator.last_call.metadata if investigator and investigator.last_call else None
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
                        "model_call_number": model_call_number or None,
                        "model": logical_model,
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
                        "case_revision": workflow.repository.require_case(case_id).revision,
                        "human_summary": "The finite vNext assessment completed.",
                    },
                )
                return result
            except Exception as exc:
                last_error = exc
                metadata = investigator.last_call.metadata if investigator and investigator.last_call else None
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
                        "failure_category": failure_category(exc),
                        "technical_error_type": type(exc).__name__,
                        "error_type": type(exc).__name__,
                        "model_call_number": model_call_number or None,
                        "error": redact_sensitive_text(str(exc)),
                        "raw_output": getattr(exc, "raw_output", None)
                        or (investigator.last_call.raw_output if investigator and investigator.last_call else None),
                        "model": logical_model,
                        "input_tokens": metadata.input_tokens if metadata else None,
                        "output_tokens": metadata.output_tokens if metadata else None,
                        "latency_seconds": metadata.latency_seconds if metadata else None,
                        "finish_reason": metadata.finish_reason if metadata else None,
                    },
                )
                if corrective_attempted or (semantic_path and isinstance(exc, WardenValidationError)) or not self._retryable(exc) or attempt_number == self.max_attempts:
                    raise
        raise AssertionError(f"vNext run ended without a result: {last_error}")

    @staticmethod
    def _semantic_retry_constraints(error: WardenValidationError) -> list[str]:
        """Return deterministic scope constraints, never prior model reasoning."""

        constraints = {
            "Reassess the complete case from the original admitted evidence.",
            "Do not preserve conclusions from the prior failed execution.",
        }
        if any(
            issue.source_scope
            and issue.target_scope
            and issue.source_scope.get("scope_type") == "subject"
            and issue.target_scope.get("scope_type") == "relationship"
            for issue in error.issues
        ):
            constraints.update(
                {
                    "Do not derive a relationship-scoped proposition from separate student-specific evidence.",
                    "Reassess each student independently from the original admitted evidence.",
                }
            )
        return sorted(constraints)

    @staticmethod
    def _zero_evidence_assessment(run_input: VNextRunInput) -> InvestigatorAssessment:
        assessments = []
        for subject_id in run_input.subjects:
            assessments.append(SubjectAssessment(subject_id=subject_id, violation_assessments=[ViolationAssessment(violation_id=item.violation_id, status=AssessmentStatus.NOT_CURRENTLY_SUPPORTED, reasoning_summary="The current record contains no substantive evidence for this assessment.", confidence=Confidence.LOW) for item in run_input.rule_preset.violations], furthest_conclusion=FurthestJustifiedConclusion(statement="The current record does not support a finding on the configured violations.", confidence=Confidence.LOW)))
        return InvestigatorAssessment(proposal=InvestigatorProposal(graph_updates=[]), subject_assessments=assessments)

    @staticmethod
    def _retryable(exc: Exception) -> bool:
        # A read timeout may occur after Bedrock accepted the inference. Never
        # issue a fresh paid request for this ambiguous transport failure.
        if is_provider_timeout(exc):
            return False
        return isinstance(exc, (ModelParseError, VNextRunValidationError, SemanticValidationError, WardenValidationError, RuntimeError))

    @staticmethod
    def _persist_success(
        workflow: HumanEvidenceWorkflow,
        case_id: str,
        attempt_number: int,
        result: VNextRunResult,
        investigator: VNextInvestigatorModel | None,
        logical_model: str | None,
    ) -> None:
        run_id = workflow.current_run_id(case_id)
        if run_id is None:
            raise RuntimeError("Cannot persist vNext result without an active run")
        directory = workflow.repository.run_dir(case_id, run_id)
        directory.mkdir(parents=True, exist_ok=True)
        metadata = investigator.last_call.metadata if investigator and investigator.last_call else None
        snapshot_path = directory / "assessment_input_snapshot.json"
        if not snapshot_path.is_file():
            raise RuntimeError("Cannot publish vNext result without the admitted input snapshot")
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        created_at = datetime.now(timezone.utc).isoformat()
        report_record = build_report_record(snapshot, result, completed_at=created_at)
        payload = {
            "run_id": run_id,
            "attempt_number": attempt_number,
            "created_at": created_at,
            "model": logical_model,
            "model_metadata": metadata.model_dump(mode="json") if metadata else None,
            "result": result.model_dump(mode="json"),
        }
        report_destination = directory / "report_record.json"
        with NamedTemporaryFile("w", encoding="utf-8", dir=directory, delete=False, suffix=".tmp") as handle:
            report_temporary = Path(handle.name)
            json.dump(report_record, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        report_temporary.replace(report_destination)
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
                "trace_finalized": False,
                "vnext_status": result.status.value,
                "vnext_result_path": str(destination),
                "report_record_path": str(report_destination),
                "vnext_furthest_conclusion": conclusion,
                "vnext_subject_conclusions": subject_conclusions,
                "model": logical_model,
                "input_tokens": metadata.input_tokens if metadata else None,
                "output_tokens": metadata.output_tokens if metadata else None,
                "latency_seconds": metadata.latency_seconds if metadata else None,
                "finish_reason": metadata.finish_reason if metadata else None,
            }
        )
        workflow._write_run_result(run_result_path, run_summary)
