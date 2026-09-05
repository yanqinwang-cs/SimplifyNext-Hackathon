"""Human-facing evidence-request workflow outside the semantic CaseGraph."""

import re
import json
import hashlib
import shutil
import uuid
from pathlib import Path
from threading import RLock
from typing import Any, Callable
from datetime import datetime, timezone

from investigator.cycle import EvidenceRequest, EvidenceRequestResponse, EvidenceRequestStatus, RequestEvidence, RequestInformation
from investigator.models.evidence_request import allocate_evidence_request_id
from investigator.cycle import TurnSnapshot
from investigator.sources import SourceRegistry
from investigator.models.source import Source
from investigator.models.source import SourceType
from investigator.graph import GraphScope, GraphScopeType
from investigator.models.assessment import AssessmentSubject, SubjectRelationship
from investigator.state.case_state import CaseState
from investigator.state.repository import CaseRepository
from investigator.vnext.presets import preset_for_case
from investigator.vnext.source_applicability import validate_configured_identifiers
from investigator.llm.bedrock import redact_sensitive_text
from investigator.reporting import build_input_snapshot, public_report_from_record
from investigator.public_views import document_format, public_assessment_source_handle, public_run_handle_for_instance


class EvidenceRequestConflict(RuntimeError):
    pass


class CaseSnapshotMismatch(EvidenceRequestConflict):
    """The canonical repository changed relative to a role-turn baseline."""
    pass


class ReportIntegrityError(RuntimeError):
    """A published assessment artifact cannot be safely projected."""

    pass


def _public_semantic_text(state: CaseState, value: object) -> str:
    """Remove internal graph/source identity tokens from model-derived text."""

    text = str(value or "")
    replacements: list[tuple[str, str]] = []
    for subject_id, subject in state.subjects.items():
        replacements.append((subject_id, subject.display_name))
    for source_id in state.sources:
        replacements.append((source_id, "the source"))
    for relationship_id in state.subject_relationships:
        replacements.append((relationship_id, "the relationship"))
    replacements.sort(key=lambda item: len(item[0]), reverse=True)
    for internal, replacement in replacements:
        text = re.sub(rf"(?<![A-Za-z0-9_]){re.escape(internal)}(?![A-Za-z0-9_])", replacement, text)
    text = re.sub(r"(?<![A-Za-z0-9_])rel_[a-f0-9]{12,64}(?![A-Za-z0-9_])", "the relationship", text)
    return text


def _state_signature(state: CaseState) -> str:
    graph = state.reasoning_graph.model_dump(mode="json") if state.reasoning_graph is not None else None
    payload = {
        "graph": graph,
        "focus_node_id": state.focus_node_id,
        "revision": state.revision,
        "pending_request": next((item.model_dump(mode="json") for item in reversed(state.evidence_request_history) if item.status is EvidenceRequestStatus.PENDING), None),
        "sources": {
            source_id: hashlib.sha256(json.dumps(source.model_dump(mode="json"), sort_keys=True).encode()).hexdigest()
            for source_id, source in sorted(state.sources.items())
        },
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


class HumanEvidenceWorkflow:
    """Single-pending-request application boundary backed by CaseRepository."""

    def __init__(self, repository: CaseRepository, resume_callback: Callable[[str], None] | None = None, run_callback: Callable[[str, "HumanEvidenceWorkflow"], None] | None = None, run_mode: str = "legacy") -> None:
        if run_mode not in {"legacy", "vnext"}:
            raise ValueError("SIMPLIFYNEXT_RUN_MODE must be either 'vnext' or 'legacy'")
        self.repository = repository
        self.resume_callback = resume_callback
        self.run_mode = run_mode
        self._lock = RLock()
        self._model_revision_active: set[str] = set()
        self._in_flight_actor: dict[str, str] = {}
        self.run_callback = run_callback
        self._active_runs: dict[str, str] = {}
        self._workspace_events: dict[str, list[dict[str, Any]]] = {}
        self._event_sequence = 0

    def _run_dir(self, case_id: str, run_id: str) -> Path:
        return self.repository.run_dir(case_id, run_id)

    def _allocate_run_id(self, case_id: str) -> str:
        run_root = self.repository.runs_dir(case_id)
        run_root.mkdir(parents=True, exist_ok=True)
        numbers = [int(match.group(1)) for path in run_root.iterdir() if (match := re.fullmatch(r"run_(\d{6})", path.name))]
        return f"run_{max(numbers, default=0) + 1:06d}"

    def _prepare_run_artifacts(
        self,
        state: CaseState,
        start_revision: int | None,
        preset: Any,
        *,
        runtime_status: str,
    ) -> str:
        run_id = self._allocate_run_id(state.case_id)
        directory = self._run_dir(state.case_id, run_id)
        directory.mkdir(parents=True)
        run_instance_id = uuid.uuid4().hex
        try:
            snapshot = build_input_snapshot(state, preset, run_instance_id)
            self._write_run_result(directory / "assessment_input_snapshot.json", snapshot)
            started_at = datetime.now(timezone.utc).isoformat()
            self._write_run_result(directory / "run_result.json", {"run_id": run_id, "run_instance_id": run_instance_id, "started_at": started_at, "ended_at": None, "termination_reason": None, "final_runtime_status": runtime_status, "outcome_type": "RUNNING", "originating_actor": "INVESTIGATOR", "start_revision": start_revision, "run_start_revision": start_revision, "latest_safe_revision": start_revision, "final_committed_revision": start_revision, "model_calls": 0, "proposal_correction_calls": 0, "clean_execution_retries": 0, "correction_retries": 0, "final_error": None, "pending_request_id": None, "trace_path": f"/api/cases/{state.case_id}/runs/{run_id}/raw-traces"})
        except Exception:
            shutil.rmtree(directory, ignore_errors=True)
            raise
        return run_id

    def begin_run(self, case_id: str, start_revision: int | None = None, preset: Any | None = None) -> str:
        """Prepare and register a run for legacy callers.

        vNext admission uses the failure-safe path in ``start_run_with_id``;
        this method remains a small compatibility boundary for older callers.
        """
        with self._lock:
            state = self.repository.require_case(case_id) if self.run_mode == "vnext" else self.ensure_case(case_id)
            if preset is None:
                preset = preset_for_case(state)
            run_id = self._prepare_run_artifacts(
                state,
                start_revision,
                preset,
                runtime_status="RUNNING" if self.run_mode == "vnext" else "RUNNING_INVESTIGATOR",
            )
            self._active_runs[case_id] = run_id
            return run_id

    def current_run_id(self, case_id: str) -> str | None:
        return self._active_runs.get(case_id)

    def recover_interrupted_run(self, case_id: str) -> None:
        """Mark a persisted in-flight run interrupted after a process restart."""
        with self._lock:
            if case_id in self._active_runs or case_id in self._in_flight_actor:
                return
            state = self.repository.require_case(case_id) if self.run_mode == "vnext" else self.ensure_case(case_id)
            if state.runtime_status not in {"RUNNING", "RUNNING_INVESTIGATOR", "RUNNING_STEWARD"}:
                return
            runs = self.get_runs(case_id)
            run = next((item for item in reversed(runs) if item.get("outcome_type") == "RUNNING"), None)
            error = {"failure_category": "PROCESS_RESTART", "message": "The assessment was interrupted when the service restarted."}
            if run is not None:
                path = self._run_dir(case_id, str(run["run_id"])) / "run_result.json"
                result = json.loads(path.read_text(encoding="utf-8"))
                result.update({
                    "ended_at": datetime.now(timezone.utc).isoformat(),
                    "termination_reason": "process_restart",
                    "final_runtime_status": "INTERRUPTED",
                    "outcome_type": "INTERRUPTED",
                    "final_error": error,
                })
                self._write_run_result(path, result)
            state.runtime_status = "INTERRUPTED"
            state.current_actor = "NONE"
            state.last_error = error
            state.last_updated_at = datetime.now(timezone.utc)
            self.repository.save(state)

    def finalize_run(self, case_id: str, *, expected_run_id: str | None = None, termination_reason: str | None = None, final_error: dict[str, Any] | None = None) -> None:
        with self._lock:
            if expected_run_id is not None and self._active_runs.get(case_id) != expected_run_id:
                return
            run_id = self._active_runs.pop(case_id, None)
            if not run_id:
                return
            state = self.ensure_case(case_id)
            path = self._run_dir(case_id, run_id) / "run_result.json"
            result = json.loads(path.read_text(encoding="utf-8"))
            pending = next((item for item in reversed(state.evidence_request_history) if item.status.value == "pending"), None)
            outcome = "WAITING_FOR_EVIDENCE" if pending else {"IDLE": "COMPLETED", "STOPPED": "STOPPED", "FAILED": "FAILED"}.get(state.runtime_status, state.runtime_status)
            result.update({"ended_at": datetime.now(timezone.utc).isoformat(), "termination_reason": termination_reason or state.runtime_status.lower(), "final_runtime_status": state.runtime_status, "outcome_type": outcome, "final_case_revision": state.revision, "latest_safe_revision": state.revision, "final_committed_revision": state.revision, "final_error": final_error or state.last_error, "pending_request_id": pending.request_id if pending else None, "request_text": pending.information_sought if pending else None})
            self._write_run_result(path, result)

    def record_model_attempt(self, case_id: str, *, correction: bool = False, kind: str | None = None) -> None:
        run_id = self.current_run_id(case_id)
        if not run_id:
            return
        path = self._run_dir(case_id, run_id) / "run_result.json"
        result = json.loads(path.read_text(encoding="utf-8"))
        result["model_calls"] = int(result.get("model_calls") or 0) + 1
        resolved_kind = kind or ("proposal_correction" if correction else "initial")
        if resolved_kind == "proposal_correction":
            result["proposal_correction_calls"] = int(result.get("proposal_correction_calls") or 0) + 1
            result["correction_retries"] = int(result.get("correction_retries") or 0) + 1
        elif resolved_kind == "clean_execution_retry":
            result["clean_execution_retries"] = int(result.get("clean_execution_retries") or 0) + 1
        self._write_run_result(path, result)

    def record_run_model(self, case_id: str, model: str | None) -> None:
        run_id = self.current_run_id(case_id)
        if not run_id or not model:
            return
        path = self._run_dir(case_id, run_id) / "run_result.json"
        result = json.loads(path.read_text(encoding="utf-8"))
        result["model"] = model
        self._write_run_result(path, result)

    @staticmethod
    def _write_run_result(path: Path, result: dict[str, Any]) -> None:
        """Publish a complete run snapshot so readers never see a truncated JSON file."""
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)

    def start_run(self, case_id: str) -> dict[str, Any]:
        """Start a run and retain the historical workspace-only return shape."""
        _, workspace = self.start_run_with_id(case_id)
        return workspace

    def start_run_with_id(self, case_id: str) -> tuple[str, dict[str, Any]]:
        """Start one backend-owned run through the configured production hook."""
        with self._lock:
            state = self.repository.require_case(case_id) if self.run_mode == "vnext" else self.ensure_case(case_id)
            pending = any(item.status.value == "pending" for item in state.evidence_request_history)
            if self.run_mode == "legacy" and (pending or state.runtime_status == "WAITING_FOR_EVIDENCE"):
                raise EvidenceRequestConflict("Resolve the pending human evidence request before running")
            if state.runtime_status in {"RUNNING", "RUNNING_INVESTIGATOR", "RUNNING_STEWARD"} or case_id in self._in_flight_actor or case_id in self._active_runs:
                raise EvidenceRequestConflict("Investigation is already running")
            if state.case_status != "ACTIVE":
                raise EvidenceRequestConflict("Stopped or inactive cases cannot be restarted")
            if self.run_mode == "vnext":
                try:
                    validate_configured_identifiers(state.subjects)
                except ValueError as exc:
                    raise EvidenceRequestConflict("Each student must have a distinct identifier.") from exc
            runtime_status = "RUNNING" if self.run_mode == "vnext" else "RUNNING_INVESTIGATOR"
            run_preset = None
            callback_owner = getattr(self.run_callback, "__self__", None)
            resolver = getattr(callback_owner, "preset_resolver", None)
            if resolver is not None:
                run_preset = resolver(state)
            if run_preset is None:
                run_preset = preset_for_case(state)
            previous_state = state.model_copy(deep=True)
            run_id = self._prepare_run_artifacts(state, state.revision, run_preset, runtime_status=runtime_status)
            try:
                state.runtime_status = runtime_status
                state.current_actor = "INVESTIGATOR"
                state.last_error = None
                state.last_trace_step = None
                state.last_updated_at = datetime.now(timezone.utc)
                self.repository.save(state)
                self._active_runs[case_id] = run_id
                self._in_flight_actor[case_id] = "INVESTIGATOR"
                thread = __import__("threading").Thread(target=self._execute_run, args=(case_id, run_id), daemon=True)
                try:
                    thread.start()
                except Exception:
                    self._active_runs.pop(case_id, None)
                    self._in_flight_actor.pop(case_id, None)
                    self.repository.save(previous_state)
                    shutil.rmtree(self._run_dir(case_id, run_id), ignore_errors=True)
                    raise RuntimeError("Assessment could not be started")
                summary = "Assessment started." if self.run_mode == "vnext" else f"Investigation run {run_id} started from the current verified case state."
                self.record_workspace_event(case_id, {"type": "run_started", "run_id": run_id, "runtime_status": runtime_status, "case_revision": state.revision, "human_summary": summary})
            except Exception:
                if self._active_runs.get(case_id) == run_id:
                    self._active_runs.pop(case_id, None)
                self._in_flight_actor.pop(case_id, None)
                try:
                    self.repository.save(previous_state)
                finally:
                    shutil.rmtree(self._run_dir(case_id, run_id), ignore_errors=True)
                raise
        return run_id, self.get_workspace(case_id)

    def _execute_run(self, case_id: str, run_id: str) -> None:
        try:
            if self.run_callback is None:
                raise RuntimeError("No production Investigator/Steward orchestration entrypoint is configured")
            self.run_callback(case_id, self)
            state = self.ensure_case(case_id)
            if state.runtime_status.startswith("RUNNING_"):
                self.set_runtime(case_id, "IDLE", "NONE")
                summary = "Assessment completed." if self.run_mode == "vnext" else "The investigation run completed successfully."
                self.record_workspace_event(case_id, {"type": "run_completed", "run_id": run_id, "runtime_status": "IDLE", "case_revision": state.revision, "human_summary": summary})
            elif state.runtime_status == "STOPPED":
                self.record_workspace_event(case_id, {"type": "run_stopped", "run_id": run_id, "runtime_status": "STOPPED", "case_revision": state.revision, "resumable": state.case_status == "ACTIVE", "human_summary": "Autonomous investigation paused for human review. The unresolved issues remain preserved, and the investigation can be resumed or new evidence can be added."})
        except Exception as exc:
            if self._active_runs.get(case_id) != run_id:
                return
            category = "ORCHESTRATION_CONFIGURATION_FAILURE" if type(exc).__name__ == "BedrockConfigurationError" else ("CASE_SNAPSHOT_MISMATCH" if isinstance(exc, CaseSnapshotMismatch) else type(exc).__name__)
            state = self.ensure_case(case_id)
            safe_message = redact_sensitive_text(str(exc))
            self.record_trace(case_id, {"event": "failed", "step": state.last_trace_step, "actor": "system", "runtime_status": "FAILED", "case_revision": state.revision, "current_actor": state.current_actor, "failure_category": category, "error": safe_message})
            self.set_runtime(case_id, "FAILED", "NONE", failure_category=category, message=safe_message, step=state.last_trace_step)
            summary = "Assessment could not be completed. Case evidence was unchanged." if self.run_mode == "vnext" else f"The investigation run failed. The failed turn was not committed; earlier successful turns remain preserved at revision {state.revision}."
            self.record_workspace_event(case_id, {"type": "run_failed", "run_id": run_id, "runtime_status": "FAILED", "case_revision": state.revision, "final_case_revision": state.revision, "human_summary": summary})
        finally:
            self.finalize_run(case_id, expected_run_id=run_id)

    def set_runtime(self, case_id: str, runtime_status: str, actor: str = "NONE", *, failure_category: str | None = None, message: str | None = None, step: int | None = None) -> None:
        """Persist truthful runtime state; RUNNING is also tracked in-process."""
        with self._lock:
            state = self.ensure_case(case_id)
            state.runtime_status = runtime_status
            state.current_actor = actor
            state.last_trace_step = step
            state.last_error = ({"failure_category": failure_category, "message": message, "actor": actor, "step": step} if runtime_status == "FAILED" else None)
            state.last_updated_at = datetime.now(timezone.utc)
            if runtime_status == "RUNNING" or runtime_status.startswith("RUNNING_"):
                self._in_flight_actor[case_id] = actor
            else:
                self._in_flight_actor.pop(case_id, None)
            self.repository.save(state)
            if runtime_status == "WAITING_FOR_EVIDENCE":
                run_id = self.current_run_id(case_id)
                if run_id:
                    path = self._run_dir(case_id, run_id) / "run_result.json"
                    if path.is_file():
                        result = json.loads(path.read_text(encoding="utf-8"))
                        pending = next((item for item in reversed(state.evidence_request_history) if item.status.value == "pending"), None)
                        result.update({"final_runtime_status": "WAITING_FOR_EVIDENCE", "outcome_type": "WAITING_FOR_EVIDENCE", "pending_request_id": pending.request_id if pending else None, "request_text": pending.information_sought if pending else None})
                        self._write_run_result(path, result)

    def record_trace(self, case_id: str, trace: dict[str, Any]) -> None:
        with self._lock:
            state = self.ensure_case(case_id)
            record = dict(trace)
            if self.current_run_id(case_id):
                record.setdefault("run_id", self.current_run_id(case_id))
                path = self._run_dir(case_id, self.current_run_id(case_id)) / "raw_traces.jsonl"
                with path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record, default=str) + "\n")
            state.trace_history.append(record)
            state.last_trace_step = trace.get("step") if isinstance(trace.get("step"), int) else state.last_trace_step
            state.last_updated_at = datetime.now(timezone.utc)
            self.repository.save(state)

    def save_reasoning_state(self, case_id: str, graph: Any, focus_node_id: str, case_revision: int | None = None, focus: Any | None = None) -> None:
        with self._lock:
            state = self.ensure_case(case_id)
            state.reasoning_graph = graph.model_copy(deep=True)
            state.focus_node_id = focus_node_id
            if focus is not None:
                state.focus_recent_node_ids = list(focus.recent_node_ids)
                state.focus_recent_region_node_ids = list(focus.recent_region_node_ids)
            if case_revision is not None:
                state.revision = case_revision
            state.last_updated_at = datetime.now(timezone.utc)
            self.repository.save(state)

    def assert_turn_snapshot_current(self, case_id: str, snapshot: TurnSnapshot) -> None:
        """Optimistic concurrency gate; never supplies a replacement apply baseline."""
        state = self.repository.require_case(case_id) if self.run_mode == "vnext" else self.ensure_case(case_id)
        expected_revision = snapshot.repository_revision if snapshot.repository_revision is not None else snapshot.case_revision
        if state.revision != expected_revision:
            raise CaseSnapshotMismatch("Turn snapshot is stale: canonical case revision changed")
        if state.reasoning_graph is None or state.reasoning_graph.model_dump(mode="json") != snapshot.graph.model_dump(mode="json"):
            raise CaseSnapshotMismatch("Turn snapshot is stale: canonical graph changed")
        if state.focus_node_id != snapshot.focus.node_id:
            raise CaseSnapshotMismatch("Turn snapshot is stale: canonical focus changed")
        current_sources = self.readable_sources(case_id)
        current_signatures = {
            source.id: hashlib.sha256(json.dumps(source.model_dump(mode="json"), sort_keys=True).encode()).hexdigest()
            for source in current_sources
        }
        if current_signatures != snapshot.visible_source_signatures:
            raise CaseSnapshotMismatch("Turn snapshot is stale: readable source registry changed")
        pending = next((item for item in reversed(state.evidence_request_history) if item.status is EvidenceRequestStatus.PENDING), None)
        snapshot_pending = snapshot.cycle.evidence_request if snapshot.cycle is not None else None
        if (pending.model_dump(mode="json") if pending else None) != (snapshot_pending.model_dump(mode="json") if snapshot_pending else None):
            raise CaseSnapshotMismatch("Turn snapshot is stale: pending request changed")

    def ensure_case(self, case_id: str, title: str = "Business Law Tutorial 5") -> CaseState:
        if not self.repository.exists(case_id):
            state = CaseState(case_id=case_id, title=title)
            self.repository.save(state)
            return state
        return self.repository.load(case_id)

    def update_case_title(self, case_id: str, title: str) -> CaseState:
        """Update only administrative case naming; substantive revision is unchanged."""
        with self._lock:
            state = self.repository.require_case(case_id) if self.run_mode == "vnext" else self.ensure_case(case_id)
            if state.case_kind == "sample":
                raise EvidenceRequestConflict("Sample case names are fixed")
            cleaned = title.strip()
            if not cleaned:
                raise ValueError("Case name is required")
            if len(cleaned) > 160:
                raise ValueError("Case name must be 160 characters or fewer")
            if cleaned == state.title:
                return state
            state.title = cleaned
            state.administrative_revision += 1
            state.administrative_activity.append({"type": "case_name_updated", "human_summary": "Case name updated."})
            state.last_updated_at = datetime.now(timezone.utc)
            self.repository.save(state)
            return state

    @staticmethod
    def _restore_clean_checkpoint(state: CaseState) -> CaseState:
        checkpoint = state.clean_checkpoint or {}
        if "state" in checkpoint:
            checkpoint_state = checkpoint["state"]
            expected_signature = checkpoint.get("signature")
        else:
            checkpoint_state = checkpoint
            expected_signature = None
        payload = state.model_dump(mode="json")
        payload.update(checkpoint_state)
        payload["clean_checkpoint"] = state.clean_checkpoint
        restored = CaseState.model_validate(payload)
        if expected_signature is not None and _state_signature(restored) != expected_signature:
            raise CaseSnapshotMismatch("Clean checkpoint identity does not match restored state")
        return restored

    def request_evidence(self, case_id: str, step: RequestEvidence | RequestInformation | dict, expected_case_revision: int | None = None) -> EvidenceRequest:
        with self._lock:
            state = self.ensure_case(case_id)
            self._check_revision(state, expected_case_revision)
            if self.run_mode == "vnext" and state.case_kind == "sample":
                raise EvidenceRequestConflict("Sample cases are read-only; reset the sample to restore its original configuration")
            if case_id in self._model_revision_active:
                raise EvidenceRequestConflict("A human request cannot be created while an Investigator revision is active")
            if any(item.status.value == "pending" for item in state.evidence_request_history):
                raise EvidenceRequestConflict("Only one pending human evidence request is allowed")
            if isinstance(step, (RequestEvidence, RequestInformation)):
                parsed = step
            elif step.get("type") == "request_information":
                parsed = RequestInformation.model_validate(step)
            else:
                parsed = RequestEvidence.model_validate(step)
            question = parsed.question if isinstance(parsed, RequestInformation) else parsed.information_sought
            target = parsed.target_uncertainty_id
            expected_value = parsed.expected_information_value
            reason = parsed.reason
            request = EvidenceRequest(
                request_id=allocate_evidence_request_id(state.evidence_request_history),
                target_uncertainty_id=target,
                information_sought=question or "Additional case information.",
                reason=reason,
                expected_information_value=expected_value,
                requested_at_revision=state.revision,
            )
            state.evidence_request_history.append(request)
            state.runtime_status = "WAITING_FOR_EVIDENCE"
            state.current_actor = "NONE"
            self._in_flight_actor.pop(case_id, None)
            state.last_updated_at = datetime.now(timezone.utc)
            state.revision += 1
            self.repository.save(state)
            self._publish_waiting_run(case_id, state, request)
            self.record_workspace_event(case_id, {"type": "request_created", "run_id": request.originating_run_id, "request_id": request.request_id, "runtime_status": "WAITING_FOR_EVIDENCE", "case_revision": state.revision, "human_summary": f"The Investigator requested: {request.information_sought}"})
            return request

    def persist_pending_request(self, case_id: str, request: EvidenceRequest) -> EvidenceRequest:
        """Persist the coordinator's canonical request without reconstructing it."""
        with self._lock:
            state = self.ensure_case(case_id)
            if any(item.status.value == "pending" for item in state.evidence_request_history):
                raise EvidenceRequestConflict("Only one pending human evidence request is allowed")
            if any(item.request_id == request.request_id for item in state.evidence_request_history):
                raise EvidenceRequestConflict(f"Evidence request ID already exists: {request.request_id}")
            request = request.model_copy(update={
                "originating_run_id": request.originating_run_id or self.current_run_id(case_id),
                "originating_actor": request.originating_actor or "investigator",
                "created_case_revision": request.created_case_revision if request.created_case_revision is not None else state.revision,
            })
            state.evidence_request_history.append(request)
            state.runtime_status = "WAITING_FOR_EVIDENCE"
            state.current_actor = "NONE"
            self._in_flight_actor.pop(case_id, None)
            state.last_updated_at = datetime.now(timezone.utc)
            state.revision += 1
            self.repository.save(state)
            self._publish_waiting_run(case_id, state, request)
            self.record_workspace_event(case_id, {"type": "request_created", "run_id": request.originating_run_id, "request_id": request.request_id, "runtime_status": "WAITING_FOR_EVIDENCE", "case_revision": state.revision, "human_summary": f"The Investigator requested: {request.information_sought}"})
            return request

    def current_pending_request(self, case_id: str) -> EvidenceRequest:
        state = self.repository.require_case(case_id) if self.run_mode == "vnext" else self.ensure_case(case_id)
        pending = [item for item in state.evidence_request_history if item.status.value == "pending"]
        if len(pending) != 1:
            raise EvidenceRequestConflict(f"Expected exactly one pending evidence request, found {len(pending)}")
        return pending[0]

    def readable_sources(self, case_id: str) -> list[Source]:
        """Return the currently admitted raw sources for the next Investigator prompt."""
        return list(self.ensure_case(case_id).sources.values())

    def add_direct_source(
        self,
        case_id: str,
        *,
        display_name: str,
        content: str,
        source_type: SourceType = SourceType.OTHER,
        metadata: dict[str, Any] | None = None,
        assessment_scope: GraphScope | dict[str, Any] | None = None,
        expected_case_revision: int | None = None,
    ) -> Source:
        """Admit one source for the vNext input workflow without graph mutation."""
        with self._lock:
            state = self.repository.require_case(case_id) if self.run_mode == "vnext" else self.ensure_case(case_id)
            self._assert_user_mutable(state)
            self._check_revision(state, expected_case_revision)
            if not display_name.strip() or not content.strip():
                raise ValueError("display_name and content are required")
            scope = GraphScope.model_validate(assessment_scope) if assessment_scope is not None else GraphScope(scope_type=GraphScopeType.CASE)
            if scope.scope_type is GraphScopeType.SUBJECT and scope.subject_id not in state.subjects:
                raise ValueError(f"Unknown subject ID: {scope.subject_id!r}")
            if scope.scope_type is GraphScopeType.RELATIONSHIP:
                relationship = state.subject_relationships.get(scope.relationship_id)
                if relationship is None:
                    raise ValueError(f"Unknown relationship ID: {scope.relationship_id!r}")
                if len(relationship.subject_ids) < 2:
                    raise ValueError("A relationship scope requires at least two subjects")
            source_metadata = dict(metadata or {})
            source_metadata["assessment_scope"] = scope.model_dump(mode="json")
            source = SourceRegistry.register_raw_source(
                state, display_name.strip(), content, source_metadata, add_to_graph=False
            )
            source = source.model_copy(update={"source_type": SourceType(source_type)})
            state.sources[source.id] = source
            state.revision += 1
            state.runtime_status = "COMPLETED" if state.runtime_status == "COMPLETED" else state.runtime_status
            state.last_updated_at = datetime.now(timezone.utc)
            self.repository.save(state)
        self.record_workspace_event(case_id, {"type": "source_added", "source_id": source.id, "runtime_status": state.runtime_status, "case_revision": state.revision, "human_summary": f"Source {source.name} was added to the case."})
        return source

    def add_subject(self, case_id: str, subject: dict[str, Any], expected_case_revision: int | None = None) -> AssessmentSubject:
        from investigator.models.assessment import AssessmentSubject
        with self._lock:
            state = self.repository.require_case(case_id) if self.run_mode == "vnext" else self.ensure_case(case_id)
            self._assert_user_mutable(state)
            self._check_revision(state, expected_case_revision)
            payload = dict(subject)
            if not payload.get("subject_id"):
                index = 1
                while f"subject_{index}" in state.subjects:
                    index += 1
                payload["subject_id"] = f"subject_{index}"
            item = AssessmentSubject.model_validate(payload)
            if item.subject_id in state.subjects:
                raise ValueError(f"Duplicate subject ID: {item.subject_id!r}")
            proposed_subjects = dict(state.subjects)
            proposed_subjects[item.subject_id] = item
            try:
                validate_configured_identifiers(proposed_subjects)
            except ValueError as exc:
                raise ValueError("Each student must have a distinct identifier.") from exc
            state.subjects = proposed_subjects
            state.revision += 1
            state.last_updated_at = datetime.now(timezone.utc)
            self.repository.save(state)
        self.record_workspace_event(case_id, {"type": "subject_added", "subject_id": item.subject_id, "case_revision": state.revision, "human_summary": f"Subject {item.display_name} was added."})
        return item

    def rename_subject(self, case_id: str, subject_id: str, display_name: str, expected_case_revision: int | None = None) -> AssessmentSubject:
        with self._lock:
            state = self.repository.require_case(case_id) if self.run_mode == "vnext" else self.ensure_case(case_id)
            self._assert_user_mutable(state)
            self._check_revision(state, expected_case_revision)
            item = state.subjects.get(subject_id)
            if item is None:
                raise ValueError(f"Unknown subject ID: {subject_id!r}")
            renamed = item.model_copy(update={"display_name": display_name.strip()})
            if not renamed.display_name:
                raise ValueError("display_name is required")
            proposed_subjects = dict(state.subjects)
            proposed_subjects[subject_id] = renamed
            try:
                validate_configured_identifiers(proposed_subjects)
            except ValueError as exc:
                raise ValueError("Each student must have a distinct identifier.") from exc
            state.subjects = proposed_subjects
            state.revision += 1
            state.last_updated_at = datetime.now(timezone.utc)
            self.repository.save(state)
        self.record_workspace_event(case_id, {"type": "subject_renamed", "subject_id": subject_id, "case_revision": state.revision, "human_summary": "A student was renamed."})
        return renamed

    def remove_subject(self, case_id: str, subject_id: str, expected_case_revision: int | None = None) -> None:
        with self._lock:
            state = self.repository.require_case(case_id) if self.run_mode == "vnext" else self.ensure_case(case_id)
            self._assert_user_mutable(state)
            self._check_revision(state, expected_case_revision)
            if subject_id not in state.subjects:
                raise ValueError(f"Unknown subject ID: {subject_id!r}")
            if len(state.subjects) <= 1:
                raise ValueError("A case must retain at least one student")
            if any(subject_id in relationship.subject_ids for relationship in state.subject_relationships.values()):
                raise ValueError("Cannot remove a student referenced by a relationship")
            del state.subjects[subject_id]
            state.revision += 1
            state.last_updated_at = datetime.now(timezone.utc)
            self.repository.save(state)
        self.record_workspace_event(case_id, {"type": "subject_removed", "subject_id": subject_id, "case_revision": state.revision, "human_summary": "A student was removed from the case."})

    def add_relationship(self, case_id: str, relationship: dict[str, Any], expected_case_revision: int | None = None) -> SubjectRelationship:
        from investigator.models.assessment import SubjectRelationship
        with self._lock:
            state = self.ensure_case(case_id)
            self._check_revision(state, expected_case_revision)
            item = SubjectRelationship.model_validate(relationship)
            if item.relationship_id in state.subject_relationships:
                raise ValueError(f"Duplicate relationship ID: {item.relationship_id!r}")
            if any(subject_id not in state.subjects for subject_id in item.subject_ids):
                raise ValueError("Relationship contains an unknown subject ID")
            if any(source_id not in state.sources for source_id in item.source_ids):
                raise ValueError("Relationship contains an unknown source ID")
            state.subject_relationships[item.relationship_id] = item
            state.revision += 1
            state.last_updated_at = datetime.now(timezone.utc)
            self.repository.save(state)
        self.record_workspace_event(case_id, {"type": "relationship_added", "relationship_id": item.relationship_id, "case_revision": state.revision, "human_summary": f"Relationship {item.relationship_id} was added."})
        return item

    def update_context(self, case_id: str, context: dict[str, Any], expected_case_revision: int | None = None) -> Any:
        from investigator.models.assessment import AssessmentContext
        with self._lock:
            state = self.ensure_case(case_id)
            self._check_revision(state, expected_case_revision)
            item = AssessmentContext.model_validate(context)
            state.assessment_context = item
            state.revision += 1
            state.last_updated_at = datetime.now(timezone.utc)
            self.repository.save(state)
        self.record_workspace_event(case_id, {"type": "context_updated", "case_revision": state.revision, "human_summary": "Assessment context was updated."})
        return item

    def respond(self, case_id: str, request_id: str, response: EvidenceRequestResponse | dict, sources: list[dict[str, Any]] | None = None, expected_case_revision: int | None = None) -> EvidenceRequest:
        with self._lock:
            state = self.ensure_case(case_id)
            self._check_revision(state, expected_case_revision)
            if self.run_mode == "vnext" and state.case_kind == "sample":
                raise EvidenceRequestConflict("Sample cases are read-only; reset the sample to restore its original configuration")
            if case_id in self._model_revision_active:
                raise EvidenceRequestConflict("Human evidence responses are blocked while an Investigator revision is active")
            pending = next((item for item in state.evidence_request_history if item.request_id == request_id and item.status.value == "pending"), None)
            if pending is None:
                raise EvidenceRequestConflict("Request is not the current pending evidence request")
            parsed = response if isinstance(response, EvidenceRequestResponse) else EvidenceRequestResponse.model_validate(response)
            if parsed.request_id != request_id:
                raise EvidenceRequestConflict("Response request_id does not match the URL request ID")
            submitted = list(sources or [])
            if parsed.status == "fulfilled" and not submitted:
                raise ValueError("FULFILLED requires at least one supplied source")
            if parsed.status == "fulfilled" and len(submitted) != 1:
                raise ValueError("FULFILLED accepts exactly one supplied source")
            if parsed.status == "unavailable" and submitted:
                raise ValueError("UNAVAILABLE cannot include supplied sources")
            source_ids: list[str] = []
            if parsed.status == "fulfilled":
                for source_data in submitted:
                    source = SourceRegistry.register_raw_source(
                        state,
                        str(source_data.get("display_name") or source_data.get("name") or "Supplied source"),
                        str(source_data.get("content") or ""),
                        dict(source_data.get("metadata") or {}),
                    )
                    source_ids.append(source.id)
            completed = pending.model_copy(update={"status": EvidenceRequestStatus(parsed.status), "released_source_ids": source_ids, "note": parsed.note, "fulfilled_case_revision": state.revision + 1})
            index = state.evidence_request_history.index(pending)
            state.evidence_request_history[index] = completed
            state.revision += 1
            state.runtime_status = "IDLE"
            state.current_actor = "NONE"
            state.last_updated_at = datetime.now(timezone.utc)
            self.repository.save(state)
        self.record_trace(case_id, {"event": "evidence_request_resolved", "request_id": request_id, "status": completed.status.value, "released_source_ids": list(completed.released_source_ids), "fulfilled_case_revision": completed.fulfilled_case_revision, "actor": "human"})
        self.record_workspace_event(case_id, {"type": "request_resolved", "request_id": request_id, "run_id": completed.originating_run_id, "runtime_status": "IDLE", "case_revision": completed.fulfilled_case_revision, "human_summary": f"Human evidence request {request_id} was marked {completed.status.value}."})
        if self.resume_callback:
            self.resume_callback(case_id)
            resumed_run_id = self.current_run_id(case_id)
            if resumed_run_id:
                with self._lock:
                    state = self.ensure_case(case_id)
                    current = next(item for item in state.evidence_request_history if item.request_id == request_id)
                    updated = current.model_copy(update={"resumed_run_id": resumed_run_id})
                    state.evidence_request_history[state.evidence_request_history.index(current)] = updated
                    self.repository.save(state)
                    completed = updated
        return completed

    def set_model_revision_active(self, case_id: str, active: bool) -> None:
        with self._lock:
            if active:
                self._model_revision_active.add(case_id)
            else:
                self._model_revision_active.discard(case_id)

    def get_workspace(self, case_id: str) -> dict[str, Any]:
        state = self.ensure_case(case_id)
        pending = next((item for item in reversed(state.evidence_request_history) if item.status.value == "pending"), None)
        runtime_status = state.runtime_status
        current_actor = state.current_actor
        if case_id in self._in_flight_actor and self.run_mode == "vnext":
            current_actor = "INVESTIGATOR"
            runtime_status = "RUNNING"
        elif case_id in self._in_flight_actor:
            current_actor = self._in_flight_actor[case_id]
            runtime_status = f"RUNNING_{current_actor}"
        messages: list[dict[str, Any]] = []
        if pending:
            request_payload = self._public_request(pending)
            messages.append({"id": f"request-{pending.request_id}", "role": "simplifynext", "text": "The available material leaves one important question open.", "request": request_payload})
        pending_payload = self._public_request(pending) if pending else None
        runs = self.get_runs(case_id)
        latest = runs[-1] if runs else None
        display_title = "Law Exam Investigation" if state.title == "Business Law Tutorial 5" else state.title
        return {"caseId": state.case_id, "caseRevision": state.revision, "title": display_title, "status": runtime_status.lower(), "caseStatus": state.case_status, "runtimeStatus": runtime_status, "currentActor": current_actor, "institutionalStatus": "Investigating" if state.case_status == "ACTIVE" else state.case_status.title(), "currentFocus": pending.information_sought if pending else "Review the current case state.", "messages": messages, "chatHistory": [], "workspaceTurns": [], "workspaceEvents": self.workspace_events(case_id), "visibleSources": [{"id": source.id, "name": source.name, "sourceType": source.source_type.value, "content": source.content or "", "contentPreview": (source.content or "")[:180], "metadata": source.metadata} for source in state.sources.values()], "assessmentContext": state.assessment_context.model_dump(mode="json") if state.assessment_context else None, "subjects": [item.model_dump(mode="json") for item in sorted(state.subjects.values(), key=lambda item: item.subject_id)], "relationships": [item.model_dump(mode="json") for item in sorted(state.subject_relationships.values(), key=lambda item: item.relationship_id)], "pendingEvidenceRequest": pending_payload, "requestHistory": [self._public_request(item) for item in state.evidence_request_history], "runs": runs, "lastError": state.last_error, "lastTraceStep": state.last_trace_step, "lastUpdatedAt": state.last_updated_at.isoformat(), "latestRun": latest}

    def _assessment_guidance(self, case_id: str) -> dict[str, Any]:
        """Build internal report material before projecting it for Help."""
        state = self.repository.require_case(case_id) if self.run_mode == "vnext" else self.ensure_case(case_id)
        runs = self.get_runs(case_id)
        latest = next((run for run in reversed(runs) if run.get("vnext_status") == "completed"), None)
        if latest and self.run_mode == "vnext":
            try:
                record = self._report_record(case_id, latest)
            except (FileNotFoundError, ReportIntegrityError):
                record = None
            if record is not None:
                assessments = []
                for student in record.get("students", []):
                    findings = []
                    for violation in student.get("violations", []):
                        findings.append({
                            "violation_id": violation.get("violation_id"),
                            "status": violation.get("status"),
                            "reasoning_summary": violation.get("reasoning_summary", ""),
                            "unresolved_points": violation.get("unresolved_points", []),
                            "supporting_material": [{"statement": item.get("statement", ""), "source_labels": [source.get("file_name", "") for source in item.get("sources", [])]} for item in violation.get("supporting_material", [])],
                            "mitigating_material": [{"statement": item.get("statement", ""), "source_labels": [source.get("file_name", "") for source in item.get("sources", [])]} for item in violation.get("limiting_material", [])],
                        })
                    assessments.append({
                        "subject_id": student.get("subject_id"),
                        "subject_display_name": student.get("display_name", "Student"),
                        "subject_candidate_number": student.get("candidate_number"),
                        "violation_assessments": findings,
                        "furthest_conclusion": {"statement": student.get("furthest_conclusion", "")},
                    })
                return {
                    "case_id": case_id,
                    "title": record.get("case_name_at_assessment", state.title),
                    "assessment_context": None,
                    "subjects": [{"subject_id": item.get("subject_id"), "display_name": item.get("display_name"), "candidate_number": item.get("candidate_number")} for item in record.get("students", [])],
                    "relationships": [],
                    "source_inventory": [{"id": item.get("source_id"), "name": item.get("filename"), "source_type": "document", "scope": None, "metadata": {}} for item in record.get("sources", [])],
                    "current_case_revision": state.revision,
                    "latest_successful_vnext_run": latest,
                    "latest_assessment_revision": record.get("assessment_input_revision"),
                    "assessment_is_stale": int(record.get("assessment_input_revision") or 0) < int(state.revision),
                    "per_subject_assessments": assessments,
                    "final_graph": None,
                    "run_status": state.runtime_status,
                    "preset": record.get("preset", {}),
                }
        result: dict[str, Any] | None = None
        if latest and latest.get("vnext_result_path"):
            path = Path(str(latest["vnext_result_path"]))
            if not path.is_absolute():
                path = self.repository.run_dir(case_id, str(latest["run_id"])) / path.name
            if path.is_file():
                payload = json.loads(path.read_text(encoding="utf-8"))
                result = payload.get("result")
        subject_lookup = state.subjects
        graph_nodes = ((result or {}).get("graph") or {}).get("nodes", {})
        def material(node_id: str) -> dict[str, Any]:
            node = graph_nodes.get(node_id)
            if not node:
                return {"id": node_id, "statement": "Referenced material is unavailable."}
            metadata = node.get("metadata", {})
            source_ids = metadata.get("source_ids", [])
            if metadata.get("source_id"):
                source_ids = [metadata["source_id"]]
            return {"id": node_id, "statement": node.get("statement", "Referenced material is unavailable."), "source_labels": [state.sources[source_id].name for source_id in source_ids if source_id in state.sources]}
        assessments = []
        for assessment in (result or {}).get("subject_assessments", []):
            subject = subject_lookup.get(assessment.get("subject_id"))
            item = dict(assessment)
            item["subject_display_name"] = subject.display_name if subject else assessment.get("subject_id")
            item["subject_candidate_number"] = subject.candidate_number if subject else None
            for violation in item.get("violation_assessments", []):
                violation["supporting_material"] = [material(node_id) for node_id in violation.get("supporting_node_ids", [])]
                violation["mitigating_material"] = [material(node_id) for node_id in violation.get("mitigating_node_ids", [])]
            assessments.append(item)
        assessment_revision = latest.get("start_revision") if latest else None
        return {
            "case_id": state.case_id,
            "title": state.title,
            "assessment_context": state.assessment_context.model_dump(mode="json") if state.assessment_context else None,
            "subjects": [item.model_dump(mode="json") for item in sorted(state.subjects.values(), key=lambda item: item.subject_id)],
            "relationships": [item.model_dump(mode="json") for item in sorted(state.subject_relationships.values(), key=lambda item: item.relationship_id)],
            "source_inventory": [{"id": item.id, "name": item.name, "source_type": item.source_type.value, "scope": item.metadata.get("assessment_scope"), "metadata": item.metadata} for item in sorted(state.sources.values(), key=lambda item: item.id)],
            "current_case_revision": state.revision,
            "latest_successful_vnext_run": latest,
            "latest_assessment_revision": assessment_revision,
            "assessment_is_stale": assessment_revision is not None and assessment_revision < state.revision,
            "per_subject_assessments": assessments,
            "final_graph": (result or {}).get("graph"),
            "run_status": state.runtime_status,
        }

    def get_guidance_context(self, case_id: str) -> dict[str, Any]:
        """Return only human-facing, read-only context for the vNext Help Agent."""
        from investigator.public_views import document_format

        state = self.repository.require_case(case_id) if self.run_mode == "vnext" else self.ensure_case(case_id)
        internal = self._assessment_guidance(case_id)
        preset_payload = internal.get("preset")
        labels = {item["violation_id"]: item["label"] for item in preset_payload.get("violations", [])} if isinstance(preset_payload, dict) and preset_payload.get("violations") else {item.violation_id: item.label for item in preset_for_case(state).violations}
        assessments: list[dict[str, Any]] = []
        for assessment in internal["per_subject_assessments"]:
            safe_violations = []
            for violation in assessment.get("violation_assessments", []):
                safe_violations.append({
                    "label": labels.get(violation.get("violation_id"), "Configured violation"),
                    "status": violation.get("status"),
                    "reasoning_summary": _public_semantic_text(state, violation.get("reasoning_summary", "")),
                    "unresolved_points": [_public_semantic_text(state, point) for point in violation.get("unresolved_points", [])],
                    "supporting_material": [{"statement": _public_semantic_text(state, item.get("statement", "")), "source_labels": list(item.get("source_labels", []))} for item in violation.get("supporting_material", [])],
                    "limiting_material": [{"statement": _public_semantic_text(state, item.get("statement", "")), "source_labels": list(item.get("source_labels", []))} for item in violation.get("mitigating_material", [])],
                })
            conclusion = assessment.get("furthest_conclusion") or {}
            assessments.append({
                "subject_display_name": assessment.get("subject_display_name", "Student"),
                "subject_candidate_number": assessment.get("subject_candidate_number"),
                "violation_assessments": safe_violations,
                "furthest_conclusion": {**conclusion, "statement": _public_semantic_text(state, conclusion.get("statement", ""))},
            })
        return {
            "case_name": state.title,
            "assessment": {
                "state": "stale" if internal["assessment_is_stale"] else ("complete" if internal["latest_successful_vnext_run"] else "not_started"),
                "assessment_is_stale": internal["assessment_is_stale"],
                "runtime_status": state.runtime_status,
            },
            "students": [{"display_name": item.display_name, "candidate_number": item.candidate_number} for item in sorted(state.subjects.values(), key=lambda item: item.subject_id)],
            "sources": [{"file_name": item.name, "document_format": document_format(item.name)} for item in sorted(state.sources.values(), key=lambda item: item.id)],
            "policy_context": ([{"label": item["label"], "rule_text": item["rule_text"], "prohibited_conduct": item["prohibited_conduct"]} for item in preset_payload.get("violations", [])] if isinstance(preset_payload, dict) and preset_payload.get("violations") else [{"label": item.label, "rule_text": item.rule_text, "prohibited_conduct": item.prohibited_conduct} for item in preset_for_case(state).violations]),
            "per_subject_assessments": assessments,
            "unresolved_points": sorted({point for assessment in assessments for violation in assessment["violation_assessments"] for point in violation["unresolved_points"]}),
            "run_status": state.runtime_status,
        }

    def _successful_run(self, case_id: str, run_id: str | None = None) -> dict[str, Any] | None:
        runs = self.get_runs(case_id)
        if run_id is not None:
            selected = next((item for item in runs if item.get("run_id") == run_id), None)
            if selected is None or selected.get("vnext_status") != "completed":
                raise KeyError("Assessment report not found")
            return selected
        return next((item for item in reversed(runs) if item.get("vnext_status") == "completed"), None)

    def _report_record(self, case_id: str, run: dict[str, Any]) -> dict[str, Any]:
        run_id = str(run["run_id"])
        path = self.repository.run_dir(case_id, run_id) / "report_record.json"
        if not path.is_file():
            raise FileNotFoundError(path)
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ReportIntegrityError("The assessment report could not be read safely.") from exc
        if not isinstance(record, dict) or record.get("case_id") != case_id or not record.get("run_instance_id"):
            raise ReportIntegrityError("The assessment report failed its integrity check.")
        return record

    def get_report(self, case_id: str, run_id: str | None = None) -> dict[str, Any]:
        """Project one immutable successful assessment behind a safe public DTO."""
        state = self.repository.require_case(case_id) if self.run_mode == "vnext" else self.ensure_case(case_id)
        run = self._successful_run(case_id, run_id)
        if run is None:
            return {
                "caseId": case_id,
                "currentCaseName": state.title,
                "reportState": "unavailable",
                "assessmentIsStale": False,
                "isLatestSuccessfulAssessment": False,
                "assessment": None,
            }
        try:
            record = self._report_record(case_id, run)
        except FileNotFoundError:
            return {
                "caseId": case_id,
                "currentCaseName": state.title,
                "reportState": "historical_unavailable",
                "assessmentIsStale": False,
                "isLatestSuccessfulAssessment": run_id is None,
                "message": "Historical report details are unavailable for this assessment. Run a new assessment to produce a report from the current case record.",
                "assessment": None,
            }
        latest = self._successful_run(case_id)
        latest_id = latest.get("run_id") if latest else None
        stale = int(record.get("assessment_input_revision") or 0) < int(state.revision)
        return public_report_from_record(
            record,
            current_case_name=state.title,
            report_state="stale" if stale else "current",
            is_latest_successful_assessment=run.get("run_id") == latest_id,
            run_handle=public_run_handle_for_instance(case_id, str(run["run_id"]), run.get("run_instance_id")),
        )

    def get_historical_source(self, case_id: str, run_id: str, source_handle: str) -> dict[str, Any]:
        """Read exactly one source version admitted by a selected report."""
        state = self.repository.require_case(case_id) if self.run_mode == "vnext" else self.ensure_case(case_id)
        del state  # The selected report, not current CaseState, is authoritative here.
        run = self._successful_run(case_id, run_id)
        if run is None:
            raise KeyError("Assessment report not found")
        record = self._report_record(case_id, run)
        run_instance_id = str(record["run_instance_id"])
        for source in record.get("sources", []):
            source_id = str(source.get("source_id"))
            if public_assessment_source_handle(case_id, run_instance_id, source_id) == source_handle:
                return {
                    "caseId": case_id,
                    "source": {
                        "sourceHandle": source_handle,
                        "fileName": source["filename"],
                        "documentFormat": source["document_format"],
                        "content": source.get("content", ""),
                        "assessmentDate": record.get("completed_at"),
                    },
                }
        raise KeyError("Historical source not found")

    @staticmethod
    def _public_request(request: EvidenceRequest) -> dict[str, Any]:
        """Expose the human-facing question without internal targeting metadata."""
        return {
            "request_id": request.request_id,
            "informationSought": request.information_sought,
            "reason": request.reason,
            "status": request.status.value,
            "originating_run_id": request.originating_run_id,
            "originating_actor": request.originating_actor,
            "resumed_run_id": request.resumed_run_id,
        }

    def record_workspace_event(self, case_id: str, event: dict[str, Any]) -> dict[str, Any]:
        self._event_sequence += 1
        record = {"event_id": f"workspace_event_{self._event_sequence:06d}", "created_at": datetime.now(timezone.utc).isoformat(), **event}
        self._workspace_events.setdefault(case_id, []).append(record)
        return record

    def workspace_events(self, case_id: str) -> list[dict[str, Any]]:
        return [dict(event) for event in self._workspace_events.get(case_id, [])]

    def get_traces(self, case_id: str) -> list[dict[str, Any]]:
        return [dict(trace) for trace in self.ensure_case(case_id).trace_history]

    def get_runs(self, case_id: str) -> list[dict[str, Any]]:
        root = self.repository.runs_dir(case_id)
        if not root.is_dir():
            return []
        runs = []
        for directory in sorted(root.iterdir()):
            result = directory / "run_result.json"
            if result.is_file():
                try:
                    payload = json.loads(result.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    # A run may be initializing on another thread; omit it until
                    # its first result snapshot is complete.
                    continue
                runs.append({key: payload.get(key) for key in ("run_id", "run_instance_id", "started_at", "ended_at", "termination_reason", "final_runtime_status", "outcome_type", "originating_actor", "start_revision", "run_start_revision", "latest_safe_revision", "final_case_revision", "final_committed_revision", "final_error", "pending_request_id", "request_text", "trace_path", "vnext_status", "vnext_result_path", "report_record_path", "vnext_furthest_conclusion", "vnext_subject_conclusions", "model", "model_calls", "proposal_correction_calls", "clean_execution_retries", "correction_retries", "input_tokens", "output_tokens", "latency_seconds", "finish_reason")})
        return runs

    def raw_trace_path(self, case_id: str, run_id: str) -> Path:
        if not re.fullmatch(r"run_\d{6}", run_id):
            raise ValueError("Invalid run ID")
        path = self._run_dir(case_id, run_id) / "raw_traces.jsonl"
        if not path.is_file():
            raise KeyError("Run not found")
        return path

    def sanitized_raw_trace(self, case_id: str, run_id: str) -> bytes:
        """Return the exact trace records with credential-like fields redacted."""
        path = self.raw_trace_path(case_id, run_id)
        sensitive = ("access_key", "secret", "session_token", "authorization", "credential")

        def scrub(value: Any, key: str = "") -> Any:
            if any(term in key.lower() for term in sensitive):
                return "[REDACTED]"
            if isinstance(value, dict):
                return {name: scrub(item, name) for name, item in value.items()}
            if isinstance(value, list):
                return [scrub(item, key) for item in value]
            return value

        lines = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                lines.append(json.dumps(scrub(json.loads(line)), default=str))
            except json.JSONDecodeError:
                lines.append(line)
        return ("\n".join(lines) + ("\n" if lines else "")).encode("utf-8")

    @staticmethod
    def _check_revision(state: CaseState, expected_case_revision: int | None) -> None:
        if expected_case_revision is not None and expected_case_revision != state.revision:
            raise EvidenceRequestConflict("The case revision is stale")

    def _assert_mutable(self, state: CaseState) -> None:
        if self.run_mode == "vnext" and (state.runtime_status in {"RUNNING", "RUNNING_INVESTIGATOR", "RUNNING_STEWARD"} or state.case_id in self._in_flight_actor):
            raise EvidenceRequestConflict("Assessment is running; wait for it to finish before changing the case")

    def _assert_user_mutable(self, state: CaseState) -> None:
        self._assert_mutable(state)
        if self.run_mode == "vnext" and state.case_kind == "sample":
            raise EvidenceRequestConflict("Sample cases are read-only; reset the sample to restore its original configuration")

    def _publish_waiting_run(self, case_id: str, state: CaseState, request: EvidenceRequest) -> None:
        run_id = self.current_run_id(case_id)
        if not run_id:
            return
        path = self._run_dir(case_id, run_id) / "run_result.json"
        if not path.is_file():
            return
        result = json.loads(path.read_text(encoding="utf-8"))
        result.update({"final_runtime_status": "WAITING_FOR_EVIDENCE", "outcome_type": "WAITING_FOR_EVIDENCE", "pending_request_id": request.request_id, "request_text": request.information_sought})
        self._write_run_result(path, result)
