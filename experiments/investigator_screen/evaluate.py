from copy import deepcopy

from investigator.cycle import InvestigatorCycleCoordinator, InvestigatorTurnResponse, CycleError, RequestEnquiry
from investigator.graph import GraphNodeType

from experiments.investigator_screen.models import InvestigatorFixture, InvestigatorScreenResult


def evaluate_payload(fixture: InvestigatorFixture, payload: dict) -> InvestigatorScreenResult:
    result = InvestigatorScreenResult(fixture.fixture_id, raw_output=payload)
    coordinator = InvestigatorCycleCoordinator(deepcopy(fixture.observation.local_graph), fixture.observation.current_focus.model_copy(deep=True), available_enquiries=fixture.observation.available_enquiries, participants=fixture.observation.participants, max_turns_per_tenure=fixture.observation.max_turns_per_tenure)
    try:
        response = InvestigatorTurnResponse.model_validate(payload)
        result.schema_valid = True
        result.next_step_type = response.next_step.type
        result.update_operations = [item.operation for item in response.graph_updates]
    except Exception as exc:
        result.failure_categories.append("SCHEMA_FAILURE")
        result.diagnostics.append(str(exc))
        return result
    try:
        coordinator.apply_turn(response)
        result.production_applied = True
    except CycleError as exc:
        result.failure_categories.append(exc.code.value)
        result.diagnostics.append(str(exc))
        return result
    except Exception as exc:
        result.failure_categories.append("GRAPH_UPDATE_FAILURE")
        result.diagnostics.append(str(exc))
        return result
    for requirement in fixture.required:
        if not _satisfies(requirement, response, coordinator, fixture):
            result.diagnostics.append(f"required semantic condition not met: {requirement.kind}")
    for forbidden in fixture.forbidden:
        if _satisfies(forbidden, response, coordinator, fixture):
            result.diagnostics.append(f"forbidden semantic condition met: {forbidden.kind}")
    if result.next_step_type not in fixture.acceptable_next_steps:
        result.diagnostics.append(f"next step {result.next_step_type!r} is not acceptable")
    result.semantic_pass = not result.diagnostics
    if not result.semantic_pass:
        result.failure_categories.append("SEMANTIC_FAILURE")
    return result


def _satisfies(requirement, response, coordinator, fixture) -> bool:
    kind, values = requirement.kind, requirement.values
    if kind == "node_created":
        expected = GraphNodeType(values[0])
        return any(node.id not in fixture.observation.local_graph.nodes and node.node_type is expected for node in coordinator.graph.nodes.values())
    if kind == "uncertainty":
        return any(item.operation == "add_uncertainty" and item.target_node_id == values[0] for item in response.graph_updates)
    if kind == "edge_created":
        relation, source, target = values
        return any(edge.relation.value == relation and edge.source_id == source and edge.target_id == target for edge in coordinator.graph.edges.values())
    if kind == "derived_from":
        proposition_ids = {item.node_id for item in response.graph_updates if item.operation == "add_proposition"}
        return any(item.operation == "add_proposition" and item.node_id in proposition_ids and set(values) <= set(item.derived_from_node_ids) for item in response.graph_updates)
    if kind == "enquiry":
        step = response.next_step
        return isinstance(step, RequestEnquiry) and step.action_id == values[0] and step.target_uncertainty_id == values[1]
    if kind == "has_update":
        return bool(response.graph_updates)
    if kind == "next_step":
        return response.next_step.type == values[0]
    if kind == "no_unsupported_specificity":
        return not any(item.operation == "add_specialization" for item in response.graph_updates)
    return False
