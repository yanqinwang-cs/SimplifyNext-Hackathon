import json

from investigator.cycle import InvestigatorObservation, TURN_RESPONSE_ADAPTER
from investigator.roles.procedure import render_procedure


def build_investigator_cycle_prompt(observation: InvestigatorObservation) -> str:
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
        "<OPERATION_POLICY>\nIF a directly source-grounded graph operation is offered by the exact schema, use the relevant visible source reference and verify the observation against that source.\nELIF a statement is a small inference from visible graph material, use ADD_PROPOSITION with appropriate derivation or relation.\nELIF a statement is a broader explanation, reuse a substantively equivalent HYPOTHESIS; otherwise create one using the exact schema branch.\nELIF a consequential question remains unresolved, use ADD_UNCERTAINTY.\nIF precise target uncertainty and usefulness are justified, use REQUEST_EVIDENCE with every required field. ELIF useful context is missing but that specificity has not been earned, use REQUEST_OPEN with reason and optional information_sought. ELIF predefined actions are explicitly offered, use only a listed legacy REQUEST_ENQUIRY; ELSE PASS.\nIF a request returns ALREADY_AVAILABLE or NO_NEW_SOURCE: review current visible sources, continue from what they support, and do not immediately repeat the same request.\nIF a request is UNAVAILABLE: NEVER infer the requested proposition is false; reassess another useful enquiry, current focus, or Steward review.\nMOVE_FOCUS may target only a legal canonical graph node. Focus guides attention but does not limit Investigator visibility or referenceability.\nNEVER create a Steward operation, release a raw source, decide guilt, or use a hidden source or mechanism.\n</OPERATION_POLICY>",
        "<LOCAL_AUTHORITY>\nThe exact runtime Pydantic schema defines which graph operations are available. The coordinator defines mechanical legality. In production, investigator_visible_graph_node_ids equals the full canonical graph node set; active_reasoning_node_ids remains a compatibility diagnostic with the same production value. Raw SourceRegistry visibility is a separate namespace; a visible raw source is not inaccessible merely because there is no semantic graph node for it: it is read-only and has no semantic edges.\n</LOCAL_AUTHORITY>",
        "<CYCLE_DISCIPLINE>\nAll graph_updates form one coherent local step and are applied in order. Reason first, operation second; prerequisites must be satisfied before serialization. CONTINUE_LOCAL requires graph work. REQUEST_STEWARD_REVIEW requests global case management without selecting the Steward operation.\n</CYCLE_DISCIPLINE>",
        "<HUMAN_EVIDENCE_REQUESTS>\nBoth Investigator and Steward may request human information. A request must state a concrete information need, why it matters, and expected investigative value; never ask vaguely for more information. Prefer request_open when targeted specificity is not earned, and request_evidence only for one active uncertainty. Requesting does not mutate the graph. A fulfilled response registers a read-only SOURCE for review; it is not automatically semantic EVIDENCE.\n</HUMAN_EVIDENCE_REQUESTS>",
        f"<CASE_SOURCES>\n{sources}\n</CASE_SOURCES>",
        f"<CURRENT_OBSERVATION>\n{json.dumps({'current_focus': observation.current_focus.model_dump(mode='json'), 'local_graph': local_graph, 'available_enquiries': enquiries, 'participants': observation.participants, 'tenure_turn_count': observation.tenure_turn_count, 'max_turns_per_tenure': observation.max_turns_per_tenure, 'turns_remaining': observation.turns_remaining, 'in_flight_enquiry': observation.in_flight_enquiry.model_dump(mode='json') if observation.in_flight_enquiry else None, 'recently_released_evidence_ids': observation.recently_released_evidence_ids, 'workflow_feedback': observation.workflow_feedback}, sort_keys=True)}\n</CURRENT_OBSERVATION>",
        "<OUTPUT_SCHEMA>\nReturn exactly one JSON object validated as InvestigatorTurnResponse. The following schema is generated directly from the production Pydantic contract; use its exact field names, discriminator values, and branch shapes. Do not add commentary, markdown fences, graph metadata, or fields outside the schema.\n" + json.dumps(schema, indent=2, sort_keys=True) + "\n</OUTPUT_SCHEMA>",
    ])
