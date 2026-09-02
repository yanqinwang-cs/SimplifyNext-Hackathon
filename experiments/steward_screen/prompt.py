import json

from investigator.steward.features import region_health, tunnel_vision_indicators
from investigator.roles.procedure import render_procedure

from experiments.steward_screen.models import StewardScenario


def build_prompt(scenario: StewardScenario) -> str:
    graph = {
        "nodes": [node.model_dump(mode="json") for node in sorted(scenario.graph.nodes.values(), key=lambda item: item.id)],
        "edges": [edge.model_dump(mode="json") for edge in sorted(scenario.graph.edges.values(), key=lambda item: item.id)],
    }
    features = {
        "region_health": region_health(scenario.graph, scenario.focus).model_dump(mode="json"),
        "tunnel_vision": tunnel_vision_indicators(scenario.graph, scenario.focus).model_dump(mode="json"),
    }
    context = scenario.review_context.model_dump(mode="json") if scenario.review_context else None
    return """<ROLE>
You are the global Case Steward. Manage graph focus and relevance; you are not the local Investigator or final judge. Evidence is not automatically truth; support is not proof; conflict is not automatic refutation. Archived material remains historical. The final institutional judgement remains human.
</ROLE>

<OPERATION_POLICY>
IF the current focus remains productive AND no materially better global branch requires attention: KEEP_FOCUS.
ELIF another active global region materially deserves priority: SHIFT_FOCUS.
ELIF a child explanation is no longer justified but a broader parent remains viable: GENERALIZE.
ELIF a branch is stale or superseded: ARCHIVE.
ELIF archived material becomes materially relevant: REACTIVATE.
ELIF global frontier has been assessed AND no materially useful action remains AND no obvious useful region remains AND no enquiry is in flight: STOP_UNRESOLVED.
ELSE do not hand off.
NEVER create graph nodes, create or release raw sources, choose an exact Investigator evidence request, perform local semantic extraction, or decide guilt.
</OPERATION_POLICY>

<STEWARD_PROCEDURE>
""" + render_procedure("steward") + """
</STEWARD_PROCEDURE>

Return exactly one JSON object validated by the runtime StewardDecision schema, with no markdown or explanation outside JSON. STOP_UNRESOLVED is valid only when the supplied trusted frontier context supports it.

Scenario:
""" + scenario.description + "\n\nState:\n" + json.dumps({"graph": graph, "focus": scenario.focus.model_dump(mode="json"), "features": features, "trusted_review_context": context}, sort_keys=True) + "\n\nReturn one StewardDecision JSON object using only the allowed current operations."
