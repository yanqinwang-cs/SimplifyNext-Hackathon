from __future__ import annotations

import json
from typing import Any

from pydantic import TypeAdapter

from investigator.cycle import InvestigatorObservation, TURN_RESPONSE_ADAPTER
from investigator.cycle_prompt import build_investigator_cycle_prompt
from investigator.roles import StewardDecision


def build_investigator_prompt(observation: InvestigatorObservation) -> str:
    return build_investigator_cycle_prompt(observation)


def build_steward_prompt(snapshot: Any, review_context: Any) -> str:
    payload = {"graph": snapshot.graph.model_dump(mode="json"), "current_focus": snapshot.focus.model_dump(mode="json"), "review_context": review_context.model_dump(mode="json")}
    schema = TypeAdapter(StewardDecision).json_schema()
    guidance = "You are the Case Steward. Manage global focus and graph status only. Do not create nodes, choose exact enquiries, or decide guilt. Neglected or unvisited branches are not by themselves a reason to rotate. Terminal decisions require trusted frontier exhaustion. STOP_UNRESOLVED means consequential investigative uncertainty remains although no useful enquiry can reduce it. READY_FOR_HUMAN_DECISION means no useful investigation remains and no consequential investigative uncertainty requires another enquiry; it is a neutral workflow handoff, not a disciplinary judgment. Do not choose either terminal operation merely because one hypothesis appears persuasive. Return JSON only."
    return guidance + "\n\nCURRENT STATE:\n" + json.dumps(payload, sort_keys=True) + "\n\nEXACT PRODUCTION SCHEMA:\n" + json.dumps(schema, sort_keys=True)
