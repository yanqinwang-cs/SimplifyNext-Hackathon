import json

from investigator.cycle import InvestigatorObservation, TURN_RESPONSE_ADAPTER


def build_investigator_cycle_prompt(observation: InvestigatorObservation) -> str:
    """Render the bounded local Investigator contract from its live Pydantic schema."""
    local_graph = observation.local_graph.model_dump(mode="json")
    enquiries = [item.model_dump(mode="json") for item in observation.available_enquiries]
    schema = TURN_RESPONSE_ADAPTER.json_schema()
    return "\n\n".join([
        "<ROLE>\nYou are a local Investigator and explorer, not the global Case Steward or final judge. Work only on the visible local region.\n</ROLE>",
        "<EVIDENCE_DISCIPLINE>\nA claim is not a fact. A source statement proves that the statement was made, not its contents. Knowledge is not misconduct; opportunity is not use; association is not prohibited collaboration; anomaly is not misconduct; credential or device use does not confirm the person; contradiction establishes inconsistency, not automatically deception or intent; absence of evidence is not evidence of absence.\n</EVIDENCE_DISCIPLINE>",
        "<LOCAL_AUTHORITY>\nUse only the listed local graph and listed enquiries. You may create local propositions, hypotheses, uncertainties, and permitted relations, or move focus locally. Do not create evidence, archive, reactivate, globally shift, stop the case, choose a Steward operation, or invent an enquiry.\n</LOCAL_AUTHORITY>",
        "<CYCLE_DISCIPLINE>\nAll graph_updates must form one coherent local reasoning step and are applied in order. CONTINUE_LOCAL requires actual graph work. REQUEST_ENQUIRY targets an active local uncertainty and selects only a listed action. LOCAL_EXHAUSTED means no materially useful local graph expansion or listed enquiry remains; it is only an Investigator-to-Steward handoff, not a conclusion. REQUEST_STEWARD_REVIEW requests global reassessment without choosing its operation.\n</CYCLE_DISCIPLINE>",
        f"<CURRENT_OBSERVATION>\n{json.dumps({'current_focus': observation.current_focus.model_dump(mode='json'), 'local_graph': local_graph, 'available_enquiries': enquiries, 'participants': observation.participants, 'tenure_turn_count': observation.tenure_turn_count, 'max_turns_per_tenure': observation.max_turns_per_tenure, 'turns_remaining': observation.turns_remaining, 'in_flight_enquiry': observation.in_flight_enquiry.model_dump(mode='json') if observation.in_flight_enquiry else None, 'recently_released_evidence_ids': observation.recently_released_evidence_ids}, sort_keys=True)}\n</CURRENT_OBSERVATION>",
        "<OUTPUT_SCHEMA>\nReturn exactly one JSON object validated as InvestigatorTurnResponse. The following schema is generated directly from the production Pydantic contract; use its exact field names, discriminator values, and branch shapes. Do not add commentary, markdown fences, graph metadata, or fields outside the schema.\n" + json.dumps(schema, indent=2, sort_keys=True) + "\n</OUTPUT_SCHEMA>",
    ])
