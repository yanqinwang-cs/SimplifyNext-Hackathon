import json

from investigator.policy import render_policy_profile
from investigator.steward.features import region_health, tunnel_vision_indicators

from experiments.steward_screen.models import StewardScenario


def _ref(identifier: str) -> str:
    return "{{" + identifier + "}}"


def build_prompt(scenario: StewardScenario) -> str:
    graph = {
        "nodes": [{**node.model_dump(mode="json"), "id": _ref(node.id)} for node in sorted(scenario.graph.nodes.values(), key=lambda item: item.id)],
        "edges": [{**edge.model_dump(mode="json"), "source_id": _ref(edge.source_id), "target_id": _ref(edge.target_id)} for edge in sorted(scenario.graph.edges.values(), key=lambda item: item.id)],
    }
    features = {"region_health": region_health(scenario.graph, scenario.focus).model_dump(mode="json"), "tunnel_vision": tunnel_vision_indicators(scenario.graph, scenario.focus).model_dump(mode="json")}
    participant_text = "\n".join(f"{_ref(item.id)} roles={', '.join(item.contextual_roles)} label={item.display_label}" for item in scenario.participants)
    focus_history = " -> ".join(_ref(item) for item in scenario.focus.recent_node_ids) or "(none yet)"
    sections = [
        "<ROLE>\nYou are the Case Steward. Manage global graph focus and relevance. Do not investigate, choose an exact enquiry or tool, create nodes, or decide institutional guilt. Return exactly one legal StewardDecision JSON object.\n</ROLE>",
        "<INVESTIGATIVE_PURPOSE>\nDetermine what can responsibly be inferred from available evidence and what consequential uncertainty remains. Do not search for incriminating material or prefer an explanation because it is more incriminating. Final institutional judgement remains human.\n</INVESTIGATIVE_PURPOSE>",
        "<OBJECT_LEGEND>\nA persistent CaseGraph object is an ID plus natural-language content plus structured status/relations. {{E... | EVIDENCE}} is obtained material, not automatically true. {{P... | PROPOSITION}} is a factual claim. {{H... | HYPOTHESIS}} is a possible explanation. {{U... | UNCERTAINTY}} is a consequential unresolved question. Object operations act on stable IDs; focus/status changes do not change statement truth. Participants such as {{PERSON1}} are contextual entities, not CaseGraph node types.\n</OBJECT_LEGEND>",
        "<RELATION_LEGEND>\nSUPPORTS: A provides some support for B, not establishment. CONFLICTS: A creates tension with B, not automatic falsity. SPECIALIZES: child -> parent; child failure does not defeat parent. DEPENDS_ON: A materially depends on B. TARGETS: U concerns P or H. DERIVED_FROM: A was interpreted or derived from B. Edges do not encode chronology; focus/history does.\n</RELATION_LEGEND>",
        "<POLICY_CONTEXT>\n" + render_policy_profile() + "\n</POLICY_CONTEXT>",
        "<POLICY_DISCIPLINE>\nReason evidence -> factual proposition or uncertainty -> applicable policy rule. Cite exact existing rule IDs such as {{R1.1}} only when participant role, conduct/resource, temporal, location, and context scope match. Policy relevance does not establish factual truth. Preserve possession versus use, installed local LLM versus use during an examination, and association versus collaboration.\n</POLICY_DISCIPLINE>",
        "<STEWARD_OPERATIONS>\nKEEP_FOCUS keeps a materially useful current region. SHIFT_FOCUS(destination) selects another existing ACTIVE object; graph otherwise unchanged. GENERALIZE(target) moves to the immediate active SPECIALIZES parent and does not archive/reject the child. ARCHIVE(target) preserves history but removes active participation; it does not mean false, and archiving current focus requires an explicit different ACTIVE destination. REACTIVATE(target) makes relevant archived material active. STOP_UNRESOLVED is valid only when trusted frontier context establishes no materially useful frontier remains; it is not a guilt, innocence, truth, or falsity conclusion.\n</STEWARD_OPERATIONS>",
        "<AUTHORITY_BOUNDARY>\nThe Steward does not create evidence, propositions, hypotheses, uncertainties, tools, or environment actions. The Investigator works locally; the Steward manages global graph focus/status.\n</AUTHORITY_BOUNDARY>",
        "<CASE_PARTICIPANTS>\n" + participant_text + "\n</CASE_PARTICIPANTS>",
        "<CASEGRAPH>\n" + json.dumps(graph, sort_keys=True) + "\n</CASEGRAPH>",
        "<CURRENT_FOCUS>\n" + _ref(scenario.focus.node_id) + "\n</CURRENT_FOCUS>",
        "<FOCUS_HISTORY>\n" + focus_history + "\n</FOCUS_HISTORY>",
        "<DETERMINISTIC_FEATURES>\n" + json.dumps(features, sort_keys=True) + "\n</DETERMINISTIC_FEATURES>",
    ]
    if scenario.review_context is not None:
        sections.append("<TRUSTED_FRONTIER>\n" + json.dumps(scenario.review_context.model_dump(mode="json"), sort_keys=True) + "\nThis is trusted external structural input. Do not author or modify it.\n</TRUSTED_FRONTIER>")
    sections.extend(["<DYNAMIC_SCENARIO>\n" + scenario.description + "\n</DYNAMIC_SCENARIO>", "<OUTPUT_CONTRACT>\nReturn one JSON object using only KEEP_FOCUS, SHIFT_FOCUS, GENERALIZE, ARCHIVE, REACTIVATE, or STOP_UNRESOLVED and the fields allowed by that branch. Do not return alternatives or chain-of-thought.\n</OUTPUT_CONTRACT>"])
    return "\n\n".join(sections)
