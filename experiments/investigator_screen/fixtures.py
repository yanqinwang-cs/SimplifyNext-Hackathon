from investigator.cycle import AvailableEnquiry, EnquiryKind, InvestigatorCycleCoordinator
from investigator.graph import CaseGraph, EdgeRelation, GraphEdge, GraphNode, GraphNodeType
from investigator.roles import InvestigationFocus

from experiments.investigator_screen.models import InvestigatorFixture, SemanticRequirement


def _graph(*nodes: tuple[str, GraphNodeType], edges: tuple[str, str, EdgeRelation] = ()) -> CaseGraph:
    values = {identifier: GraphNode(id=identifier, node_type=kind, statement=identifier) for identifier, kind in nodes}
    graph_edges = {f"{source}_{relation.value.upper()}_{target}": GraphEdge(id=f"{source}_{relation.value.upper()}_{target}", source_id=source, target_id=target, relation=relation) for source, target, relation in edges}
    return CaseGraph(case_id="investigator-screen", nodes=values, edges=graph_edges)


def _fixture(identifier: str, description: str, graph: CaseGraph, focus: str, required=(), forbidden=(), steps=("continue_local",), enquiries=()) -> InvestigatorFixture:
    graph.case_id = identifier
    coordinator = InvestigatorCycleCoordinator(graph, InvestigationFocus(node_id=focus), available_enquiries=list(enquiries))
    basis = {f"{item.kind}:{index}": item.basis for index, item in enumerate(required)}
    return InvestigatorFixture(identifier, description, coordinator.observation(), list(required), list(forbidden), frozenset(steps), basis)


def _req(kind: str, *values: str, basis: tuple[str, ...]) -> SemanticRequirement:
    return SemanticRequirement(kind, basis, values)


def all_fixtures() -> list[InvestigatorFixture]:
    n = GraphNodeType
    clarify = AvailableEnquiry(action_id="A1", kind=EnquiryKind.CLARIFY, description="Clarify the timing statement.", addressable_uncertainty_ids=["U1"])
    compute = AvailableEnquiry(action_id="A1", kind=EnquiryKind.COMPUTE, description="Compute interval overlap from visible timestamps.", addressable_uncertainty_ids=["U1"])
    verify = AvailableEnquiry(action_id="A1", kind=EnquiryKind.VERIFY, description="Verify attribution of the account activity.", addressable_uncertainty_ids=["U1"])
    return [
        _fixture("INV1", "Two released submissions contain the same unusual intermediate error.", _graph(("E1", n.EVIDENCE), ("E2", n.EVIDENCE), ("H1", n.HYPOTHESIS), edges=(("E1", "H1", EdgeRelation.CONFLICTS), ("E2", "H1", EdgeRelation.CONFLICTS))), "H1", [_req("node_created", "proposition", basis=("E1 and E2 visibly contain the shared observation.",)), _req("derived_from", "E1", "E2", basis=("The required observation is relational and both evidence IDs are visible."))], steps=("continue_local", "request_steward_review")),
        _fixture("INV2", "P1 is active and its timing attribution remains consequentially unresolved.", _graph(("P1", n.PROPOSITION), ("H1", n.HYPOTHESIS)), "P1", [_req("node_created", "uncertainty", "P1", basis=("P1 is visible and the unresolved dimension concerns that claim.",))]),
        _fixture("INV3", "E1 is a directly relevant released record for P1.", _graph(("E1", n.EVIDENCE), ("P1", n.PROPOSITION), edges=(("E1", "P1", EdgeRelation.CONFLICTS),)), "P1", [_req("edge_created", "supports", "E1", "P1", basis=("E1 and P1 are visible and the record bears directly on P1.",))]),
        _fixture("INV4", "E1 reports a fact inconsistent with the active P1.", _graph(("E1", n.EVIDENCE), ("P1", n.PROPOSITION), edges=(("E1", "P1", EdgeRelation.SUPPORTS),)), "P1", [_req("edge_created", "conflicts", "E1", "P1", basis=("The visible record is inconsistent with P1.",))]),
        _fixture("INV5", "E1 supports considering a broad assistance explanation but gives no actor, device, or mechanism.", _graph(("E1", n.EVIDENCE), ("H1", n.HYPOTHESIS)), "H1", [_req("no_unsupported_specificity", basis=("Only a broad possibility is supported; no mechanism or actor is visible.",))]),
        _fixture("INV6", "A visible source says the event happened around the exam and U1 asks when it occurred.", _graph(("P1", n.PROPOSITION), ("U1", n.UNCERTAINTY), edges=(("U1", "P1", EdgeRelation.TARGETS),)), "P1", [_req("enquiry", "A1", "U1", basis=("The vague timing statement, U1, and CLARIFY A1 are all visible.",))], steps=("request_enquiry",), enquiries=(clarify,)),
        _fixture("INV7", "A vague timing phrase is visible, but independent material already settles the only consequential boundary.", _graph(("P1", n.PROPOSITION), ("U1", n.UNCERTAINTY), edges=(("U1", "P1", EdgeRelation.TARGETS),)), "P1", steps=("continue_local", "local_exhausted"), enquiries=(clarify,)),
        _fixture("INV8", "Visible released timestamps make the interval-overlap question answerable by computation.", _graph(("P1", n.PROPOSITION), ("U1", n.UNCERTAINTY), edges=(("U1", "P1", EdgeRelation.TARGETS),)), "P1", [_req("enquiry", "A1", "U1", basis=("Timestamps, U1, and COMPUTE A1 are visible.",))], steps=("request_enquiry",), enquiries=(compute,)),
        _fixture("INV9", "P1 depends on whether account activity can be attributed to PERSON1; the visible credential record does not settle identity.", _graph(("P1", n.PROPOSITION), ("U1", n.UNCERTAINTY), edges=(("U1", "P1", EdgeRelation.TARGETS),)), "P1", [_req("enquiry", "A1", "U1", basis=("The attribution uncertainty and VERIFY A1 are visible.",))], steps=("request_enquiry",), enquiries=(verify,)),
        _fixture("INV10", "E1 supports a new local observation while another visible local question remains.", _graph(("E1", n.EVIDENCE), ("H1", n.HYPOTHESIS)), "H1", [_req("has_update", basis=("E1 is visible and local graph work remains." ,))], steps=("continue_local",)),
        _fixture("INV11", "A new local interpretation would affect a separate global explanation visible through the current context.", _graph(("E1", n.EVIDENCE), ("H1", n.HYPOTHESIS), ("H2", n.HYPOTHESIS)), "H1", [_req("has_update", basis=("The local evidence and competing visible explanations support a local update.",))], steps=("request_steward_review",)),
        _fixture("INV12", "The visible local region has no useful graph expansion or listed enquiry remaining.", _graph(("H1", n.HYPOTHESIS)), "H1", [_req("next_step", "local_exhausted", basis=("No local update or available enquiry is visible.",))], steps=("local_exhausted",)),
    ]
