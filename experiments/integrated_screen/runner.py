from __future__ import annotations

import argparse
import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from experiments.steward_screen.models import MODEL_REGISTRY
from investigator.cycle import CycleStatus, InvestigatorCycleCoordinator
from investigator.llm import ModelCallResult, ModelClient, ModelParseError
from investigator.llm.bedrock import BedrockModelClient
from investigator.roles import StewardDecision, StewardReviewContext

from .environment import Stage1Environment
from .fixtures import Stage1Fixture, all_fixtures, fixture_map
from .prompt import build_investigator_prompt, build_steward_prompt
from .evaluate import evaluate_trajectory

MAX_INVESTIGATOR_TURNS_PER_TENURE = 3
MAX_MODEL_CALLS = 10
MAX_STEPS = 20


def _hash(value: Any) -> str:
    payload = value if isinstance(value, bytes) else json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(payload).hexdigest()


def _graph_hash(coordinator: InvestigatorCycleCoordinator) -> str:
    return _hash(coordinator.graph.model_dump(mode="json"))


def _graph_delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    before_nodes, after_nodes = before["nodes"], after["nodes"]
    before_edges, after_edges = before["edges"], after["edges"]
    return {
        "added_node_ids": sorted(set(after_nodes) - set(before_nodes)),
        "added_edge_ids": sorted(set(after_edges) - set(before_edges)),
        "removed_edge_ids": sorted(set(before_edges) - set(after_edges)),
        "node_status_changes": [{"node_id": identifier, "before": before_nodes[identifier]["status"], "after": after_nodes[identifier]["status"]} for identifier in sorted(set(before_nodes) & set(after_nodes)) if before_nodes[identifier]["status"] != after_nodes[identifier]["status"]],
    }


def live_review_context(coordinator: InvestigatorCycleCoordinator, environment: Stage1Environment) -> StewardReviewContext:
    available = environment.current_available_enquiries()
    unresolved = sorted(node.id for node in coordinator.graph.nodes.values() if node.node_type.value == "uncertainty" and node.status.value == "active")
    material = environment.materially_usable_action_ids()
    return StewardReviewContext(
        global_frontier_assessed=environment.global_frontier_assessed(),
        local_frontier_exhausted=not material,
        local_exhaustion_required=False,
        available_action_ids=[item.action_id for item in available],
        materially_usable_action_ids=material,
        active_unresolved_ids=unresolved,
        obvious_useful_region_remains=bool(material),
    )


def run_trajectory(
    fixture: Stage1Fixture,
    investigator_client: ModelClient,
    steward_client: ModelClient,
    *,
    max_investigator_turns_per_tenure: int = MAX_INVESTIGATOR_TURNS_PER_TENURE,
    max_model_calls: int = MAX_MODEL_CALLS,
    max_steps: int = MAX_STEPS,
) -> dict[str, Any]:
    environment = Stage1Environment.for_fixture(fixture)
    coordinator = InvestigatorCycleCoordinator(deepcopy(fixture.graph), fixture.focus.model_copy(deep=True), available_enquiries=environment.current_available_enquiries(), max_turns_per_tenure=max_investigator_turns_per_tenure)
    traces: list[dict[str, Any]] = []
    model_calls = 0
    termination = None

    def trace_base(step: int, actor: str, before: str, before_graph: dict[str, Any]) -> dict[str, Any]:
        return {"step": step, "actor": actor, "focus_before": coordinator.focus.node_id, "graph_fingerprint_before": before, "available_action_ids_before": [a.action_id for a in environment.current_available_enquiries()], "materially_usable_action_ids_before": environment.materially_usable_action_ids(), "recently_released_evidence_ids": [], "visible_released_evidence_ids": [], "steward_review_context": None, "raw_model_output": None, "parsed_response": None, "graph_delta": {}, "enquiry_requested": None, "environment_release": None, "executed_action_id": None, "completed_action_ids": sorted(environment.completed_action_ids), "steward_decision": None, "focus_after": None, "graph_fingerprint_after": None, "available_action_ids_after": None, "materially_usable_action_ids_after": None, "input_tokens": None, "output_tokens": None, "latency_seconds": None, "failure_category": None, "error": None, "_graph_before": before_graph}

    for step in range(1, max_steps + 1):
        if coordinator.cycle.status is CycleStatus.STOPPED:
            termination = "STOP_UNRESOLVED"
            break
        before_graph = coordinator.graph.model_dump(mode="json")
        before = _hash(before_graph)
        status = coordinator.cycle.status
        if status is CycleStatus.ENQUIRY_IN_FLIGHT:
            trace = trace_base(step, "environment", before, before_graph)
            action_id = coordinator.cycle.in_flight_enquiry.action_id
            try:
                release = environment.execute_enquiry(action_id)
                trace["executed_action_id"] = action_id
                coordinator.complete_enquiry_with_evidence(action_id, release.evidence)
                coordinator.set_available_enquiries(environment.current_available_enquiries())
                trace["environment_release"] = [node.model_dump(mode="json") for node in release.evidence]
                trace["completed_action_ids"] = sorted(environment.completed_action_ids)
            except Exception as exc:
                trace["failure_category"] = "ENVIRONMENT_RELEASE"
                trace["error"] = str(exc)
                traces.append(trace)
                termination = "FAIL / ENVIRONMENT_RELEASE"
                break
        elif status is CycleStatus.LOCAL_ACTIVE:
            if model_calls >= max_model_calls:
                termination = "FAIL / MODEL_BUDGET_EXCEEDED"
                break
            trace = trace_base(step, "investigator", before, before_graph)
            observation = coordinator.observation()
            trace["recently_released_evidence_ids"] = list(observation.recently_released_evidence_ids)
            trace["visible_released_evidence_ids"] = [identifier for identifier in observation.recently_released_evidence_ids if identifier in observation.local_graph.nodes]
            prompt = build_investigator_prompt(observation)
            trace["prompt_hash"] = _hash(prompt)
            try:
                call: ModelCallResult = investigator_client.call(prompt, __import__("investigator.cycle", fromlist=["InvestigatorTurnResponse"]).InvestigatorTurnResponse)
                model_calls += 1
                trace.update({"raw_model_output": call.raw_output, "parsed_response": call.parsed.model_dump(mode="json"), "input_tokens": call.metadata.input_tokens, "output_tokens": call.metadata.output_tokens, "latency_seconds": call.metadata.latency_seconds})
                response = call.parsed
                requested = getattr(response.next_step, "action_id", None)
                trace["enquiry_requested"] = requested
                coordinator.apply_turn(response)
            except ModelParseError as exc:
                model_calls += 1
                trace.update({"raw_model_output": exc.raw_output, "failure_category": "INVESTIGATOR_SCHEMA", "error": str(exc)})
                traces.append(trace)
                termination = "FAIL / INVESTIGATOR_SCHEMA"
                break
            except Exception as exc:
                model_calls += 1
                trace.update({"failure_category": "INVESTIGATOR_APPLY", "error": str(exc)})
                traces.append(trace)
                termination = "FAIL / INVESTIGATOR_APPLY"
                break
        else:
            if model_calls >= max_model_calls:
                termination = "FAIL / MODEL_BUDGET_EXCEEDED"
                break
            trace = trace_base(step, "steward", before, before_graph)
            context = live_review_context(coordinator, environment)
            trace["steward_review_context"] = context.model_dump(mode="json")
            prompt = build_steward_prompt(coordinator.steward_snapshot(), context)
            trace["prompt_hash"] = _hash(prompt)
            try:
                call = steward_client.call(prompt, _StewardDecisionSchema)
                model_calls += 1
                trace.update({"raw_model_output": call.raw_output, "parsed_response": call.parsed.model_dump(mode="json"), "input_tokens": call.metadata.input_tokens, "output_tokens": call.metadata.output_tokens, "latency_seconds": call.metadata.latency_seconds})
                trace["steward_decision"] = call.parsed.model_dump(mode="json")
                coordinator.apply_steward_decision(call.parsed, coordinator.cycle.case_revision, review_context=context)
            except ModelParseError as exc:
                model_calls += 1
                trace.update({"raw_model_output": exc.raw_output, "failure_category": "STEWARD_SCHEMA", "error": str(exc)})
                traces.append(trace)
                termination = "FAIL / STEWARD_SCHEMA"
                break
            except Exception as exc:
                trace.update({"failure_category": "STEWARD_APPLY", "error": str(exc)})
                traces.append(trace)
                termination = "FAIL / STEWARD_APPLY"
                break
        trace["graph_delta"] = _graph_delta(before_graph, coordinator.graph.model_dump(mode="json"))
        trace["focus_after"] = coordinator.focus.node_id
        trace["graph_fingerprint_after"] = _graph_hash(coordinator)
        trace["available_action_ids_after"] = [a.action_id for a in environment.current_available_enquiries()]
        trace["materially_usable_action_ids_after"] = environment.materially_usable_action_ids()
        trace.pop("_graph_before", None)
        traces.append(trace)
    else:
        termination = "FAIL / LOOP_BUDGET_EXCEEDED"
    if termination is None:
        termination = "QUIESCENT"
    result = {"fixture_id": fixture.fixture_id, "termination": termination, "model_calls": model_calls, "completed_action_ids": sorted(environment.completed_action_ids), "traces": traces, "final_graph": coordinator.graph.model_dump(mode="json"), "final_graph_fingerprint": _graph_hash(coordinator), "final_focus": coordinator.focus.model_dump(mode="json"), "final_environment": {"available_action_ids": [a.action_id for a in environment.current_available_enquiries()], "materially_usable_action_ids": environment.materially_usable_action_ids(), "completed_action_ids": sorted(environment.completed_action_ids)}}
    result["evaluation"] = evaluate_trajectory(result, fixture.requirements)
    return result


class _StewardDecisionSchema:
    @classmethod
    def model_validate(cls, value):
        return TypeAdapter(StewardDecision).validate_python(value)


def dry_run() -> dict[str, Any]:
    for fixture in all_fixtures():
        environment = Stage1Environment.for_fixture(fixture)
        coordinator = InvestigatorCycleCoordinator(deepcopy(fixture.graph), fixture.focus.model_copy(deep=True), available_enquiries=environment.current_available_enquiries(), max_turns_per_tenure=MAX_INVESTIGATOR_TURNS_PER_TENURE)
        build_investigator_prompt(coordinator.observation())
        build_steward_prompt(coordinator.steward_snapshot(), live_review_context(coordinator, environment))
    return {"dry_run": True, "fixture_ids": [fixture.fixture_id for fixture in all_fixtures()], "model_calls": 0, "max_model_calls": MAX_MODEL_CALLS, "max_steps": MAX_STEPS}


def manifest(model_investigator: str | None = None, model_steward: str | None = None) -> dict[str, Any]:
    fixtures = all_fixtures()
    public = [{"fixture_id": fixture.fixture_id, "description": fixture.description, "graph": fixture.graph.model_dump(mode="json"), "actions": [action.model_dump(mode="json") for action in fixture.available_enquiries]} for fixture in fixtures]
    hidden = [{"fixture_id": fixture.fixture_id, "hidden_audit_truth": fixture.hidden_audit_truth, "releases": {action: release.model_dump(mode="json") for action, release in fixture.releases.items()}, "requirements": fixture.requirements.model_dump(mode="json")} for fixture in fixtures]
    return {
        "suite": "integrated-stage1",
        "suite_version": "stage1-v2",
        "fixture_ids": [fixture.fixture_id for fixture in fixtures],
        "fixture_count": len(fixtures),
        "fixture_suite_hash": _hash(public),
        "hidden_environment_hash": _hash(hidden),
        "evaluator_requirements_hash": _hash([fixture.requirements.model_dump(mode="json") for fixture in fixtures]),
        "investigator_schema_hash": _hash(__import__("investigator.cycle", fromlist=["TURN_RESPONSE_ADAPTER"]).TURN_RESPONSE_ADAPTER.json_schema()),
        "steward_schema_hash": _hash(TypeAdapter(StewardDecision).json_schema()),
        "investigator_prompt_source_hash": _hash(Path(__file__).parents[2].joinpath("src/investigator/cycle_prompt.py").read_bytes()),
        "steward_prompt_source_hash": _hash(Path(__file__).with_name("prompt.py").read_bytes()),
        "evaluator_hash": _hash(Path(__file__).with_name("evaluate.py").read_bytes()),
        "caps": {"investigator_turns_per_tenure": MAX_INVESTIGATOR_TURNS_PER_TENURE, "model_calls": MAX_MODEL_CALLS, "steps": MAX_STEPS},
        "investigator_model": model_investigator,
        "steward_model": model_steward,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the deterministic Stage 1 Investigator + Steward screen.")
    parser.add_argument("--investigator-model", default="anthropic.claude-opus-4-5", choices=sorted(MODEL_REGISTRY))
    parser.add_argument("--steward-model", default="anthropic.claude-opus-4-5", choices=sorted(MODEL_REGISTRY))
    parser.add_argument("--fixtures", nargs="+", default=[fixture.fixture_id for fixture in all_fixtures()])
    parser.add_argument("--output-dir", type=Path, default=Path("experiments/integrated_screen/results"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    selected = fixture_map()
    unknown = sorted(set(args.fixtures) - set(selected))
    if unknown:
        parser.error(f"unknown fixture IDs: {unknown}; expected C1-C4")
    if args.dry_run:
        print(json.dumps(dry_run(), indent=2))
        return
    clients = [BedrockModelClient(model_id=MODEL_REGISTRY[name].invocation_id, region=MODEL_REGISTRY[name].region) for name in (args.investigator_model, args.steward_model)]
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    results = [run_trajectory(selected[name], clients[0], clients[1]) for name in args.fixtures]
    output = args.output_dir / run_id
    output.mkdir(parents=True, exist_ok=True)
    run_manifest = manifest(args.investigator_model, args.steward_model)
    run_manifest.update({"run_id": run_id, "selected_fixtures": args.fixtures})
    (output / "manifest.json").write_text(json.dumps(run_manifest, indent=2) + "\n")
    (output / "trajectory_results.json").write_text(json.dumps(results, indent=2, sort_keys=True) + "\n")
    with (output / "raw_traces.jsonl").open("w", encoding="utf-8") as handle:
        for result in results:
            for trace in result["traces"]:
                handle.write(json.dumps({"fixture_id": result["fixture_id"], **trace}, sort_keys=True) + "\n")
    print(f"Manifest: {output / 'manifest.json'}\nRaw traces: {output / 'raw_traces.jsonl'}\nTrajectory results: {output / 'trajectory_results.json'}")


if __name__ == "__main__":
    main()
