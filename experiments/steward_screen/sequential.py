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
