"""Reusable real-model Investigator adapter for vNext."""

import json

from investigator.llm import ModelCallResult, ModelClient
from investigator.vnext.models import InvestigatorAssessment, InvestigatorProposal, VNextRunInput
from investigator.vnext.source_applicability import SourceApplicabilityClassification
from investigator.vnext.relationships import relationship_scope_prompt_view
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
    sources = {
        "case_shared": [],
        "student_specific": {},
        "multi_student_candidate": [],
    }
    for source_id, source in sorted(run_input.sources.items()):
        item = {"source_id": source_id, "filename": source.name, "content": source.content or ""}
        applicability = run_input.source_applicability[source_id]
        if applicability.classification is SourceApplicabilityClassification.CASE_SHARED:
            sources["case_shared"].append(item)
        elif applicability.classification is SourceApplicabilityClassification.SINGLE_STUDENT_DEFAULT:
            sources["student_specific"].setdefault(next(iter(run_input.subjects), "case_subject"), []).append(item)
        elif applicability.classification is SourceApplicabilityClassification.STUDENT_SPECIFIC:
            for subject_id in applicability.matched_student_ids:
                bucket = sources["student_specific"].setdefault(subject_id, [])
                if not any(existing["source_id"] == source_id for existing in bucket):
                    bucket.append(item)
            if applicability.trusted_scope is not None and applicability.trusted_scope.subject_id:
                bucket = sources["student_specific"].setdefault(applicability.trusted_scope.subject_id, [])
                if not any(existing["source_id"] == source_id for existing in bucket):
                    bucket.append(item)
        else:
            sources["multi_student_candidate"].append({**item, "matched_student_ids": applicability.matched_student_ids})
    subjects = {
        subject_id: {"display_name": subject.display_name, "candidate_number": subject.candidate_number}
        for subject_id, subject in sorted(run_input.subjects.items())
    }
    relationships = relationship_scope_prompt_view(run_input.relationship_scopes)
    schema = InvestigatorAssessment.model_json_schema()
    graph_scope_schema = schema.get("$defs", {}).get("GraphScope")
    if isinstance(graph_scope_schema, dict):
        properties = graph_scope_schema.get("properties")
        if isinstance(properties, dict):
            properties.pop("relationship_id", None)
        required = graph_scope_schema.get("required")
        if isinstance(required, list) and "relationship_id" in required:
            required.remove("relationship_id")
    retry_section = (
        "\nDETERMINISTIC RETRY CONSTRAINTS\n"
        + "\n".join(f"- {constraint}" for constraint in run_input.retry_constraints)
        + "\n"
        if run_input.retry_constraints
        else ""
    )
    return "\n".join(
        [
            "You are the Investigator for one complete finite assessment.",
            "Evaluate every configured violation exactly once for every configured assessment subject and return the complete assessment in one response.",
            "Return one SubjectAssessment per configured student. subject_id is the supplied reporting key; never merge, rename, or infer students.",
            "subject_id is the authoritative identity key. A recorded relationship is an association or observation, not automatically prohibited collaboration.",
            "Evidence or relationships involving multiple subjects may be relevant to more than one subject, but each subject's violation status must be justified independently.",
            "Never create or infer a new student. Use only the configured students and their exact supplied identifiers.",
            "A one-student source cannot support another student. Do not stitch separate A-only and B-only sources into a relationship.",
            "A relationship scope may be proposed only when one admitted source jointly identifies every proposed participant. A source naming multiple students does not establish collaboration.",
            "do not invent relationships. Use only the supplied relationship scopes or propose one with the required joint source.",
            "Relationship participation does not establish knowledge, communication, use, intent, or misconduct. Each participant may receive a different status and must be assessed independently.",
            "Do not create a relationship merely because scripts are similar or students shared a class, tutor, room, or system. Preserve ambiguity when attribution is unsafe.",
            "Do not omit configured students with no incriminating evidence; assess them as NOT_CURRENTLY_SUPPORTED where appropriate.",
            "In multi-student runs, every semantic graph node requires an explicit appropriate scope. Use CASE only for truly shared evidence, SUBJECT for one student, and RELATIONSHIP for material inherently concerning participants of a validated relationship scope. Never widen private material to CASE or another student.",
            "Do not ask for more evidence, request human input, or produce a follow-up question.",
            "Missing evidence means NOT_CURRENTLY_SUPPORTED, not another enquiry.",
            "A supported narrower violation does not require proof of stronger downstream conduct.",
            "Assess only the prohibited conduct actually defined by each rule.",
            "If possession itself is prohibited, proof of activation or use is not required.",
            "If communication is prohibited, do not require proof of its exact medium or extent.",
            "Evidence discipline: a document is a source; text in it is a source statement or recorded observation, not automatically a true proposition. Reliability, independence, specificity, and conflict matter. A claim is not automatically a fact; contradiction establishes inconsistency, not deception or intent; similarity does not establish copying or collaboration; association does not establish prohibited collaboration; opportunity does not establish use; anomaly does not establish misconduct; credential or device linkage does not establish the human actor; knowledge does not establish guilt; and absence of current support does not establish innocence.",
            "Misconduct requires relevant conduct plus applicable policy context. Policy is normative evaluation criteria, not factual evidence. Assess each student independently; relationship participation never propagates guilt.",
            "Raw source IDs identify source records, not automatically established facts. Use graph proposals for any E/P/H/U concepts you need, and reference proposal local_ref values in the assessment after proposing them. Relationship scopes use local relationship_ref values such as R1, never persistent relationship IDs.",
            "Return JSON only. Use exactly the current schema below. Do not add fields.",
            "\nCONFIGURED STUDENTS (REPORTING TARGETS ONLY)\n" + json.dumps(subjects, indent=2),
            "\nAPPLICABLE POLICY / CONFIGURED VIOLATIONS (NORMATIVE CONTEXT, NOT FACTUAL EVIDENCE)\n" + json.dumps(run_input.rule_preset.model_dump(mode="json"), indent=2),
            "\nADMITTED EVIDENCE SOURCES — CASE-WIDE / STUDENT-SPECIFIC / MULTI-STUDENT CANDIDATE (SOURCE STATEMENTS ARE NOT AUTOMATICALLY TRUE)\n" + json.dumps(sources, indent=2),
            "\nAVAILABLE INTERNAL RELATIONSHIP SCOPES (LOCAL REFS ONLY; STRUCTURAL SCOPE, NOT FINDINGS)\n" + json.dumps(relationships, indent=2),
            "\nEXPECTED CONFIGURED STUDENT IDS\n" + json.dumps(sorted(run_input.subjects) or ["case_subject"]),
            retry_section,
            "\nEXACT INVESTIGATOR ASSESSMENT JSON SCHEMA\n"
            + json.dumps(schema, indent=2, sort_keys=True),
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
            "Do not preserve or recreate a relation operation when another retained operation already creates the same semantic relation. If validation identifies a duplicate relation, remove only the redundant operation.",
            "Do not add new factual predicates, conduct, intent, use, communication, assistance, or certainty to node statements during repair unless that meaning was already present in the frozen first assessment or proposal.",
            "When creating a missing node required only for graph representation, use the narrowest statement needed to represent the existing frozen semantic conclusion.",
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
