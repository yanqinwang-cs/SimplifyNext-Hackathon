from __future__ import annotations

from investigator.cycle import AvailableEnquiry, EnquiryKind
from investigator.graph import CaseGraph, EdgeRelation, GraphEdge, GraphNode, GraphNodeType, GraphStatus
from investigator.roles import InvestigationFocus

from .environment import EvidenceRelease
from .evaluate import TrajectoryRequirements


class Stage1Fixture:
    def __init__(self, fixture_id, description, graph, focus, available_enquiries, releases, requirements, materially_usable_action_ids=None, hidden_audit_truth="", assess_global_frontier_when_materially_usable_empty=False):
        self.fixture_id, self.description, self.graph, self.focus = fixture_id, description, graph, focus
        self.available_enquiries, self.releases = available_enquiries, releases
        self.requirements = TrajectoryRequirements.model_validate(requirements)
        self.materially_usable_action_ids = list(materially_usable_action_ids if materially_usable_action_ids is not None else [a.action_id for a in available_enquiries])
        self.hidden_audit_truth = hidden_audit_truth
        self.assess_global_frontier_when_materially_usable_empty = assess_global_frontier_when_materially_usable_empty


def _graph(identifier, nodes, edges=()):
    values = {node.id: node for node in nodes}
    edge_values = {f"{s}_{r.value.upper()}_{t}": GraphEdge(id=f"{s}_{r.value.upper()}_{t}", source_id=s, relation=r, target_id=t) for s, r, t in edges}
    return CaseGraph(case_id=identifier, nodes=values, edges=edge_values)


def _node(identifier, kind, statement, status=GraphStatus.ACTIVE):
    return GraphNode(id=identifier, node_type=kind, statement=statement, status=status)


def _action(identifier, kind, description, targets):
    return AvailableEnquiry(action_id=identifier, kind=kind, description=description, addressable_uncertainty_ids=targets)


def all_fixtures() -> list[Stage1Fixture]:
    n, e = GraphNodeType, EdgeRelation
    c1_a1 = _action("A1", EnquiryKind.REVIEW, "Review the course and distributed practice materials for the same intermediate result.", ["U1"])
    c2_a1 = _action("A1", EnquiryKind.VERIFY, "Review the complete record of the named tutor's preparation session for the terminology or an equivalent explanation.", ["U1"])
    c3_a1 = _action("A1", EnquiryKind.CLARIFY, "Ask the interviewee what 'around the exam' means.", ["U1"])
    c3_a2 = _action("A2", EnquiryKind.REVIEW, "Review available timestamped communication records for the discussion.", ["U1"])
    c4_a1 = _action("A1", EnquiryKind.REVIEW, "Review the available message archive for communication relevant to the assessment.", ["U1"])
    c4_a2 = _action("A2", EnquiryKind.VERIFY, "Review document revision metadata for provenance or coordination indicators.", ["U1"])
    c4_a3 = _action("A3", EnquiryKind.OBTAIN, "Obtain any reliable comparison source capable of estimating how common the observed revision sequence is.", ["U2"])
    return [
        Stage1Fixture("C1", "A shared intermediate result initially appears source-specific, but a legitimate common source may explain it.", _graph("C1", [_node("E1", n.EVIDENCE, "Submission A and Submission B both contain the intermediate result '7x = 21'."), _node("P1", n.PROPOSITION, "Both submissions contain the same intermediate result '7x = 21'."), _node("H1", n.HYPOTHESIS, "A common preparation source may explain the shared intermediate result."), _node("H2", n.HYPOTHESIS, "The shared intermediate result may have arisen independently or from a legitimate common source."), _node("U1", n.UNCERTAINTY, "How source-specific is the shared intermediate result?")], [("E1", e.SUPPORTS, "P1"), ("P1", e.SUPPORTS, "H1"), ("U1", e.TARGETS, "P1")]), InvestigationFocus(node_id="U1"), [c1_a1], {"A1": EvidenceRelease(evidence=[_node("E2", n.EVIDENCE, "An official practice solution distributed to the whole class before the assessment contains the same intermediate result '7x = 21' in the corresponding solution step.")], global_frontier_assessed=True)}, {"required_action_ids": ["A1"], "required_release_ids": ["E2"], "required_visible_evidence_ids": ["E2"], "require_material_graph_change_after_release": True, "qualitative_checks": ["Assess whether the shared result became less discriminating between the competing explanations."]}, hidden_audit_truth="Both submissions independently drew on the distributed practice solution."),
        Stage1Fixture("C2", "Unfamiliar terminology may have come from an external source; a named tutor's recorded session is one specific explanation.", _graph("C2", [_node("E1", n.EVIDENCE, "The response uses the phrase 'constraint propagation frontier'. The phrase does not appear in the supplied course materials."), _node("P1", n.PROPOSITION, "The response uses terminology not present in the supplied course materials."), _node("H1", n.HYPOTHESIS, "An external source may explain the unfamiliar terminology."), _node("H1.1", n.HYPOTHESIS, "The named tutor supplied the unfamiliar terminology during the recorded preparation session."), _node("H2", n.HYPOTHESIS, "The student independently knew or learned the unfamiliar terminology."), _node("U1", n.UNCERTAINTY, "Did the named tutor use or supply the unfamiliar terminology during the recorded preparation session?")], [("E1", e.SUPPORTS, "P1"), ("P1", e.SUPPORTS, "H1"), ("H1.1", e.SPECIALIZES, "H1"), ("U1", e.TARGETS, "H1.1")]), InvestigationFocus(node_id="H1.1"), [c2_a1], {"A1": EvidenceRelease(evidence=[_node("E2", n.EVIDENCE, "The complete record of the named tutor's preparation session contains no use of 'constraint propagation frontier' or an equivalent term, and the recorded session ended before the relevant topic appeared in the assessment materials.")], global_frontier_assessed=True)}, {"required_action_ids": ["A1"], "required_release_ids": ["E2"], "required_visible_evidence_ids": ["E2"], "require_material_graph_change_after_release": True, "qualitative_checks": ["Assess whether H1.1 ceased to drive the investigation without automatically rejecting H1."]}, hidden_audit_truth="The terminology came from the student's prior independent learning; the named tutor did not supply it."),
        Stage1Fixture("C3", "A vague statement about discussion timing can be clarified or checked against an independent timestamp.", _graph("C3", [_node("E1", n.EVIDENCE, "The interviewee said: 'We discussed the question around the exam.'"), _node("P1", n.PROPOSITION, "A discussion about the assessment occurred near the assessment period."), _node("H1", n.HYPOTHESIS, "The discussion may have occurred during the prohibited assessment period."), _node("U1", n.UNCERTAINTY, "When did the discussion occur relative to the prohibited period?")], [("E1", e.SUPPORTS, "P1"), ("P1", e.SUPPORTS, "H1"), ("U1", e.TARGETS, "H1")]), InvestigationFocus(node_id="U1"), [c3_a1, c3_a2], {"A1": EvidenceRelease(evidence=[_node("E2", n.EVIDENCE, "The interviewee clarified: 'I think it was sometime that week, but I do not remember the exact day.'")]), "A2": EvidenceRelease(evidence=[_node("E3", n.EVIDENCE, "A timestamped communication record places the discussion two days before the assessment and before the prohibited assessment period began.")], no_longer_materially_usable_action_ids=["A1"], global_frontier_assessed=True)}, {"required_action_ids": ["A2"], "required_release_ids": ["E3"], "required_visible_evidence_ids": ["E3"], "forbidden_actions_after_release": {"E3": ["A1"]}, "require_material_graph_change_after_release": True, "qualitative_checks": ["Assess whether clarification ceased to be materially useful after the consequential timing boundary was independently settled."]}, hidden_audit_truth="The discussion occurred at the time shown in E3, before the prohibited period."),
        Stage1Fixture("C4", "Two drafts share an unusual revision sequence, but the available records leave communication and comparative rarity unresolved.", _graph("C4", [_node("E1", n.EVIDENCE, "Two drafts contain the same unusual sequence of revisions."), _node("P1", n.PROPOSITION, "The two drafts share an unusual revision sequence."), _node("H1", n.HYPOTHESIS, "The drafts may have been prepared using a shared source or coordinated process."), _node("H2", n.HYPOTHESIS, "The drafts may have been produced independently."), _node("U1", n.UNCERTAINTY, "Did the authors communicate or share material during preparation?"), _node("U2", n.UNCERTAINTY, "Is the revision sequence sufficiently unusual to distinguish shared preparation from independent work?")], [("E1", e.SUPPORTS, "P1"), ("P1", e.SUPPORTS, "H1"), ("U1", e.TARGETS, "P1"), ("U2", e.TARGETS, "P1")]), InvestigationFocus(node_id="P1"), [c4_a1, c4_a2, c4_a3], {"A1": EvidenceRelease(evidence=[_node("E2", n.EVIDENCE, "The available message archive contains no communication about the assessment between the two authors, but the archive does not cover part of the relevant period because those records are unavailable.")]), "A2": EvidenceRelease(evidence=[_node("E3", n.EVIDENCE, "Revision metadata confirms that the two documents underwent a similar revision ordering, but the metadata does not establish whether either author saw or accessed the other author's draft.")]), "A3": EvidenceRelease(evidence=[_node("E4", n.EVIDENCE, "No reliable comparison dataset is available for sufficiently similar assignments to estimate how often this revision sequence occurs independently.")])}, {"required_action_ids": ["A1", "A2", "A3"], "required_release_ids": ["E2", "E3", "E4"], "required_visible_evidence_ids": ["E2", "E3", "E4"], "require_stop_unresolved": True, "require_trusted_exhaustion_for_stop": True}, hidden_audit_truth="Shared preparation actually occurred, but the released evidence cannot establish it.", assess_global_frontier_when_materially_usable_empty=True),
    ]


def fixture_map() -> dict[str, Stage1Fixture]:
    return {fixture.fixture_id: fixture for fixture in all_fixtures()}
