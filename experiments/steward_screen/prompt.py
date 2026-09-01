import json
import re

from investigator.policy import render_policy_profile
from investigator.steward.features import region_health, tunnel_vision_indicators

from experiments.steward_screen.models import StewardScenario


_OUTPUT_CONTRACT = """Return exactly one JSON object matching exactly one of these branch schemas:

KEEP_FOCUS
{
  "operation": "keep_focus",
  "assessment": "Brief case-specific assessment of the current focus.",
  "reason": "Brief case-specific reason to keep the current focus."
}

SHIFT_FOCUS
{
  "operation": "shift_focus",
  "assessment": "Brief case-specific assessment of the current focus.",
  "reason": "Brief case-specific reason for shifting focus.",
  "destination_node_id": "EXACT_EXISTING_NODE_ID"
}

GENERALIZE
{
  "operation": "generalize",
  "assessment": "Brief case-specific assessment of the current focus.",
  "reason": "Brief case-specific reason for generalizing.",
  "target_node_id": "EXACT_EXISTING_NODE_ID"
}

ARCHIVE
{
  "operation": "archive",
  "assessment": "Brief case-specific assessment of the target.",
  "reason": "Brief case-specific reason for archiving.",
  "target_node_id": "EXACT_EXISTING_NODE_ID",
  "destination_node_id": "EXACT_EXISTING_NODE_ID_OR_NULL"
}

REACTIVATE
{
  "operation": "reactivate",
  "assessment": "Brief case-specific assessment of the archived target.",
  "reason": "Brief case-specific reason for reactivating it.",
  "target_node_id": "EXACT_EXISTING_NODE_ID"
}

STOP_UNRESOLVED
{
  "operation": "stop_unresolved",
  "assessment": "Brief case-specific assessment of why no useful frontier remains.",
  "reason": "Brief case-specific reason for stopping unresolved.",
  "important_unresolved_ids": ["EXACT_EXISTING_UNCERTAINTY_ID"],
  "reopening_conditions": "Concrete condition for reopening the investigation."
}

Use exactly the field names shown and the exact lowercase operation values shown. Do not use "decision" instead of "operation" or "rationale" instead of "assessment" or "reason". Do not rename fields, add fields outside the selected branch schema, return alternatives, wrap the JSON in markdown/code fences, return commentary outside the JSON, or return chain-of-thought.

Every identifier shown in the case context is already the exact stable identifier expected by the production schema and coordinator. Copy it exactly into identifier-valued output fields; do not invent IDs. For ARCHIVE, destination_node_id may be null when allowed by the production schema, but archiving the current focus requires an explicit different ACTIVE destination. STOP_UNRESOLVED is valid only when trusted external frontier context supports it.

The assessment and reason must be grounded in the supplied case state and explain why the selected operation is appropriate. Brief case-specific justification is sufficient; do not provide long reasoning."""


def _raw_policy_context() -> str:
    rendered = render_policy_profile().replace("{{R...}}", "R...", 1)
    return re.sub(r"\{\{?(R\d+(?:\.\d+)?)\}\}?", r"\1", rendered)


def build_prompt(scenario: StewardScenario) -> str:
    graph = {
        "nodes": [node.model_dump(mode="json") for node in sorted(scenario.graph.nodes.values(), key=lambda item: item.id)],
        "edges": [edge.model_dump(mode="json") for edge in sorted(scenario.graph.edges.values(), key=lambda item: item.id)],
    }
    features = {"region_health": region_health(scenario.graph, scenario.focus).model_dump(mode="json"), "tunnel_vision": tunnel_vision_indicators(scenario.graph, scenario.focus).model_dump(mode="json")}
    participant_text = "\n".join(f"{item.id} roles={', '.join(item.contextual_roles)} label={item.display_label}" for item in scenario.participants)
    focus_history = " -> ".join(scenario.focus.recent_node_ids) or "(none yet)"
    sections = [
        "<ROLE>\nYou are the Case Steward. Manage global graph focus and relevance. Do not investigate, choose an exact enquiry or tool, create nodes, or decide institutional guilt. Return exactly one legal StewardDecision JSON object.\n</ROLE>",
        "<INVESTIGATIVE_PURPOSE>\nDetermine what can responsibly be inferred from available evidence and what consequential uncertainty remains. Do not search for incriminating material or prefer an explanation because it is more incriminating. Final institutional judgement remains human.\n</INVESTIGATIVE_PURPOSE>",
        "<OBJECT_LEGEND>\nA persistent CaseGraph object has a stable ID plus natural-language content and structured status/relations. E1 = stable EVIDENCE object ID; P1 = stable PROPOSITION object ID; H1 = stable HYPOTHESIS object ID; U1 = stable UNCERTAINTY object ID; PERSON1 = stable participant ID; R1.1 = stable policy-rule ID. Evidence is obtained material, not automatically true. Propositions are factual claims, hypotheses are possible explanations, and uncertainties are consequential unresolved questions. Operations refer to objects using their exact stable IDs; focus/status changes do not change statement truth. Participants are contextual entities, not CaseGraph node types.\n</OBJECT_LEGEND>",
        "<RELATION_LEGEND>\nSUPPORTS: A provides some support for B, not establishment. CONFLICTS: A creates tension with B, not automatic falsity. SPECIALIZES: child -> parent; child failure does not defeat parent. DEPENDS_ON: A materially depends on B. TARGETS: U concerns P or H. DERIVED_FROM: A was interpreted or derived from B. Edges do not encode chronology; focus/history does.\n</RELATION_LEGEND>",
        "<POLICY_CONTEXT>\n" + _raw_policy_context() + "\n</POLICY_CONTEXT>",
        "<POLICY_DISCIPLINE>\nReason evidence -> factual proposition or uncertainty -> applicable policy rule. Cite exact existing rule IDs such as R1.1 only when participant role, conduct/resource, temporal, location, and context scope match. Policy relevance does not establish factual truth. Preserve possession versus use, installed local LLM versus use during an examination, and association versus collaboration.\n</POLICY_DISCIPLINE>",
        "<STEWARD_OPERATIONS>\nEach operation is an API contract. First choose the smallest operation whose precondition is satisfied; then serialize only its exact branch.\n\nKEEP_FOCUS\nPRECONDITION: The current focus remains materially useful and no separate global graph-management change is required.\nACTION: Make no graph, status, or focus change.\nPOSTCONDITION: Current focus and every node status are unchanged.\nVALIDATE: Use KEEP_FOCUS only when you are not also archiving, reactivating, generalizing, or changing focus.\n\nSHIFT_FOCUS\nPRECONDITION: Another existing node is the useful destination and its status is ACTIVE.\nACTION: Set destination_node_id to that node.\nPOSTCONDITION: Only focus changes; the previous focus stays ACTIVE, and no node is archived or reactivated.\nVALIDATE: destination_node_id must be an exact existing ACTIVE node ID. Never use SHIFT_FOCUS to select an ARCHIVED node.\n\nGENERALIZE\nPRECONDITION: The current active child is too specific or its local frontier is exhausted while its broader active parent remains viable.\nACTION: Set target_node_id to the specific child being generalized FROM. The coordinator follows the one immediate active parent through child --SPECIALIZES--> parent.\nPOSTCONDITION: Focus moves one level to that parent; the child is not archived, rejected, or supplied as the parent argument.\nVALIDATE: target_node_id must be the exact existing ACTIVE child and must have an immediate active SPECIALIZES parent.\n\nARCHIVE\nPRECONDITION: An ACTIVE target no longer belongs in active reasoning.\nACTION: Set target_node_id to the node becoming ARCHIVED; if it is the current focus, also set destination_node_id to a different existing ACTIVE node.\nPOSTCONDITION: Target changes ACTIVE -> ARCHIVED. A non-focus target leaves focus unchanged; a focus target moves focus to the supplied destination.\nVALIDATE: ARCHIVE is about active participation, not truth, falsity, rejection, or disproval.\n\nREACTIVATE\nPRECONDITION: An ARCHIVED node is newly relevant to active reasoning.\nACTION: Set target_node_id to that archived node.\nPOSTCONDITION: Target changes ARCHIVED -> ACTIVE; focus does not change automatically.\nVALIDATE: Do not use SHIFT_FOCUS to select an archived object; use REACTIVATE first.\n\nSTOP_UNRESOLVED\nPRECONDITION: Trusted frontier context establishes that the global frontier is assessed, the local frontier is exhausted, and no materially useful frontier or action remains.\nACTION: Identify the trusted active uncertainty IDs that remain important and state concrete reopening conditions.\nPOSTCONDITION: Investigation stops while important uncertainty may remain.\nVALIDATE: Do not stop when trusted context reports a neglected candidate, useful region, or usable action. STOP_UNRESOLVED does not establish guilt, innocence, truth, or falsity.\n</STEWARD_OPERATIONS>",
        "<AUTHORITY_BOUNDARY>\nThe Steward does not create evidence, propositions, hypotheses, uncertainties, tools, or environment actions. The Investigator works locally; the Steward manages global graph focus/status.\n</AUTHORITY_BOUNDARY>",
        "<CASE_PARTICIPANTS>\n" + participant_text + "\n</CASE_PARTICIPANTS>",
        "<CASEGRAPH>\n" + json.dumps(graph, sort_keys=True) + "\n</CASEGRAPH>",
        "<CURRENT_FOCUS>\n" + scenario.focus.node_id + "\n</CURRENT_FOCUS>",
        "<FOCUS_HISTORY>\n" + focus_history + "\n</FOCUS_HISTORY>",
        "<DETERMINISTIC_FEATURES>\n" + json.dumps(features, sort_keys=True) + "\n</DETERMINISTIC_FEATURES>",
    ]
    if scenario.review_context is not None:
        sections.append("<TRUSTED_FRONTIER>\n" + json.dumps(scenario.review_context.model_dump(mode="json"), sort_keys=True) + "\nThis is trusted external structural input. Do not author or modify it.\n</TRUSTED_FRONTIER>")
    sections.extend(["<DYNAMIC_SCENARIO>\n" + scenario.description + "\n</DYNAMIC_SCENARIO>", "<OUTPUT_CONTRACT>\n" + _OUTPUT_CONTRACT + "\n</OUTPUT_CONTRACT>"])
    return "\n\n".join(sections)
