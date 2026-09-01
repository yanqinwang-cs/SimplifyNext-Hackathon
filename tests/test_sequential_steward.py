from experiments.steward_screen.scenarios import all_scenarios, expanded_scenarios
from experiments.steward_screen.sequential import MultipleValidCase, first_move_is_acceptable, run_trajectory


def test_sequential_runner_emits_one_operation_and_recomputes_state() -> None:
    result = run_trajectory(all_scenarios()[0], max_steps=2)
    assert result.steps
    assert all("operation" in step and "before" in step and "after" in step for step in result.steps)


def test_sequential_runner_has_step_cap_and_detects_stale_keep() -> None:
    result = run_trajectory(all_scenarios()[0], max_steps=2)
    assert len(result.steps) <= 2
    assert result.termination == "step_cap"
    assert "NO_PROGRESS_LOOP" in result.failures


def test_expanded_cases_remain_unique_for_sequential_input() -> None:
    scenarios = expanded_scenarios()
    assert len({scenario.scenario_id for scenario in scenarios}) == len(scenarios)


def test_multiple_valid_case_accepts_any_declared_legal_first_move() -> None:
    case = MultipleValidCase("ambiguous-1", frozenset({"archive", "reactivate"}))
    assert first_move_is_acceptable("archive", case)
    assert first_move_is_acceptable("reactivate", case)
    assert not first_move_is_acceptable("stop_unresolved", case)
