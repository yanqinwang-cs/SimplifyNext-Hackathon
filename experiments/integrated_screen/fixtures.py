from __future__ import annotations

from investigator.cycle import AvailableEnquiry, EnquiryKind
from investigator.graph import CaseGraph, EdgeRelation, GraphEdge, GraphNode, GraphNodeType, GraphStatus
from investigator.roles import InvestigationFocus

from .environment import EvidenceRelease
from .evaluate import TrajectoryRequirements


class Stage1Fixture:
    def __init__(self, fixture_id, description, graph, focus, available_enquiries, releases, requirements, materially_usable_action_ids=None):
        self.fixture_id, self.description, self.graph, self.focus = fixture_id, description, graph, focus
        self.available_enquiries, self.releases, self.requirements = available_enquiries, releases, TrajectoryRequirements.model_validate(requirements)
        self.materially_usable_action_ids = list(materially_usable_action_ids if materially_usable_action_ids is not None else [a.action_id for a in available_enquiries])


def _graph(identifier, nodes, edges=()):
    values = {node.id: node for node in nodes}
    edge_values = {f"{s}_{r.value.upper()}_{t}": GraphEdge(id=f"{s}_{r.value.upper()}_{t}", source_id=s, relation=r, target_id=t) for s, r, t in edges}
    return CaseGraph(case_id=identifier, nodes=values, edges=edge_values)


def _node(identifier, kind, statement, status=GraphStatus.ACTIVE):
    return GraphNode(id=identifier, node_type=kind, statement=statement, status=status)


def all_fixtures() -> list[Stage1Fixture]:
    n, e = GraphNodeType, EdgeRelation
    a1 = AvailableEnquiry(action_id="A1", kind=EnquiryKind.VERIFY, description="Review the released source record.", addressable_uncertainty_ids=["U1"])
    a2 = AvailableEnquiry(action_id="A2", kind=EnquiryKind.CLARIFY, description="Clarify the remaining temporal question.", addressable_uncertainty_ids=["U1"])
    return [
        Stage1Fixture("C1", "Two submissions share an unusual intermediate result; a common preparation source and an independent explanation remain possible.", _graph("C1", [_node("E1", n.EVIDENCE, "Submission A contains the same unusual intermediate result."), _node("P1", n.PROPOSITION, "The submissions share that intermediate result."), _node("H1", n.HYPOTHESIS, "A common preparation source may explain the similarity."), _node("H2", n.HYPOTHESIS, "Independent work or a legitimate common source may explain the similarity."), _node("U1", n.UNCERTAINTY, "How unusual or source-specific is the shared result?")], [("E1", e.SUPPORTS, "P1"), ("P1", e.SUPPORTS, "H1"), ("U1", e.TARGETS, "H1")]), InvestigationFocus(node_id="H1"), [a1], {"A1": EvidenceRelease(evidence=[_node("E2", n.EVIDENCE, "The same intermediate result appears in a commonly distributed practice solution.")])}, {"required_release_ids": ["E2"], "required_action_ids": ["A1"], "required_visible_evidence_ids": ["E2"], "require_material_graph_change_after_release": True, "qualitative_checks": ["Assess whether the evidence was integrated sufficiently."]}, ["A1"]),
        Stage1Fixture("C2", "A specific assistance mechanism specializes a broader external-assistance explanation, alongside an independent-preparation alternative.", _graph("C2", [_node("E1", n.EVIDENCE, "A released record describes the observed work."), _node("H1", n.HYPOTHESIS, "External assistance may explain the response."), _node("H1.1", n.HYPOTHESIS, "A particular assistance mechanism explains the response."), _node("H2", n.HYPOTHESIS, "Independent preparation explains the response."), _node("U1", n.UNCERTAINTY, "Whether the particular mechanism fits the record.")], [("H1.1", e.SPECIALIZES, "H1"), ("U1", e.TARGETS, "H1.1")]), InvestigationFocus(node_id="H1.1"), [a1], {"A1": EvidenceRelease(evidence=[_node("E2", n.EVIDENCE, "The released record is inconsistent with that particular mechanism.")])}, {"required_release_ids": ["E2"], "required_action_ids": ["A1"]}, ["A1"]),
        Stage1Fixture("C3", "A source says discussion occurred around the exam, while the consequential timing question remains open.", _graph("C3", [_node("E1", n.EVIDENCE, "The source said: We discussed it around the exam."), _node("P1", n.PROPOSITION, "The discussion timing may affect the relevant assessment window."), _node("U1", n.UNCERTAINTY, "Whether discussion occurred during the consequential period.")], [("E1", e.SUPPORTS, "P1"), ("U1", e.TARGETS, "P1")]), InvestigationFocus(node_id="P1"), [a1], {"A1": EvidenceRelease(evidence=[_node("E2", n.EVIDENCE, "An independent timestamp places the discussion two days before the exam.")], no_longer_materially_usable_action_ids=["A1"])}, {"required_release_ids": ["E2"], "required_action_ids": ["A1"]}, ["A1"]),
        Stage1Fixture("C4", "Several released records leave a consequential uncertainty unresolved and exhaust the recoverable frontier.", _graph("C4", [_node("E1", n.EVIDENCE, "No recoverable message history is available."), _node("H1", n.HYPOTHESIS, "The available record may support more than one explanation."), _node("U1", n.UNCERTAINTY, "Whether the available record is sufficient to resolve the explanation.")], [("E1", e.SUPPORTS, "H1"), ("U1", e.TARGETS, "H1")]), InvestigationFocus(node_id="H1"), [a1, a2], {"A1": EvidenceRelease(evidence=[_node("E2", n.EVIDENCE, "The revision history is inconclusive.")]), "A2": EvidenceRelease(evidence=[_node("E3", n.EVIDENCE, "Independent verification is unavailable.")], global_frontier_assessed=True)}, {"required_action_ids": ["A1", "A2"], "require_stop_unresolved": True}, ["A1", "A2"]),
    ]


def fixture_map() -> dict[str, Stage1Fixture]:
    return {fixture.fixture_id: fixture for fixture in all_fixtures()}
