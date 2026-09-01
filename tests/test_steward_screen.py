from investigator.llm import ModelCallMetadata, ModelCallResult, ModelParseError

from experiments.steward_screen.evaluate import evaluate_result
from experiments.steward_screen.models import MODEL_REGISTRY
from experiments.steward_screen.prompt import build_prompt
from experiments.steward_screen.report import aggregate
from experiments.steward_screen.runner import JsonObject, jobs
from experiments.steward_screen.scenarios import all_scenarios


def decision_payload(scenario):
    payload = {"assessment": "The state supports this case-management decision.", "reason": "The structural view supports it."}
    payload["operation"] = scenario.expected_operation
    if scenario.expected_target_node_id:
        payload["target_node_id"] = scenario.expected_target_node_id
    if scenario.expected_destination_node_id:
        payload["destination_node_id"] = scenario.expected_destination_node_id
    if scenario.expected_operation == "handoff_to_human":
        payload.update({"important_unresolved_ids": [], "reopening_conditions": "New relevant evidence.", "handoff_summary": "Return the case to a human decision-maker."})
    return payload


def fake_result(payload):
    return ModelCallResult(parsed=JsonObject(root=payload), raw_output=payload, metadata=ModelCallMetadata(provider="mock", model="screen-test", latency_seconds=0.01, parse_success=True))


def test_exactly_twelve_valid_scenarios_and_expected_decisions_apply() -> None:
    scenarios = all_scenarios()
    assert len(scenarios) == 12
    assert len({scenario.scenario_id for scenario in scenarios}) == 12
    for scenario in scenarios:
        for identifier in [scenario.expected_target_node_id, scenario.expected_destination_node_id]:
            if identifier:
                assert identifier in scenario.graph.nodes
        result = evaluate_result("mock", "mock", scenario, 1, build_prompt(scenario), fake_result(decision_payload(scenario)))
        assert result.schema_valid and result.coordinator_accepted and result.post_state_correct


def test_jobs_expand_cartesian_product_and_report_aggregates() -> None:
    scenario_ids = [scenario.scenario_id for scenario in all_scenarios()[:2]]
    selected_model = next(iter(MODEL_REGISTRY))
    expanded = jobs([selected_model], scenario_ids, 3)
    assert len(expanded) == 6
    results = [evaluate_result("mock", "mock", all_scenarios()[0], index, "prompt", fake_result(decision_payload(all_scenarios()[0]))) for index in range(1, 4)]
    summary = aggregate(results)
    assert summary.by_model["mock"]["runs"] == 3
    assert summary.by_model["mock"]["fully_correct"] == 3


def test_malformed_output_is_recorded_as_schema_failure() -> None:
    scenario = all_scenarios()[0]
    result = evaluate_result("mock", "mock", scenario, 1, "prompt", error=ModelParseError("bad JSON", raw_output="not json"))
    assert result.error_category == "schema_parse"
    assert result.raw_model_output == "not json"
    assert not result.schema_valid
