from investigator.llm import ModelCallMetadata, ModelCallResult, ModelParseError

from experiments.steward_screen.evaluate import evaluate_result
from experiments.steward_screen.models import MODEL_REGISTRY
from experiments.steward_screen.prompt import build_prompt
from experiments.steward_screen.report import aggregate
from experiments.steward_screen.runner import JsonObject, jobs
from experiments.steward_screen.scenarios import all_scenarios, expanded_scenarios


def decision_payload(scenario):
    payload = {"assessment": "The state supports this case-management decision.", "reason": "The structural view supports it."}
    payload["operation"] = scenario.expected_operation
    if scenario.expected_target_node_id:
        payload["target_node_id"] = scenario.expected_target_node_id
    if scenario.expected_destination_node_id:
        payload["destination_node_id"] = scenario.expected_destination_node_id
    if scenario.expected_operation == "stop_unresolved":
        payload.update({"important_unresolved_ids": ["U1"], "reopening_conditions": "New relevant evidence."})
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


def test_claude_45_registry_entries_use_exact_us_inference_profiles() -> None:
    expected = {
        "anthropic.claude-haiku-4-5": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
        "anthropic.claude-sonnet-4-5": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
        "anthropic.claude-opus-4-5": "us.anthropic.claude-opus-4-5-20251101-v1:0",
    }
    for name, invocation_id in expected.items():
        spec = MODEL_REGISTRY[name]
        assert spec.name == name
        assert spec.invocation_id == invocation_id
        assert spec.region == "us-east-1"

    existing_models = (
        "zai.glm-5", "deepseek.v3.2", "moonshot.kimi-k2-thinking",
        "qwen.qwen3-next-80b-a3b", "openai.gpt-oss-120b-1:0",
        "amazon.nova-2-lite-v1:0", "zai.glm-4.7-flash",
    )
    assert all(MODEL_REGISTRY[name].invocation_id == name for name in existing_models)
    assert jobs(list(expected), ["K1"], 1) == [(name, "K1", 1) for name in expected]


def test_malformed_output_is_recorded_as_schema_failure() -> None:
    scenario = all_scenarios()[0]
    result = evaluate_result("mock", "mock", scenario, 1, "prompt", error=ModelParseError("bad JSON", raw_output="not json"))
    assert result.error_category == "schema_parse"
    assert result.raw_model_output == "not json"
    assert not result.schema_valid


def test_steward_prompt_uses_neutral_ordered_ontology_and_references() -> None:
    prompt = build_prompt(all_scenarios()[0])
    for section in ["<ROLE>", "<INVESTIGATIVE_PURPOSE>", "<OBJECT_LEGEND>", "<RELATION_LEGEND>", "<POLICY_CONTEXT>", "<POLICY_DISCIPLINE>", "<STEWARD_OPERATIONS>", "<AUTHORITY_BOUNDARY>", "<CASE_PARTICIPANTS>", "<CASEGRAPH>", "<CURRENT_FOCUS>", "<FOCUS_HISTORY>", "<DETERMINISTIC_FEATURES>", "<OUTPUT_CONTRACT>"]:
        assert section in prompt
    assert "PERSON1" in prompt and "PERSON2" in prompt
    assert '"id": "H1"' in prompt and '"id": "E1"' in prompt and "R1.1" in prompt
    assert "determine guilt" not in prompt.lower()
    assert "choose an exact enquiry or tool" in prompt


def test_steward_prompt_exposes_exact_zero_shot_output_contract() -> None:
    prompt = build_prompt(all_scenarios()[0])
    contract = prompt.split("<OUTPUT_CONTRACT>\n", 1)[1].split("\n</OUTPUT_CONTRACT>", 1)[0]
    for operation in ("keep_focus", "shift_focus", "generalize", "archive", "reactivate", "stop_unresolved"):
        assert f'"operation": "{operation}"' in contract
    for field in ("assessment", "reason", "destination_node_id", "target_node_id", "important_unresolved_ids", "reopening_conditions"):
        assert f'"{field}"' in contract
    assert '"decision" instead of "operation"' in contract
    assert '"rationale" instead of "assessment" or "reason"' in contract
    assert '"operation"' in contract
    assert "Do not ... markdown/code fences" not in contract
    assert "markdown/code fences" in contract
    assert "commentary outside the JSON" in contract
    assert "Every identifier shown in the case context is already the exact stable identifier" in contract
    assert "Copy it exactly into identifier-valued output fields" in contract
    assert "<OUTPUT_EXAMPLE>" not in prompt
    for identifier in ("P1", "H1", "E1", "U1", "PERSON1", "R1.1"):
        assert f"{{{{{identifier}}}}}" not in prompt


def test_expanded_calibration_set_is_unique_and_covers_all_operations() -> None:
    scenarios = expanded_scenarios()
    assert len(scenarios) == 24
    assert len({scenario.scenario_id for scenario in scenarios}) == 24
    counts = {operation: sum(item.expected_operation == operation for item in scenarios) for operation in {item.expected_operation for item in scenarios}}
    assert counts == {"keep_focus": 4, "shift_focus": 6, "generalize": 4, "archive": 4, "reactivate": 4, "stop_unresolved": 2}


def test_luna_producer_input_does_not_include_expected_labels() -> None:
    import inspect
    from experiments.steward_screen.luna import produce
    assert "expected_operation" not in inspect.signature(produce).parameters
    prompt = build_prompt(all_scenarios()[0])
    assert "expected_operation" not in prompt and "expected_target_node_id" not in prompt


def test_steward_prompt_defines_each_operation_with_four_constraints() -> None:
    prompt = build_prompt(all_scenarios()[0])
    operations = prompt.split("<STEWARD_OPERATIONS>\n", 1)[1].split("\n</STEWARD_OPERATIONS>", 1)[0]
    for marker in ("PRECONDITION:", "ACTION:", "POSTCONDITION:", "VALIDATE:"):
        assert operations.count(marker) == 6
    assert "no other global graph-management operation is warranted" in operations
    assert "specific CHILD being generalized FROM" in operations
    assert "non-focus target is archived and focus is unchanged" in operations
    assert "target_node_id exists, is ARCHIVED" in operations
    assert "Trusted external review context permits stopping" in operations


def test_case_only_operation_error_is_diagnostic_only() -> None:
    scenario = next(item for item in all_scenarios() if item.scenario_id == "S1")
    payload = decision_payload(scenario)
    payload["operation"] = "SHIFT_FOCUS"
    result = evaluate_result("mock", "mock", scenario, 1, "prompt", fake_result(payload))
    assert not result.schema_valid
    assert result.schema_failure_code == "F2_INVALID_OPERATION_ENUM"
    assert result.schema_recoverable
    assert result.diagnostic_operation == "shift_focus"
    assert result.diagnostic_destination_node_id == "P1"
    assert result.diagnostic_decision_correct
    assert not result.coordinator_accepted and not result.post_state_correct


def test_diagnostics_do_not_correct_semantic_identifiers_or_operations() -> None:
    generalize = next(item for item in all_scenarios() if item.scenario_id == "G1")
    wrong_target = decision_payload(generalize)
    wrong_target["target_node_id"] = "H1"
    result = evaluate_result("mock", "mock", generalize, 1, "prompt", fake_result(wrong_target))
    assert result.schema_valid and result.operation_correct
    assert not result.identifier_correct
    assert result.diagnostic_target_node_id == "H1"
    assert not result.diagnostic_decision_correct

    reactivate = next(item for item in all_scenarios() if item.scenario_id == "R1")
    wrong_operation = decision_payload(reactivate)
    wrong_operation["operation"] = "shift_focus"
    wrong_operation.pop("target_node_id")
    wrong_operation["destination_node_id"] = "H2"
    result = evaluate_result("mock", "mock", reactivate, 1, "prompt", fake_result(wrong_operation))
    assert result.schema_valid and not result.operation_correct
    assert not result.coordinator_accepted
    assert result.diagnostic_operation == "shift_focus"

    archive = next(item for item in all_scenarios() if item.scenario_id == "A1")
    keep = decision_payload(archive)
    keep["operation"] = "keep_focus"
    keep.pop("target_node_id", None)
    result = evaluate_result("mock", "mock", archive, 1, "prompt", fake_result(keep))
    assert result.schema_valid and not result.operation_correct
    assert result.diagnostic_operation == "keep_focus"
