"""Bounded production Investigator/Steward orchestration for the human loop."""

import hashlib
import json
from pathlib import Path
from typing import Any
from pydantic import BaseModel, RootModel, TypeAdapter

from investigator.cycle import CycleStatus, InvestigatorCycleCoordinator, InvestigatorTurnResponse, TurnSnapshot
from investigator.cycle_prompt import build_investigator_cycle_prompt
from investigator.graph import CaseGraph, GraphNode, GraphNodeType
from investigator.llm import ModelCallResult, ModelClient, ModelParseError
from investigator.roles import InvestigationFocus
from investigator.roles.procedure import correction_guidance, render_procedure
from investigator.roles.steward import ProductionStewardDecision, StewardDecision, StewardReviewContext, render_production_steward_contract
from investigator.services.evidence_requests import CaseSnapshotMismatch, HumanEvidenceWorkflow
from investigator.state.case_state import CaseState
from investigator.sources import SourceRegistry


MAX_ORCHESTRATION_STEPS = 12
MAX_MODEL_CALLS = 8
RECENT_INVESTIGATOR_TURN_LIMIT = 3


def _snapshot_hash(snapshot: TurnSnapshot) -> str:
    safe = {
        "case_revision": snapshot.case_revision,
        "graph_node_ids": sorted(snapshot.graph.nodes),
        "graph_edge_ids": sorted(snapshot.graph.edges),
        "focus_node_id": snapshot.focus.node_id,
        "active_reasoning_node_ids": sorted(snapshot.active_reasoning_node_ids),
        "visible_source_ids": sorted(source.id for source in snapshot.visible_sources),
        "visible_source_signatures": snapshot.visible_source_signatures,
    }
    return hashlib.sha256(json.dumps(safe, sort_keys=True).encode()).hexdigest()


def _recent_investigator_actions(workflow: HumanEvidenceWorkflow, case_id: str) -> list[dict[str, Any]]:
    completed = [trace for trace in workflow.get_traces(case_id) if trace.get("actor") == "investigator" and trace.get("event") == "investigator_completed"]
    return [
        {
            "revision": trace.get("committed_case_revision"),
            "changes": trace.get("committed_graph_changes", []),
            "next_step": (trace.get("parsed_response") or {}).get("next_step", {}).get("type", "unknown"),
            "correction_used": int(trace.get("attempts", 1)) > 1,
        }
        for trace in completed[-RECENT_INVESTIGATOR_TURN_LIMIT:]
    ]


def _committed_graph_changes(response: InvestigatorTurnResponse, before: CaseGraph, after: CaseGraph) -> list[dict[str, Any]]:
    """Summarize committed turn facts without retaining hidden model reasoning."""
    new_node_ids = set(after.nodes) - set(before.nodes)
    changes: list[dict[str, Any]] = []
    for update in response.graph_updates:
        payload = update.model_dump(mode="json")
        operation = payload["operation"]
        change: dict[str, Any] = {"operation": operation}
        node_id = payload.get("node_id")
        if node_id is None and operation.startswith("add_"):
            matches = [identifier for identifier in new_node_ids if after.nodes[identifier].statement == payload.get("statement")]
            node_id = matches[0] if len(matches) == 1 else None
        if node_id and node_id in after.nodes:
            node = after.nodes[node_id]
            change.update({"node_id": node.id, "node_type": node.node_type.value, "statement": node.statement})
        elif payload.get("statement"):
            change["statement"] = payload["statement"]
        source_ids = payload.get("source_ids")
        if source_ids:
            change["source_ids"] = source_ids
        affected = [payload.get(name) for name in ("source_node_id", "target_node_id", "derived_proposition_id", "child_hypothesis_id", "parent_hypothesis_id", "focus_node_id") if payload.get(name)]
        if affected:
            change["affected_node_ids"] = affected
        changes.append(change)
    return changes


def _initial_graph(case_id: str) -> CaseGraph:
    return CaseGraph(case_id=case_id, nodes={"U1": GraphNode(id="U1", node_type=GraphNodeType.UNCERTAINTY, statement="What remains unresolved in the current case record?")}, edges={})


def _checkpoint_state(state: CaseState) -> dict[str, Any]:
    return {
        "sources": {key: value.model_dump(mode="json") for key, value in state.sources.items()},
        "evidence": {}, "entities": {}, "claims": {}, "hypotheses": {}, "transformations": [],
        "uncertainties": {}, "conflicts": {}, "evidence_correction_history": [],
        "reasoning_graph": state.reasoning_graph.model_dump(mode="json"),
        "focus_node_id": state.focus_node_id, "focus_recent_node_ids": [], "focus_recent_region_node_ids": [], "revision": 0, "evidence_request_history": [],
        "case_status": "ACTIVE", "runtime_status": "IDLE", "current_actor": "NONE",
        "last_error": None, "last_trace_step": None,
    }


def _checkpoint_signature(state: CaseState) -> str:
    source_hashes = {
        source_id: hashlib.sha256(json.dumps(source.model_dump(mode="json"), sort_keys=True).encode()).hexdigest()
        for source_id, source in sorted(state.sources.items())
    }
    payload = {"graph": state.reasoning_graph.model_dump(mode="json"), "focus_node_id": state.focus_node_id, "revision": state.revision, "pending_request": None, "sources": source_hashes}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def reset_demo_case(workflow: HumanEvidenceWorkflow, case_id: str) -> None:
    """Replace the controlled demo case with one verified canonical initial state."""
    existing = workflow.ensure_case(case_id)
    state = CaseState(case_id=case_id, title=existing.title, description=existing.description)
    fixture_root = Path(__file__).resolve().parents[3] / "experiments" / "stage2_request_driven" / "fixtures" / "visible"
    filenames = ("blank_tutorial.md", "student_script.md", "marker_report.md", "invigilator_report.md", "assessment_rules.md", "assessment_logistics.md")
    for filename in filenames:
        SourceRegistry.register_raw_source(state, filename, (fixture_root / filename).read_text(encoding="utf-8"), {"demo_seed": True})
    state.reasoning_graph = _initial_graph(case_id)
    state.focus_node_id = "U1"
    state.revision = 0
    expected_sources = {f"S{number}" for number in range(20, 26)}
    if set(state.sources) != expected_sources or set(state.reasoning_graph.nodes) != {"U1"} or state.reasoning_graph.edges or state.focus_node_id != "U1" or state.revision != 0 or state.evidence_request_history:
        raise RuntimeError("Controlled demo initial state verification failed")
    checkpoint_state = _checkpoint_state(state)
    state.clean_checkpoint = {"state": checkpoint_state, "signature": _checkpoint_signature(state)}
    workflow.repository.save(state)


def seed_demo_case(workflow: HumanEvidenceWorkflow, case_id: str) -> None:
    """Backward-compatible explicit demo initialization/reset helper."""
    reset_demo_case(workflow, case_id)


def _assert_clean_baseline(state: CaseState) -> None:
    checkpoint = state.clean_checkpoint or {}
    if state.revision == 0 and checkpoint.get("signature") and _checkpoint_signature(state) != checkpoint["signature"]:
        raise CaseSnapshotMismatch("Clean checkpoint does not match the canonical case state")


class ProductionInvestigationRunner:
    def __init__(self, workflow: HumanEvidenceWorkflow, client: ModelClient, *, seed_demo_sources: bool = True) -> None:
        self.workflow = workflow
        self.client = client
        self.seed_demo_sources = seed_demo_sources

    def run(self, case_id: str) -> None:
        if self.seed_demo_sources and not self.workflow.ensure_case(case_id).clean_checkpoint:
            seed_demo_case(self.workflow, case_id)
        state = self.workflow.ensure_case(case_id)
        _assert_clean_baseline(state)
        run_start_revision = state.revision
        graph = state.reasoning_graph or _initial_graph(case_id)
        focus_id = state.focus_node_id or "U1"
        coordinator = InvestigatorCycleCoordinator(graph, InvestigationFocus(node_id=focus_id, recent_node_ids=state.focus_recent_node_ids, recent_region_node_ids=state.focus_recent_region_node_ids), case_revision=state.revision, full_graph_visibility=True, evidence_request_history=state.evidence_request_history)
        model_calls = 0
        self.workflow.record_trace(case_id, {"event": "run_started", "step": 0, "actor": "system", "runtime_status": "RUNNING_INVESTIGATOR", "case_revision": coordinator.cycle.case_revision, "run_start_revision": run_start_revision, "latest_safe_revision": run_start_revision})
        for step in range(1, MAX_ORCHESTRATION_STEPS + 1):
            if coordinator.cycle.status is CycleStatus.WAITING_FOR_EVIDENCE:
                return
            if coordinator.cycle.status is CycleStatus.STOPPED:
                self.workflow.set_runtime(case_id, "STOPPED", "NONE")
                self.workflow.record_trace(case_id, {"event": "stopped", "step": step, "actor": "system", "runtime_status": "STOPPED", "case_revision": coordinator.cycle.case_revision, "resumable": True, "human_summary": "Autonomous investigation paused for human review; unresolved issues remain preserved."})
                return
            if model_calls >= MAX_MODEL_CALLS:
                self.workflow.set_runtime(case_id, "IDLE", "NONE")
                self.workflow.record_trace(case_id, {"event": "idle", "step": step, "actor": "system", "runtime_status": "IDLE", "case_revision": coordinator.cycle.case_revision, "reason": "model-call safety bound reached"})
                return
            if coordinator.cycle.status is CycleStatus.AWAITING_STEWARD:
                self._steward_turn(case_id, coordinator, step, run_start_revision)
                model_calls += 1
                self.workflow.save_reasoning_state(case_id, coordinator.graph, coordinator.focus.node_id, case_revision=coordinator.cycle.case_revision, focus=coordinator.focus)
                continue
            self._investigator_turn(case_id, coordinator, step, run_start_revision)
            model_calls += 1
            if coordinator.cycle.status is CycleStatus.WAITING_FOR_EVIDENCE:
                self.workflow.persist_pending_request(case_id, coordinator.cycle.evidence_request)
            self.workflow.save_reasoning_state(case_id, coordinator.graph, coordinator.focus.node_id, case_revision=coordinator.cycle.case_revision, focus=coordinator.focus)
            if coordinator.cycle.status is CycleStatus.WAITING_FOR_EVIDENCE:
                return
        self.workflow.set_runtime(case_id, "IDLE", "NONE")

    def _investigator_turn(self, case_id: str, coordinator: InvestigatorCycleCoordinator, step: int, run_start_revision: int) -> None:
        self.workflow.set_runtime(case_id, "RUNNING_INVESTIGATOR", "INVESTIGATOR", step=step)
        canonical = self.workflow.ensure_case(case_id)
        snapshot = coordinator.turn_snapshot(self.workflow.readable_sources(case_id), repository_revision=canonical.revision)
        self.workflow.assert_turn_snapshot_current(case_id, snapshot)
        observation = coordinator.observation(snapshot=snapshot)
        recent_actions = _recent_investigator_actions(self.workflow, case_id)
        prompt = build_investigator_cycle_prompt(observation, recent_actions)
        contract_diagnostics = coordinator.contract_check(observation, prompt, snapshot=snapshot)
        trace: dict[str, Any] = {"case_id": case_id, "event": "investigator_started", "step": step, "actor": "investigator", "runtime_status": "RUNNING_INVESTIGATOR", "case_revision": coordinator.cycle.case_revision, "run_start_revision": run_start_revision, "turn_start_revision": canonical.revision, "snapshot_case_revision": snapshot.case_revision, "snapshot_graph_node_ids": sorted(snapshot.graph.nodes), "investigator_visible_graph_node_ids": sorted(observation.local_graph.nodes), "active_reasoning_node_ids": sorted(observation.local_graph.nodes), "legal_graph_node_ids": sorted(observation.local_graph.nodes), "visible_source_ids": sorted(source.id for source in observation.visible_sources), "prompt_source_ids": sorted(source.id for source in observation.visible_sources), "recent_investigator_turn_count": len(recent_actions), "recent_investigator_revision_ids": [item["revision"] for item in recent_actions], "snapshot_hash": _snapshot_hash(snapshot), "prompt_hash": hashlib.sha256(prompt.encode()).hexdigest(), "raw_output": None}
        trace["contract_check"] = contract_diagnostics
        try:
            call = self._call_with_retry(prompt, InvestigatorTurnResponse, lambda value: coordinator.validate_turn(value, snapshot=snapshot), trace, "Investigator")
        except Exception as exc:
            trace.update({"event": "investigator_failed", "error": str(exc), "failure_category": type(exc).__name__})
            trace["final_case_revision"] = self.workflow.ensure_case(case_id).revision
            trace["latest_safe_revision"] = trace["final_case_revision"]
            trace["failed_turn_start_revision"] = trace["turn_start_revision"]
            self.workflow.record_trace(case_id, trace)
            raise
        trace.update({"event": "investigator_completed", "raw_output": call.raw_output, "parsed_response": call.parsed.model_dump(mode="json"), "input_tokens": call.metadata.input_tokens, "output_tokens": call.metadata.output_tokens, "latency_seconds": call.metadata.latency_seconds})
        self.workflow.assert_turn_snapshot_current(case_id, snapshot)
        coordinator.apply_turn(call.parsed, snapshot=snapshot)
        trace.update({"committed_case_revision": coordinator.cycle.case_revision, "final_case_revision": coordinator.cycle.case_revision, "committed_graph_changes": _committed_graph_changes(call.parsed, snapshot.graph, coordinator.graph)})
        self.workflow.record_trace(case_id, trace)

    def _steward_turn(self, case_id: str, coordinator: InvestigatorCycleCoordinator, step: int, run_start_revision: int) -> None:
        self.workflow.set_runtime(case_id, "RUNNING_STEWARD", "STEWARD", step=step)
        context = StewardReviewContext(global_frontier_assessed=True, local_frontier_exhausted=True, available_action_ids=[], materially_usable_action_ids=[], active_unresolved_ids=[node.id for node in coordinator.graph.nodes.values() if node.node_type is GraphNodeType.UNCERTAINTY and node.status.value == "active"], obvious_useful_region_remains=False)
        canonical = self.workflow.ensure_case(case_id)
        snapshot = coordinator.turn_snapshot(self.workflow.readable_sources(case_id), repository_revision=canonical.revision)
        self.workflow.assert_turn_snapshot_current(case_id, snapshot)
        steward_contract = render_production_steward_contract()
        prompt = json.dumps({"role": "Case Steward", "procedure": render_procedure("steward"), "output_contract": steward_contract, "instruction": 'Return exactly one JSON object using the "operation" discriminator; do not use "decision", "action", or "choice".', "graph": snapshot.graph.model_dump(mode="json"), "focus": snapshot.focus.model_dump(mode="json"), "review_context": context.model_dump(mode="json")}, sort_keys=True)
        trace: dict[str, Any] = {"case_id": case_id, "event": "steward_started", "step": step, "actor": "steward", "runtime_status": "RUNNING_STEWARD", "case_revision": coordinator.cycle.case_revision, "run_start_revision": run_start_revision, "turn_start_revision": canonical.revision, "snapshot_case_revision": snapshot.case_revision, "snapshot_graph_node_ids": sorted(snapshot.graph.nodes), "active_reasoning_node_ids": sorted(snapshot.active_reasoning_node_ids), "legal_graph_node_ids": sorted(snapshot.active_reasoning_node_ids), "visible_source_ids": sorted(source.id for source in snapshot.visible_sources), "snapshot_hash": _snapshot_hash(snapshot), "prompt_hash": hashlib.sha256(prompt.encode()).hexdigest(), "raw_output": None}
        try:
            call = self._call_with_retry(prompt, StewardEnvelope, lambda value: _parse_and_validate_steward(value, coordinator, context, snapshot), trace, "Steward", contract=steward_contract)
        except Exception as exc:
            trace.update({"event": "steward_failed", "error": str(exc), "failure_category": type(exc).__name__})
            trace["final_case_revision"] = self.workflow.ensure_case(case_id).revision
            trace["latest_safe_revision"] = trace["final_case_revision"]
            trace["failed_turn_start_revision"] = trace["turn_start_revision"]
            self.workflow.record_trace(case_id, trace)
            raise
        self.workflow.assert_turn_snapshot_current(case_id, snapshot)
        coordinator.apply_steward_decision(call.parsed, snapshot.case_revision, context, snapshot=snapshot)
        trace.update({"event": "steward_completed", "raw_output": call.raw_output, "parsed_response": call.parsed.model_dump(mode="json"), "input_tokens": call.metadata.input_tokens, "output_tokens": call.metadata.output_tokens, "latency_seconds": call.metadata.latency_seconds})
        self.workflow.record_trace(case_id, trace)
        if coordinator.cycle.status is CycleStatus.STOPPED:
            self.workflow.set_runtime(case_id, "STOPPED", "NONE")

    def _call_with_retry(self, prompt: str, schema: type, validator, trace: dict[str, Any], role: str, *, contract: str | None = None) -> ModelCallResult:
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                self.workflow.record_model_attempt(trace.get("case_id", ""), correction=attempt == 1)
                call = self.client.call(prompt, schema)
                try:
                    validated = validator(call.parsed)
                    if isinstance(validated, BaseModel):
                        call = call.model_copy(update={"parsed": validated})
                except Exception as exc:
                    last_error = exc
                    trace.setdefault("validation_errors", []).append(str(exc))
                    trace["initial_raw_output" if attempt == 0 else "correction_raw_output"] = call.raw_output
                    trace["initial_parsed_response" if attempt == 0 else "correction_parsed_response"] = call.parsed.model_dump(mode="json")
                    trace["initial_operation_error" if attempt == 0 else "correction_operation_error"] = str(exc)
                    if attempt == 0:
                        prompt += f"\n\nOPERATION VALIDATION FAILED\n{exc}\nPROCEDURAL GUIDANCE: {correction_guidance(exc)}\nPRESERVE THE INTENDED ACTION IF IT REMAINS SEMANTICALLY JUSTIFIED; repair structure and required fields without inventing a new case theory.\nEXACT CURRENT {role.upper()} OUTPUT CONTRACT:\n{contract or 'Use the exact production schema.'}\nReturn one complete replacement {role} response satisfying the exact production schema and current legal state."
                        continue
                    raise RuntimeError(f"{role.upper()}_OPERATION_VALIDATION_FAILURE: {exc}") from exc
                trace["attempts"] = attempt + 1
                return call
            except ModelParseError as exc:
                last_error = exc
                trace["raw_output"] = exc.raw_output
                trace["initial_raw_output" if attempt == 0 else "correction_raw_output"] = exc.raw_output
                trace["initial_schema_error" if attempt == 0 else "correction_schema_error"] = str(exc)
                trace["validation_errors"] = trace.get("validation_errors", []) + [str(exc)]
                if attempt == 0:
                    prompt += f"\nPROCEDURAL GUIDANCE: {correction_guidance(exc)}\nEXACT CURRENT {role.upper()} OUTPUT CONTRACT:\n{contract or 'Use the exact production schema.'}\nReturn one complete replacement response satisfying the exact production schema."
        assert last_error is not None
        raise last_error


def default_production_run(case_id: str, workflow: HumanEvidenceWorkflow) -> None:
    from investigator.llm.bedrock import BedrockModelClient
    ProductionInvestigationRunner(workflow, BedrockModelClient()).run(case_id)


class StewardEnvelope(RootModel[dict[str, object]]):
    """Provider JSON envelope validated against the production Steward union."""


def _parse_and_validate_steward(value: StewardEnvelope, coordinator: InvestigatorCycleCoordinator, context: StewardReviewContext, snapshot=None) -> ProductionStewardDecision:
    decision = TypeAdapter(ProductionStewardDecision).validate_python(value.root)
    coordinator.validate_steward_decision(decision, coordinator.cycle.case_revision, context, snapshot=snapshot)
    return decision
