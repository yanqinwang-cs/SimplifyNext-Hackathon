import json
from typing import Any

from investigator.cycle import InvestigatorObservation, TURN_RESPONSE_ADAPTER
from investigator.roles.procedure import render_procedure


def _render_recent_actions(recent_actions: list[dict[str, Any]] | None) -> str:
    if not recent_actions:
        return "<RECENT_INVESTIGATOR_ACTIONS>\nNone.\n</RECENT_INVESTIGATOR_ACTIONS>"
    lines = ["<RECENT_INVESTIGATOR_ACTIONS>"]
    for item in recent_actions:
        lines.append(f"Turn committed at revision {item['revision']}:")
        for change in item.get("changes", []):
            details = f"- {change['operation']}"
            if change.get("node_id"):
                details += f" {change['node_type']} {change['node_id']}"
            if change.get("statement"):
                details += f": {change['statement']}"
            if change.get("source_ids"):
                details += f"; sources: {', '.join(change['source_ids'])}"
            if change.get("affected_node_ids"):
                details += f"; affected nodes: {', '.join(change['affected_node_ids'])}"
            lines.append(details)
        lines.append(f"- next step: {item['next_step']}")
        lines.append(f"- correction used: {'yes' if item.get('correction_used') else 'no'}")
    lines.append("Do not repeat a recent operation or statement merely by paraphrasing it.")
    lines.append("</RECENT_INVESTIGATOR_ACTIONS>")
    return "\n".join(lines)


def build_investigator_cycle_prompt(observation: InvestigatorObservation, recent_actions: list[dict[str, Any]] | None = None) -> str:
    """Render the Investigator contract from its live Pydantic schema."""
    def node_ref(node) -> str:
        if node.semantic_key:
            return node.semantic_key
        if node.node_type.value == "uncertainty":
            return "current_uncertainty" if node.id == observation.current_focus.node_id else "uncertainty_record"
        return f"{node.node_type.value}_record"

    local_graph = {
        "nodes": [{"reference": node_ref(node), "type": node.node_type.value, "statement": node.statement, "status": node.status.value} for node in observation.local_graph.nodes.values()],
        "edges": [{"source_reference": node_ref(observation.local_graph.nodes[edge.source_id]), "target_reference": node_ref(observation.local_graph.nodes[edge.target_id]), "relation": edge.relation.value, "strength": edge.strength.value if edge.strength else None} for edge in observation.local_graph.edges.values()],
    }
    enquiries = [item.model_dump(mode="json") for item in observation.available_enquiries]
    schema = TURN_RESPONSE_ADAPTER.json_schema()
    sources = "\n\n".join(
        f'<SOURCE source_id="{source.id}" filename="{source.name}">\n{source.content or ""}\n</SOURCE>'
        for source in observation.visible_sources
    )
    visibility = "The semantic graph below is the full current canonical case graph. Focus indicates where to concentrate attention; it does not hide or make other existing nodes illegal to reference." if observation.full_graph_visibility else "Work from the currently visible case context and treat the active reasoning region as the current bounded Investigator view."
    return "\n\n".join([
        f"<ROLE>\nYou are the Investigator, not the global Case Steward or final judge. {visibility}\n</ROLE>",
        "<EVIDENCE_DISCIPLINE>\nA claim is not a fact. A source statement establishes that the statement was made, not automatically that it is true. Knowledge is not misconduct; opportunity is not use; association is not prohibited collaboration; credential or device use does not confirm the person; anomaly is not misconduct; contradiction is not automatically deception or intent; absence of evidence is not evidence of absence. Distinguish raw SOURCE, semantic EVIDENCE, PROPOSITION, HYPOTHESIS, and UNCERTAINTY.\n</EVIDENCE_DISCIPLINE>",
        "<INVESTIGATOR_GLOSSARY>\nSOURCE is a registered original case record and is visible for review, but it is read-only and has no semantic edges. EVIDENCE is a direct observation grounded in readable SOURCEs. PROPOSITION is a smaller truth-apt inference from EVIDENCE/PROPOSITION. HYPOTHESIS is a broader explanation. UNCERTAINTY is a consequential unresolved question. Use readable semantic references in the graph view; system canonical IDs are not model-owned.\n</INVESTIGATOR_GLOSSARY>",
        "<INVESTIGATOR_PROCEDURE>\n" + render_procedure("investigator") + "\n</INVESTIGATOR_PROCEDURE>",
        "<OPERATION_POLICY>\nIF a directly source-grounded graph operation is offered by the exact schema, use the relevant visible source reference and verify the observation against that source.\nELIF a statement is a small inference from visible graph material, use ADD_PROPOSITION with appropriate derivation or relation.\nELIF a statement is a broader explanation, reuse a substantively equivalent HYPOTHESIS; otherwise create one using the exact schema branch.\nELIF a consequential question remains unresolved, use ADD_UNCERTAINTY.\nIF useful human information is needed, use REQUEST_INFORMATION with a concrete case-relevant question; target metadata and expected value are optional.\nELIF predefined actions are explicitly offered, use only a listed legacy REQUEST_ENQUIRY; ELSE PASS.\nIF a request returns ALREADY_AVAILABLE or NO_NEW_SOURCE: review current visible sources, continue from what they support, and do not immediately repeat the same request.\nIF a request is UNAVAILABLE: NEVER infer the requested proposition is false; reassess another useful enquiry, current focus, or Steward review.\nMOVE_FOCUS may target only a legal canonical graph node. Focus guides attention but does not limit Investigator visibility or referenceability.\nNEVER create a Steward operation, release a raw source, decide guilt, or use a hidden source or mechanism.\n</OPERATION_POLICY>",
        "<LOCAL_AUTHORITY>\nThe exact runtime Pydantic schema defines which graph operations are available. The coordinator defines mechanical legality. In production, investigator_visible_graph_node_ids equals the full canonical graph node set; active_reasoning_node_ids remains a compatibility diagnostic with the same production value. Raw SourceRegistry visibility is a separate namespace; a visible raw source is not inaccessible merely because there is no semantic graph node for it: it is read-only and has no semantic edges.\n</LOCAL_AUTHORITY>",
        '<DISCRIMINATORS>Graph updates use the exact field "operation". The next-step object uses the exact field "type". Do not swap these fields.</DISCRIMINATORS>',
        _render_recent_actions(recent_actions),
        "<MATERIAL_PROGRESS>\nMaterial progress means genuinely new source-grounded evidence, a substantively new proposition, a materially different or refined hypothesis, a consequential new uncertainty, a meaningful relation, a genuinely different unresolved region, a materially useful information request, or an appropriate exhaustion/handoff. Restating an existing node, trivial paraphrase, re-adding an equivalent source observation, repeating an uncertainty, or adding filler nodes is not progress. Do not recreate an existing observation, proposition, hypothesis, or uncertainty merely by paraphrasing it. Before creating a node, check the full graph and RECENT_INVESTIGATOR_ACTIONS for the same or materially equivalent claim or question. Revisit an existing node only when new evidence materially changes, supports, conflicts with, specializes, or clarifies it.\n</MATERIAL_PROGRESS>",
        "<CYCLE_DISCIPLINE>\nAll graph_updates form one coherent local step and are applied in order. Reason first, operation second; prerequisites must be satisfied before serialization. CONTINUE_LOCAL requires actual material graph work, not filler. If local evidence is exhausted, prefer REQUEST_INFORMATION with a concrete, case-relevant, answerable, materially useful question when one can be stated; otherwise use REQUEST_STEWARD_REVIEW or LOCAL_EXHAUSTED. REQUEST_STEWARD_REVIEW requests global case management without selecting the Steward operation.\n</CYCLE_DISCIPLINE>",
        "<HUMAN_EVIDENCE_REQUESTS>\nBoth Investigator and Steward may use REQUEST_INFORMATION. Ask a concrete, case-relevant question that can materially advance the investigation; broad is allowed, vacuous is not. Target metadata and expected information value are optional. Requesting does not mutate the graph. A fulfilled response registers a read-only SOURCE for review; it is not automatically semantic EVIDENCE.\n</HUMAN_EVIDENCE_REQUESTS>",
        '<LEGACY_COMPATIBILITY>\nThe legacy "request_open" and "request_evidence" payload names are accepted only when migrating older responses and are normalized to REQUEST_INFORMATION. Do not select either legacy name in a new production response.\n</LEGACY_COMPATIBILITY>',
        f"<CASE_SOURCES>\n{sources}\n</CASE_SOURCES>",
        f"<CURRENT_OBSERVATION>\n{json.dumps({'current_focus': observation.current_focus.model_dump(mode='json'), 'local_graph': local_graph, 'available_enquiries': enquiries, 'participants': observation.participants, 'tenure_turn_count': observation.tenure_turn_count, 'max_turns_per_tenure': observation.max_turns_per_tenure, 'turns_remaining': observation.turns_remaining, 'in_flight_enquiry': observation.in_flight_enquiry.model_dump(mode='json') if observation.in_flight_enquiry else None, 'recently_released_evidence_ids': observation.recently_released_evidence_ids, 'workflow_feedback': observation.workflow_feedback}, sort_keys=True)}\n</CURRENT_OBSERVATION>",
        "<OUTPUT_SCHEMA>\nReturn exactly one JSON object validated as InvestigatorTurnResponse. The following schema is generated directly from the production Pydantic contract; use its exact field names, discriminator values, and branch shapes. Do not add commentary, markdown fences, graph metadata, or fields outside the schema.\n" + json.dumps(schema, indent=2, sort_keys=True) + "\n</OUTPUT_SCHEMA>",
    ])
