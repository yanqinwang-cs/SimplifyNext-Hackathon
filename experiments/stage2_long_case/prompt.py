from __future__ import annotations

import json
from typing import Any

from investigator.cycle_prompt import build_investigator_cycle_prompt
from investigator.cycle import InvestigatorObservation
from investigator.roles import StewardDecision
from pydantic import TypeAdapter

from .fixture import Stage2Fixture


def _sources(fixture: Stage2Fixture) -> str:
    return "\n\n".join(f"<SOURCE source_id=\"{item.source_id}\" filename=\"{item.filename}\">\n{item.content}\n</SOURCE>" for item in fixture.evidence)


def build_prompt(observation: InvestigatorObservation, fixture: Stage2Fixture) -> str:
    guidance = "<SOURCE_EVIDENCE_CONTRACT> SOURCE is the original record. CASE_SOURCES are immutable records outside the graph and are globally readable. source_ids are a separate namespace used only by add_evidence. EVIDENCE is a concise coherent direct observation or direct comparison grounded in one or more visible sources; frame human statements as statements and technical records as records. Do not create new EVIDENCE by combining existing graph EVIDENCE; use PROPOSITION for novel inference and DERIVED_FROM for its semantic basis. Before creating EVIDENCE, reuse an active observation if it already captures the same content; re-read a source only for a materially distinct observation. PROPOSITION is a concise truth-apt interpretive claim, HYPOTHESIS an explanatory account, and UNCERTAINTY a consequential question. Graph IDs/local_refs refer only to semantic graph nodes. The ACTIVE REASONING VIEW is bounded semantic working memory, not the source-access boundary. </SOURCE_EVIDENCE_CONTRACT>"
    return fixture.introduction + "\n\n" + guidance + "\n\n<CASE_SOURCES>\n" + _sources(fixture) + "\n</CASE_SOURCES>\n\n" + build_investigator_cycle_prompt(observation)


def build_steward_prompt(snapshot: Any, context: Any, fixture: Stage2Fixture) -> str:
    payload = {"graph": snapshot.graph.model_dump(mode="json"), "current_focus": snapshot.focus.model_dump(mode="json"), "review_context": context.model_dump(mode="json")}
    schema = TypeAdapter(StewardDecision).json_schema()
    return ("You are the Case Steward. Manage global graph focus and status only; do not investigate, create nodes, or decide guilt. All public sources are available from turn 1 in CASE SOURCES, outside the semantic graph. Do not create evidence or mutate sources. A source records what it says, not automatic truth. Review the complete graph and frontier. HANDOFF_TO_HUMAN is a neutral human handoff and is valid only when no materially useful frontier remains. Return JSON only.\n\nCURRENT STATE:\n" + json.dumps(payload, sort_keys=True) + "\n\nCASE SOURCES:\n" + _sources(fixture) + "\n\nEXACT PRODUCTION SCHEMA:\n" + json.dumps(schema, sort_keys=True))
