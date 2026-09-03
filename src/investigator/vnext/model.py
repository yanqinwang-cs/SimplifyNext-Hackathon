"""Reusable real-model Investigator adapter for vNext."""

import json

from investigator.llm import ModelCallResult, ModelClient
from investigator.vnext.models import InvestigatorAssessment, InvestigatorProposal, VNextRunInput
from investigator.vnext.warden import ProposalValidationIssue


class VNextInvestigatorModel:
    """One structured Investigator call using the current vNext prompt contract."""

    def __init__(self, client: ModelClient) -> None:
        self.client = client
        self.last_call: ModelCallResult | None = None

    def __call__(self, run_input: VNextRunInput) -> InvestigatorAssessment:
        self.last_call = self.client.call(build_prompt(run_input), InvestigatorAssessment)
        return self.last_call.parsed  # type: ignore[return-value]


def build_prompt(run_input: VNextRunInput) -> str:
    """Build the exact vNext prompt from the current typed schemas and inputs."""
    sources = [
        {
            "source_id": source_id,
            "source_type": source.source_type.value,
            "title": source.name,
            "content": source.content or "",
        }
        for source_id, source in sorted(run_input.sources.items())
    ]
    return "\n".join(
        [
            "You are the Investigator for one complete finite assessment.",
            "Evaluate every configured violation exactly once and return the complete assessment in one response.",
            "Do not ask for more evidence, request human input, or produce a follow-up question.",
            "Missing evidence means NOT_CURRENTLY_SUPPORTED, not another enquiry.",
            "A supported narrower violation does not require proof of stronger downstream conduct.",
            "Assess only the prohibited conduct actually defined by each rule.",
            "If possession itself is prohibited, proof of activation or use is not required.",
            "If communication is prohibited, do not require proof of its exact medium or extent.",
            "Evidence discipline: a claim is not automatically a fact; a source statement is not automatically true; association is not collaboration; opportunity is not use; anomaly is not misconduct; absence of evidence is not evidence of absence; unsupported does not mean innocence established.",
            "Raw source IDs identify source records, not automatically established facts. Use graph proposals for any E/P/H/U concepts you need, and reference proposal local_ref values in the assessment after proposing them.",
            "Return JSON only. Use exactly the current schema below. Do not add fields.",
            "\nCASE CONTEXT\n" + (run_input.case_context or ""),
            "\nRULE PRESET\n" + json.dumps(run_input.rule_preset.model_dump(mode="json"), indent=2),
            "\nCURRENT RAW SOURCES\n" + json.dumps(sources, indent=2),
            "\nEXACT INVESTIGATOR ASSESSMENT JSON SCHEMA\n"
            + json.dumps(InvestigatorAssessment.model_json_schema(), indent=2, sort_keys=True),
        ]
    )


def build_corrective_prompt(
    assessment: InvestigatorAssessment,
    issues: list[ProposalValidationIssue],
) -> str:
    """Build a proposal-only repair request from deterministic Warden issues."""
    return "\n".join(
        [
            "Your previous InvestigatorAssessment is the authoritative semantic assessment for this retry.",
            "Do NOT re-investigate the case, change violation conclusions, change confidence, or add unrelated reasoning.",
            "Repair only the graph-contract defects listed below.",
            "Preserve unrelated valid graph updates, local_ref names, and intended semantic meaning. Operations identified by validation may be changed or removed exactly as required by their corrective action.",
            "Do not preserve an operation merely for structural similarity if a validation issue explicitly instructs you to remove it.",
            "Interpret issue fields precisely: allowed_types are for the failed field; construction_allowed_types are legal inputs for constructing a missing node; known_illegal_refs are refs illegal for this specific field only, not globally.",
            "Return only a corrected InvestigatorProposal matching its required schema.",
            "\nPREVIOUS PROPOSAL\n" + json.dumps(assessment.proposal.model_dump(mode="json"), indent=2),
            "\nDETERMINISTIC VALIDATION ISSUES\n"
            "Each issue is authoritative. Read its operation_index, field, problem, and required_action.\n"
            "Known refs that are illegal for this specific field only are not necessarily illegal as construction inputs.\n"
            + json.dumps([issue.model_dump(mode="json") for issue in issues], indent=2),
            "\nPROPOSAL JSON SCHEMA\n" + json.dumps(InvestigatorProposal.model_json_schema(), indent=2, sort_keys=True),
        ]
    )
