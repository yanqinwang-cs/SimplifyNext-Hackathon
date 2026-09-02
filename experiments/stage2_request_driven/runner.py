"""Offline dry-run and deterministic scripted Stage 2B experiment runner."""
import argparse
import hashlib
import json
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from pydantic import RootModel, TypeAdapter

from investigator.cycle import InvestigatorCycleCoordinator
from investigator.graph import CaseGraph, EdgeRelation, GraphEdge, GraphNode, GraphNodeType, make_edge_id
from investigator.models import EvidenceItem, EvidenceKind
from investigator.cycle import InvestigatorTurnResponse
from investigator.roles.steward import StewardDecision
from investigator.roles import InvestigationFocus
from investigator.roles.steward import StewardDecision, StewardReviewContext
from investigator.cycle_prompt import build_investigator_cycle_prompt
from investigator.llm import ModelCallResult, ModelParseError
from investigator.llm.bedrock import BedrockModelClient
from investigator.services import CaseService, HumanEvidenceWorkflow
from investigator.state import CaseRepository

from .environment import ControlledEvidenceEnvironment
from .fixtures import VISIBLE_FILENAMES, fixture_root, hidden_fixtures, initial_case
from experiments.steward_screen.models import MODEL_REGISTRY

CASE_ID = "stage2b-case-01"
MAX_INVESTIGATOR_TENURE = 4
MAX_MODEL_CALLS = 24
MAX_ORCHESTRATION_STEPS = 60


class JsonObject(RootModel[dict[str, object]]):
    """Provider envelope; the production Steward union is validated afterward."""


class OperationValidationError(ValueError):
    """A schema-valid role response rejected by deterministic domain rules."""

    def __init__(self, role: str, message: str) -> None:
        self.role = role
        super().__init__(message)


def sha(value: object) -> str:
    data = value if isinstance(value, bytes) else json.dumps(value, sort_keys=True, default=str).encode()
    return hashlib.sha256(data).hexdigest()


def graph() -> CaseGraph:
    return CaseGraph(case_id=CASE_ID, nodes={"U1": GraphNode(id="U1", node_type=GraphNodeType.UNCERTAINTY, statement="What best explains the credible concerns arising from Candidate A's assessment record and conduct?")}, edges={})


def manifest() -> dict[str, object]:
    visible = {name: sha((fixture_root() / "visible" / name).read_bytes()) for name in VISIBLE_FILENAMES}
    hidden = {fixture.filename: sha(fixture.content) for fixture in hidden_fixtures()}
    return {"suite": "stage2-request-driven", "suite_version": "stage2b-v1", "case_id": CASE_ID, "visible_source_filenames": list(VISIBLE_FILENAMES), "visible_source_hashes": visible, "hidden_fixture_hashes": hidden, "fixture_suite_hash": sha({"visible": visible, "hidden": hidden}), "hidden_files_exposed_to_models": False, "hidden_fixture_count": len(hidden), "matcher_hash": sha(Path(__file__).with_name("matcher.py").read_bytes()), "runner_hash": sha(Path(__file__).read_bytes()), "prompt_hash": sha("No model prompt is used by the controlled offline environment."), "schema_hashes": {"investigator_turn": sha(InvestigatorTurnResponse.model_json_schema()), "steward_decision": sha(TypeAdapter(StewardDecision).json_schema())}, "step_caps": {"max_model_calls": 0, "max_pending_requests": 1}, "git_sha": subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False).stdout.strip()}


def scripted(output: Path) -> dict[str, object]:
    repository = CaseRepository(output / "cases")
    state = initial_case()
    repository.save(state)
    workflow = HumanEvidenceWorkflow(repository)
    environment = ControlledEvidenceEnvironment(workflow)
    coordinator = InvestigatorCycleCoordinator(graph(), InvestigationFocus(node_id="U1"))
    service = CaseService(repository)
    traces: list[dict[str, object]] = []
    hidden_audit: list[dict[str, object]] = []
    releases: list[dict[str, object]] = []

    requests = ["Please provide the permitted items and belongings recorded at entry.", "Please provide the eyewear capabilities examination record.", "Please provide the identified eyewear usage activity record.", "Please provide the assessment period network and internet connectivity records.", "Please provide device and phone account linkage records.", "Please provide impossible material not represented by this controlled environment."]
    for index, text in enumerate(requests):
        step = {"type": "request_evidence", "target_uncertainty_id": "U1", "information_sought": text, "reason": "This material may reduce the active uncertainty.", "expected_information_value": "It may distinguish the remaining explanations."}
        response = {"graph_updates": [], "next_step": step}
        if index == 0:
            response["graph_updates"] = [{"operation": "add_hypothesis", "node_id": "H1", "statement": "A substantive explanation remains to be tested.", "reason": "Record a candidate explanation after inspecting the visible case material."}]
        coordinator.apply_turn(response)
        request = workflow.request_evidence(CASE_ID, step)
        started = time.perf_counter()
        result = environment.respond(CASE_ID, request)
        elapsed = time.perf_counter() - started
        state = repository.load(CASE_ID)
        coordinator.complete_evidence_request({"request_id": request.request_id, "status": result.workflow_status, "released_source_ids": [result.source_id] if result.source_id else [], "note": result.note})
        if result.source_id:
            source = state.sources[result.source_id]
            service.add_evidence(CASE_ID, EvidenceItem(id=f"E{20 + index}", source_id=result.source_id, raw_content=source.content or "", kind=EvidenceKind.RECORD))
        releases.append({"request_id": request.request_id, "status": result.status, "source_id": result.source_id})
        hidden_audit.append({"request_id": request.request_id, "matched_fixture_key": result.match.fixture.key if result.match.fixture else None, "quality": result.match.quality, "reason": result.match.reason})
        traces.append({"step": index + 1, "request_id": request.request_id, "observation": {"visible_source_ids": sorted(state.sources), "graph_node_ids": sorted(coordinator.observation().local_graph.nodes)}, "environment_outcome": {"status": result.status, "source_id": result.source_id, "note": result.note}, "latency_seconds": elapsed, "model_calls": 0})
    coordinator.apply_turn({"graph_updates": [], "next_step": {"type": "request_steward_review", "reason": "The controlled local evidence path is complete."}})
    (output / "raw_traces.jsonl").write_text("\n".join(json.dumps(item, sort_keys=True) for item in traces) + "\n", encoding="utf-8")
    final_state = repository.load(CASE_ID)
    (output / "final_case_state.json").write_text(json.dumps(final_state.model_dump(mode="json"), indent=2) + "\n", encoding="utf-8")
    (output / "final_source_registry.json").write_text(json.dumps({k: v.model_dump(mode="json") for k, v in final_state.sources.items()}, indent=2) + "\n", encoding="utf-8")
    (output / "request_history.json").write_text(json.dumps([r.model_dump(mode="json") for r in final_state.evidence_request_history], indent=2) + "\n", encoding="utf-8")
    (output / "hidden_audit.json").write_text(json.dumps(hidden_audit, indent=2) + "\n", encoding="utf-8")
    (output / "final_handoff.json").write_text(json.dumps({"case_id": CASE_ID, "status": coordinator.cycle.status.value, "reason": coordinator.cycle.handoff_reason}, indent=2) + "\n", encoding="utf-8")
    return {"mode": "scripted", "model_calls": 0, "releases": releases, "final_status": coordinator.cycle.status.value}


def _steward_prompt(coordinator: InvestigatorCycleCoordinator) -> str:
    context = _steward_context(coordinator)
    return json.dumps({"role": "Case Steward", "instruction": "Return exactly one JSON StewardDecision. The final institutional judgement remains human.", "graph": coordinator.graph.model_dump(mode="json"), "focus": coordinator.focus.model_dump(mode="json"), "review_context": context.model_dump(mode="json")}, sort_keys=True)


def _steward_context(coordinator: InvestigatorCycleCoordinator) -> StewardReviewContext:
    return StewardReviewContext(
        global_frontier_assessed=True,
        local_frontier_exhausted=True,
        available_action_ids=[],
        materially_usable_action_ids=[],
        active_unresolved_ids=sorted(coordinator.graph.nodes[node_id].id for node_id in coordinator.graph.nodes if coordinator.graph.nodes[node_id].node_type is GraphNodeType.UNCERTAINTY),
        obvious_useful_region_remains=False,
    )


def _call_with_one_correction(client: BedrockModelClient, prompt: str, schema: type, trace: dict[str, object], validator: Callable[[object], None] | None = None) -> ModelCallResult:
    last: ModelParseError | None = None
    for attempt in range(2):
        try:
            call = client.call(prompt, schema)
            trace["attempts"] = attempt + 1
            if validator is not None:
                try:
                    validator(call.parsed)
                except Exception as exc:
                    trace.setdefault("operation_validation_errors", []).append(str(exc))
                    trace["initial_raw_output" if attempt == 0 else "correction_raw_output"] = call.raw_output
                    trace["initial_parsed_response" if attempt == 0 else "correction_parsed_response"] = call.parsed.model_dump(mode="json")
                    if attempt == 0:
                        prompt = prompt + "\n\nOPERATION VALIDATION FAILED\n" + str(exc) + "\nReturn one complete replacement response satisfying the exact production schema and current legal state."
                        continue
                    raise OperationValidationError("INVESTIGATOR", str(exc)) from exc
            return call
        except ModelParseError as exc:
            last = exc
            trace["raw_output"] = exc.raw_output
            trace["attempts"] = attempt + 1
            if attempt == 0:
                prompt = prompt + "\nReturn only a JSON object valid under the exact schema."
    assert last is not None
    raise last


def apply_investigator_result(coordinator: InvestigatorCycleCoordinator, call: ModelCallResult, trace: dict[str, object]) -> None:
    """Record a successful model result before applying it to the coordinator."""
    parsed = call.parsed.model_dump(mode="json")
    trace.update({
        "raw_output": call.raw_output,
        "parsed_response": parsed,
        "graph_updates": parsed.get("graph_updates", []),
        "next_step": parsed.get("next_step"),
        "attempted_focus_destination": (parsed.get("next_step") or {}).get("focus_node_id"),
        "legal_graph_node_ids": sorted(coordinator.legal_node_ids()),
        "input_tokens": call.metadata.input_tokens,
        "output_tokens": call.metadata.output_tokens,
    })
    coordinator.apply_turn(call.parsed)


def call_and_validate_steward(client: BedrockModelClient, prompt: str, trace: dict[str, object], validator: Callable[[object], None] | None = None) -> tuple[ModelCallResult, object]:
    """Keep provider JSON and raw text before validating the Steward union."""
    last_error: Exception | None = None
    operation_validation_failed = False
    for attempt in range(2):
        try:
            call = client.call(prompt, JsonObject)
            trace.update({"raw_output": call.raw_output, "provider_json": call.parsed.root, "attempts": attempt + 1, "input_tokens": call.metadata.input_tokens, "output_tokens": call.metadata.output_tokens})
            try:
                decision = TypeAdapter(StewardDecision).validate_python(call.parsed.root)
            except Exception as exc:
                last_error = exc
                trace.update({"validation_error": str(exc)})
                trace["initial_raw_output" if attempt == 0 else "correction_raw_output"] = call.raw_output
                trace["initial_parsed_response" if attempt == 0 else "correction_parsed_response"] = call.parsed.root
                if attempt == 0:
                    prompt = prompt + "\nReturn one complete replacement response satisfying the exact production StewardDecision schema."
                continue
            if validator is not None:
                try:
                    validator(decision)
                except Exception as exc:
                    operation_validation_failed = True
                    last_error = exc
                    trace.update({"validation_error": str(exc)})
                    trace.setdefault("operation_validation_errors", []).append(str(exc))
                    trace["initial_raw_output" if attempt == 0 else "correction_raw_output"] = call.raw_output
                    trace["initial_parsed_response" if attempt == 0 else "correction_parsed_response"] = call.parsed.root
                    if attempt == 0:
                        prompt = prompt + "\n\nOPERATION VALIDATION FAILED\n" + str(exc) + "\nReturn one complete replacement response satisfying the exact production StewardDecision schema and current legal state."
                        continue
                    break
            else:
                return call, decision
            if not operation_validation_failed:
                return call, decision
        except ModelParseError as exc:
            last_error = exc
            trace.update({"raw_output": exc.raw_output, "attempts": attempt + 1, "validation_error": str(exc)})
            if attempt == 0:
                prompt = prompt + "\nReturn one complete replacement response satisfying the exact production StewardDecision schema."
    assert last_error is not None
    if operation_validation_failed:
        raise OperationValidationError("STEWARD", str(last_error)) from last_error
    raise ModelParseError(f"Steward structured output failed validation: {last_error}", raw_output=trace.get("raw_output")) from last_error


def live(model_name: str, output: Path) -> dict[str, object]:
    spec = MODEL_REGISTRY[model_name]
    client = BedrockModelClient(model_id=spec.invocation_id, region=spec.region)
    repository = CaseRepository(output / "cases")
    repository.save(initial_case())
    workflow = HumanEvidenceWorkflow(repository)
    environment = ControlledEvidenceEnvironment(workflow)
    coordinator = InvestigatorCycleCoordinator(graph(), InvestigationFocus(node_id="U1"), max_turns_per_tenure=MAX_INVESTIGATOR_TENURE)
    traces: list[dict[str, object]] = []
    usage = {"investigator_calls": 0, "steward_calls": 0, "input_tokens": 0, "output_tokens": 0}
    termination = "ORCHESTRATION_LIMIT"
    for step in range(1, MAX_ORCHESTRATION_STEPS + 1):
        if coordinator.cycle.status.value == "stopped":
            termination = "STOPPED"
            break
        if usage["investigator_calls"] + usage["steward_calls"] >= MAX_MODEL_CALLS:
            termination = "MODEL_BUDGET_EXHAUSTED"
            break
        if coordinator.cycle.status.value == "waiting_for_evidence":
            request = workflow.current_pending_request(CASE_ID)
            coordinator_request = coordinator.cycle.evidence_request
            if coordinator_request is None or coordinator_request.request_id != request.request_id:
                raise RuntimeError("Coordinator and persisted pending evidence request IDs diverged")
            before = sorted(repository.load(CASE_ID).sources)
            started = time.perf_counter()
            try:
                result = environment.respond(CASE_ID, request)
                coordinator.complete_evidence_request({"request_id": request.request_id, "status": result.workflow_status, "released_source_ids": [result.source_id] if result.source_id else [], "note": result.note})
                after = sorted(repository.load(CASE_ID).sources)
                traces.append({"step": step, "actor": "environment", "request_id": request.request_id, "information_sought": request.information_sought, "visible_source_check_result": "already_available" if result.visible_check.answerable else "not_available", "visible_source_match_reason": result.visible_check.reason, "hidden_matcher_invoked": not result.visible_check.answerable, "matcher_result": result.match.quality, "hidden_fixture_key": result.match.fixture.key if result.match.fixture else None, "matcher_reason": result.match.reason, "fulfilment_status": result.status, "fulfilment_request_id": request.request_id, "source_id": result.source_id, "source_registry_before": before, "source_registry_after": after, "request_status_before": "pending", "request_status_after": result.workflow_status, "retained_tenure_node_ids": sorted(coordinator.observation().local_graph.nodes), "latency_seconds": time.perf_counter() - started, "model_calls": 0})
            except Exception as exc:
                termination = "ENVIRONMENT_FAILURE"
                traces.append({"step": step, "actor": "environment", "failure_category": termination, "error": str(exc), "model_calls": 0})
                break
            continue
        if coordinator.cycle.status.value == "awaiting_steward":
            prompt = _steward_prompt(coordinator)
            trace: dict[str, object] = {"step": step, "actor": "steward", "case_revision": coordinator.cycle.case_revision, "focus": coordinator.focus.model_dump(mode="json"), "prompt_hash": sha(prompt), "raw_output": None, "parsed_response": None, "attempts": 0}
            started = time.perf_counter()
            usage["steward_calls"] += 1
            try:
                call, decision = call_and_validate_steward(client, prompt, trace, validator=lambda value: coordinator.validate_steward_decision(value, coordinator.cycle.case_revision, _steward_context(coordinator)))
                coordinator.apply_steward_decision(decision, coordinator.cycle.case_revision)
                trace.update({"raw_output": call.raw_output, "parsed_response": decision.model_dump(mode="json"), "input_tokens": call.metadata.input_tokens, "output_tokens": call.metadata.output_tokens, "latency_seconds": time.perf_counter() - started})
                if call.metadata.input_tokens: usage["input_tokens"] += call.metadata.input_tokens
                if call.metadata.output_tokens: usage["output_tokens"] += call.metadata.output_tokens
                traces.append(trace)
                if decision.operation == "stop_unresolved":
                    termination = "HANDOFF_TO_HUMAN"
                    break
            except ModelParseError as exc:
                termination = "STEWARD_SCHEMA_FAILURE"
                trace.update({"failure_category": termination, "error": str(exc), "raw_output": exc.raw_output, "latency_seconds": time.perf_counter() - started})
                traces.append(trace)
                break
            except OperationValidationError as exc:
                termination = "STEWARD_OPERATION_VALIDATION_FAILURE"
                trace.update({"failure_category": termination, "error": str(exc), "latency_seconds": time.perf_counter() - started})
                traces.append(trace)
                break
            except Exception as exc:
                termination = "STEWARD_APPLY_FAILURE"
                trace.update({"failure_category": termination, "error": str(exc), "latency_seconds": time.perf_counter() - started})
                traces.append(trace)
                break
            continue
        observation = coordinator.observation(environment.visible_sources(CASE_ID))
        prompt = build_investigator_cycle_prompt(observation)
        trace = {"step": step, "actor": "investigator", "case_revision": coordinator.cycle.case_revision, "focus_before": coordinator.focus.model_dump(mode="json"), "active_reasoning_node_ids": sorted(observation.local_graph.nodes), "visible_source_ids": sorted(source.id for source in observation.visible_sources), "prompt_source_ids": sorted(source.id for source in observation.visible_sources), "prompt_source_content_hashes": {source.id: sha(source.content or "") for source in observation.visible_sources}, "prompt_hash": sha(prompt), "raw_output": None, "parsed_response": None, "attempts": 0}
        trace["contract_check"] = coordinator.contract_check(observation, prompt)
        started = time.perf_counter()
        usage["investigator_calls"] += 1
        try:
            call = _call_with_one_correction(client, prompt, InvestigatorTurnResponse, trace, validator=coordinator.validate_turn)
            before_nodes = sorted(coordinator.graph.nodes)
            apply_investigator_result(coordinator, call, trace)
            trace["latency_seconds"] = time.perf_counter() - started
            if coordinator.cycle.evidence_request is not None:
                canonical = workflow.persist_pending_request(CASE_ID, coordinator.cycle.evidence_request)
                trace.update({"request_created": True, "canonical_request_id": canonical.request_id, "request_status": canonical.status.value, "case_revision_after_request": repository.load(CASE_ID).revision})
            trace.update({"focus_after": coordinator.focus.model_dump(mode="json"), "graph_node_ids_before": before_nodes, "graph_node_ids_after": sorted(coordinator.graph.nodes)})
            if call.metadata.input_tokens: usage["input_tokens"] += call.metadata.input_tokens
            if call.metadata.output_tokens: usage["output_tokens"] += call.metadata.output_tokens
            traces.append(trace)
        except ModelParseError as exc:
            termination = "INVESTIGATOR_SCHEMA_FAILURE"
            trace.update({"failure_category": termination, "error": str(exc), "raw_output": exc.raw_output, "latency_seconds": time.perf_counter() - started})
            traces.append(trace)
            break
        except OperationValidationError as exc:
            termination = "INVESTIGATOR_OPERATION_VALIDATION_FAILURE"
            trace.update({"failure_category": termination, "error": str(exc), "latency_seconds": time.perf_counter() - started, "active_reasoning_node_ids": sorted(coordinator.observation().local_graph.nodes), "legal_graph_node_ids": sorted(coordinator.legal_node_ids())})
            traces.append(trace)
            break
        except Exception as exc:
            termination = "INVESTIGATOR_APPLY_FAILURE"
            trace.update({"failure_category": termination, "error": str(exc), "latency_seconds": time.perf_counter() - started, "active_reasoning_node_ids": sorted(coordinator.observation().local_graph.nodes), "legal_graph_node_ids": sorted(coordinator.legal_node_ids())})
            traces.append(trace)
            break
    output.mkdir(parents=True, exist_ok=True)
    (output / "raw_traces.jsonl").write_text("\n".join(json.dumps(item, sort_keys=True) for item in traces) + "\n", encoding="utf-8")
    state = repository.load(CASE_ID)
    (output / "final_case_state.json").write_text(json.dumps(state.model_dump(mode="json"), indent=2) + "\n", encoding="utf-8")
    (output / "request_history.json").write_text(json.dumps([r.model_dump(mode="json") for r in state.evidence_request_history], indent=2) + "\n", encoding="utf-8")
    (output / "final_source_registry.json").write_text(json.dumps({k: v.model_dump(mode="json") for k, v in state.sources.items()}, indent=2) + "\n", encoding="utf-8")
    (output / "model_usage.json").write_text(json.dumps(usage, indent=2) + "\n", encoding="utf-8")
    if termination == "HANDOFF_TO_HUMAN":
        (output / "final_handoff.json").write_text(json.dumps(traces[-1].get("parsed_response"), indent=2) + "\n", encoding="utf-8")
    return {"mode": "live", "model_name": model_name, "invocation_id": spec.invocation_id, "region": spec.region, "model_calls": usage["investigator_calls"] + usage["steward_calls"], "termination": termination, "trace_count": len(traces)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--scripted", action="store_true")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--model", choices=sorted(MODEL_REGISTRY), default="anthropic.claude-opus-4-5")
    parser.add_argument("--output-dir", type=Path, default=Path("experiments/stage2_request_driven/results"))
    args = parser.parse_args()
    modes = sum(bool(value) for value in (args.dry_run, args.scripted, args.live))
    if modes != 1:
        parser.error("choose exactly one execution mode: --dry-run, --scripted, or --live")
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    result = {"mode": "dry-run", "model_calls": 0} if args.dry_run else (scripted(output) if args.scripted else live(args.model, output))
    (output / "manifest.json").write_text(json.dumps(manifest(), indent=2) + "\n", encoding="utf-8")
    (output / "run_result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print("FINAL OUTPUT LOCATION")
    labels = {"run_result.json": "RUN RESULT", "raw_traces.jsonl": "RAW TRACES", "final_case_state.json": "FINAL CASE STATE", "final_source_registry.json": "SOURCE REGISTRY", "request_history.json": "REQUEST HISTORY", "model_usage.json": "MODEL USAGE", "final_handoff.json": "FINAL HANDOFF"}
    for name in ("run_result.json", "raw_traces.jsonl", "final_case_state.json", "request_history.json", "final_source_registry.json", "model_usage.json", "final_handoff.json"):
        path = output / name
        if path.exists(): print(f"{labels[name]}: file://{path.resolve()}")
    print(f"RUN DIRECTORY: file://{output}")


if __name__ == "__main__":
    main()
