from copy import deepcopy

from investigator.cycle import InvestigatorCycleCoordinator, InvestigatorTurnResponse, CycleError, RequestEnquiry
from investigator.graph import GraphNodeType
from investigator.graph import EdgeRelation

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
    for code, terms in _hard_failure_terms(fixture):
        if any(term in _text_of_updates(response) for term in terms):
            result.failure_categories.append(code)
            result.diagnostics.append(f"unsupported semantic inference detected: {code}")
    for requirement in fixture.required:
        if not _satisfies(requirement, response, coordinator, fixture):
            result.diagnostics.append(f"required semantic condition not met: {requirement.kind}")
            code = _required_failure_code(requirement.kind, fixture)
            if code not in result.failure_categories:
                result.failure_categories.append(code)
    for forbidden in fixture.forbidden:
        if _satisfies(forbidden, response, coordinator, fixture):
            result.diagnostics.append(f"forbidden semantic condition met: {forbidden.kind}")
            code = "UNNECESSARY_CLARIFICATION" if forbidden.kind == "enquiry" else "SEMANTIC_FAILURE"
            if code not in result.failure_categories:
                result.failure_categories.append(code)
    if not _next_step_acceptable(result.next_step_type, fixture, response, coordinator):
        result.diagnostics.append(f"next step {result.next_step_type!r} is not acceptable")
    result.manual_review_flags.extend(_manual_review_flags(fixture, response))
    result.semantic_pass = not result.diagnostics
    if not result.semantic_pass:
        result.failure_categories.append("SEMANTIC_FAILURE")
    return result


def _next_step_acceptable(next_step: str, fixture: InvestigatorFixture, response, coordinator) -> bool:
    if next_step in fixture.acceptable_next_steps:
        return True
    # A one-turn screen cannot declare a real tenure exhausted, but it can
    # accept the handoff when the public observation exposes no local action.
    return next_step == "local_exhausted" and not fixture.observation.available_enquiries and not any(
        not _satisfies(requirement, response, coordinator, fixture) for requirement in fixture.required
    )


def _relation_path_exists(coordinator, relation: EdgeRelation, source: str, target: str) -> bool:
    edges = coordinator.graph.edges.values()
    if any(edge.source_id == source and edge.target_id == target and edge.relation is relation for edge in edges):
        return True
    for bridge in coordinator.graph.nodes.values():
        if bridge.node_type is not GraphNodeType.PROPOSITION or bridge.id == target:
            continue
        derived = any(edge.source_id == bridge.id and edge.target_id == source and edge.relation is EdgeRelation.DERIVED_FROM for edge in coordinator.graph.edges.values())
        related = any(edge.source_id == bridge.id and edge.target_id == target and edge.relation is relation for edge in coordinator.graph.edges.values())
        if derived and related:
            return True
    return False


def _text_of_updates(response) -> str:
    return " ".join(str(value) for update in response.graph_updates for value in update.model_dump(mode="json").values()).lower()


def _manual_review_flags(fixture: InvestigatorFixture, response) -> list[str]:
    flags: list[str] = []
    updates = response.graph_updates
    new_hypotheses = sum(item.operation == "add_hypothesis" for item in updates)
    new_uncertainties = sum(item.operation == "add_uncertainty" for item in updates)
    if len(updates) >= 4 and new_hypotheses + new_uncertainties >= 2:
        flags.append("POSSIBLE_OVER_EXPANSION")
    if fixture.fixture_id == "INV3" and any(term in _text_of_updates(response) for term in ("timestamp manipulation", "system-clock tampering", "copied metadata")):
        flags.append("POSSIBLE_UNSUPPORTED_ABDUCTION")
    return flags


def _satisfies(requirement, response, coordinator, fixture) -> bool:
    kind, values = requirement.kind, requirement.values
    if kind == "node_created":
        expected = GraphNodeType(values[0])
        return any(node.id not in fixture.observation.local_graph.nodes and node.node_type is expected for node in coordinator.graph.nodes.values())
    if kind == "uncertainty":
        return any(item.operation == "add_uncertainty" and item.target_node_id == values[0] for item in response.graph_updates)
    if kind == "edge_created":
        relation, source, target = values
        return _relation_path_exists(coordinator, EdgeRelation(relation), source, target)
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
        text = _text_of_updates(response)
        unsupported = ("direct help", "unauthorized website", "unauthorized book", "ai tool", "chatgpt", "named actor", "tutor")
        return not any(item.operation == "add_specialization" for item in response.graph_updates) and not any(term in text for term in unsupported)
    if kind == "grounded_update":
        if not any(item.operation in {"add_proposition", "add_uncertainty", "add_hypothesis"} for item in response.graph_updates):
            return False
        if len(values) > 1 and values[1] == "add_uncertainty":
            return any(item.operation == "add_uncertainty" and item.target_node_id in {"H1", "H2", "P1"} for item in response.graph_updates)
        return any("E1" in getattr(item, "derived_from_node_ids", []) for item in response.graph_updates) or any(
            edge.source_id == "E1" or edge.target_id == "E1" for edge in coordinator.graph.edges.values()
        )
    return False


def _hard_failure_terms(fixture: InvestigatorFixture) -> list[tuple[str, tuple[str, ...]]]:
    if fixture.fixture_id == "INV4":
        return [("CONTRADICTION_TO_DECEPTION", ("deception", "deceptive", "intentional lie", "deliberately lied", "dishonest"))]
    if fixture.fixture_id == "INV9":
        return [("CREDENTIAL_TO_PERSON", ("person1 performed", "person1 was the actor", "person1 used", "person1 accessed"))]
    return []


def _required_failure_code(kind: str, fixture: InvestigatorFixture) -> str:
    if kind == "edge_created":
        return "NO_REQUIRED_RELATION"
    if kind == "no_unsupported_specificity":
        return "UNSUPPORTED_SPECIFICITY"
    if kind == "enquiry":
        return "WRONG_ENQUIRY"
    if kind == "uncertainty":
        return "NO_REQUIRED_RELATION"
    return "SEMANTIC_FAILURE"
