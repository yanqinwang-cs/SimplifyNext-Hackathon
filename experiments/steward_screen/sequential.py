"""Transparent sequential Steward evaluation for offline calibration."""

from dataclasses import dataclass, field
from copy import deepcopy

from pydantic import TypeAdapter

from investigator.roles import GraphInvestigationCoordinator, StewardDecision

from experiments.steward_screen.luna import produce
from experiments.steward_screen.prompt import build_prompt
from experiments.steward_screen.models import StewardScenario


_ADAPTER = TypeAdapter(StewardDecision)


@dataclass
class TrajectoryResult:
    steps: list[dict] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    termination: str = "step_cap"


@dataclass(frozen=True)
class MultipleValidCase:
    """Evaluator-side acceptance set; never passed to the producer."""
    scenario_id: str
    acceptable_operations: frozenset[str]


def first_move_is_acceptable(operation: str, case: MultipleValidCase) -> bool:
    return operation in case.acceptable_operations


def _fingerprint(coordinator: GraphInvestigationCoordinator) -> tuple:
    return (coordinator.focus.node_id, tuple(sorted((node.id, node.status.value) for node in coordinator.graph.nodes.values())), coordinator.stopped)


def run_trajectory(scenario: StewardScenario, max_steps: int = 6) -> TrajectoryResult:
    """Apply one legal-or-illegal Luna decision at a time and reassess state."""
    coordinator = GraphInvestigationCoordinator(deepcopy(scenario.graph), scenario.focus.model_copy(deep=True))
    result = TrajectoryResult()
    seen = {_fingerprint(coordinator)}
    for index in range(1, max_steps + 1):
        current = scenario.model_copy(deep=True)
        current.graph = coordinator.graph
        current.focus = coordinator.focus
        prompt = build_prompt(current)
        payload = produce(prompt, current)
        try:
            decision = _ADAPTER.validate_python(payload)
        except Exception:
            result.failures.append("NULL_OR_NO_DECISION")
            result.termination = "clear_failure"
            break
        before = _fingerprint(coordinator)
        try:
            coordinator.review_with_steward(decision, review_context=current.review_context)
        except Exception:
            result.failures.append("ILLEGAL_OPERATION")
            result.termination = "clear_failure"
            break
        after = _fingerprint(coordinator)
        result.steps.append({"step": index, "operation": decision.operation, "before": before, "after": after})
        if coordinator.stopped:
            result.termination = "stopped"
            break
        if after == before:
            if "NO_PROGRESS_LOOP" not in result.failures:
                result.failures.append("NO_PROGRESS_LOOP")
        elif after in seen and "OSCILLATION" not in result.failures:
            result.failures.append("OSCILLATION")
        seen.add(after)
    return result


def summarize_trajectories(scenarios: list[StewardScenario], max_steps: int = 6) -> dict:
    """Aggregate trajectory evidence without imposing an operation ordering."""
    outcomes = [run_trajectory(scenario, max_steps=max_steps) for scenario in scenarios]
    return {
        "scenarios": len(outcomes),
        "step_cap": max_steps,
        "terminated_stopped": sum(item.termination == "stopped" for item in outcomes),
        "terminated_clear_failure": sum(item.termination == "clear_failure" for item in outcomes),
        "terminated_step_cap": sum(item.termination == "step_cap" for item in outcomes),
        "illegal_operation": sum("ILLEGAL_OPERATION" in item.failures for item in outcomes),
        "no_progress_loop": sum("NO_PROGRESS_LOOP" in item.failures for item in outcomes),
        "oscillation": sum("OSCILLATION" in item.failures for item in outcomes),
        "steps": [len(item.steps) for item in outcomes],
        "details": [{"scenario_id": scenario.scenario_id, "steps": item.steps, "failures": item.failures, "termination": item.termination} for scenario, item in zip(scenarios, outcomes)],
    }
