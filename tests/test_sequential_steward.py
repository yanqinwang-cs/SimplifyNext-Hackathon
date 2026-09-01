from experiments.steward_screen.scenarios import all_scenarios, expanded_scenarios, trajectory_scenarios
from experiments.steward_screen.sequential import MultipleValidCase, first_move_is_acceptable, run_trajectory, summarize_trajectories


def test_sequential_runner_emits_one_operation_and_recomputes_state() -> None:
    result = run_trajectory(all_scenarios()[0], max_steps=2)
    assert result.steps
    assert all("operation" in step and "before" in step and "after" in step for step in result.steps)


def test_sequential_runner_respects_step_cap() -> None:
    result = run_trajectory(all_scenarios()[0], max_steps=2)
    assert len(result.steps) <= 2
    assert result.termination in {"quiescent", "step_cap"}


def test_justified_keep_terminates_quiescently_when_no_frontier_remains() -> None:
    result = run_trajectory(all_scenarios()[0], max_steps=2)
    assert result.termination == "quiescent"
    assert "NO_PROGRESS_LOOP" not in result.failures


def test_expanded_cases_remain_unique_for_sequential_input() -> None:
    scenarios = expanded_scenarios()
    assert len({scenario.scenario_id for scenario in scenarios}) == len(scenarios)


def test_multiple_valid_case_accepts_any_declared_legal_first_move() -> None:
    case = MultipleValidCase("ambiguous-1", frozenset({"archive", "reactivate"}))
    assert first_move_is_acceptable("archive", case)
    assert first_move_is_acceptable("reactivate", case)
    assert not first_move_is_acceptable("stop_unresolved", case)


def test_trajectory_summary_reports_progress_failures_separately() -> None:
    summary = summarize_trajectories(all_scenarios()[:2], max_steps=2)
    assert summary["scenarios"] == 2
    assert summary["no_progress_loop"] == 0
    assert summary["terminated_step_cap"] == 0
    assert len(summary["details"]) == 2


def test_multi_operation_fixture_reassesses_after_each_transition() -> None:
    result = run_trajectory(trajectory_scenarios()[0], max_steps=4)
    assert len(result.steps) >= 2
    assert not result.failures
    assert result.termination == "quiescent"
