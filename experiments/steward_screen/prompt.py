import json

from investigator.steward.features import region_health, tunnel_vision_indicators

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
    return """You are the Case Steward. Manage global graph focus and relevance. Do not investigate, choose an exact enquiry or tool, create nodes, or decide institutional guilt. Evidence is not automatically truth; support is not proof; conflict is not automatic refutation. Archived material remains historical. A failed child does not automatically defeat its parent. Return exactly one legal StewardDecision as JSON, with no markdown or explanation outside JSON. HANDOFF_TO_HUMAN is valid only when the supplied trusted frontier context shows no materially useful frontier remains; record important unresolved uncertainty IDs, which may be empty. HANDOFF_TO_HUMAN is a neutral workflow handoff, not a disciplinary judgement. The final disciplinary judgement remains human.

Scenario:
""" + scenario.description + "\n\nState:\n" + json.dumps({"graph": graph, "focus": scenario.focus.model_dump(mode="json"), "features": features, "trusted_review_context": context}, sort_keys=True) + "\n\nReturn one StewardDecision JSON object using only the allowed current operations."
