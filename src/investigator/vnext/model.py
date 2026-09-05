"""Reusable real-model Investigator adapter for vNext."""

import json
from collections.abc import Callable
from typing import Any

from investigator.llm import ModelCallResult, ModelClient
from investigator.vnext.models import InvestigatorAssessment, InvestigatorProposal, VNextRunInput
from investigator.vnext.semantic import InvestigatorSemanticAssessment
from investigator.vnext.source_applicability import SourceApplicabilityClassification
from investigator.vnext.relationships import relationship_scope_prompt_view
from investigator.vnext.warden import ProposalValidationIssue


class VNextInvestigatorModel:
    """One structured Investigator call using the current vNext prompt contract."""

    def __init__(self, client: ModelClient) -> None:
        self.client = client
        self.last_call: ModelCallResult | None = None

    def __call__(self, run_input: VNextRunInput) -> InvestigatorAssessment:
        return self.call_prompt(build_prompt(run_input), InvestigatorAssessment)

    def call_prompt(
        self,
        prompt: str,
        output_schema: type[Any],
        *,
        on_started: Callable[[str], None] | None = None,
        on_completed: Callable[[ModelCallResult], None] | None = None,
        on_failed: Callable[[Exception], None] | None = None,
    ) -> Any:
        """Make one call while exposing durable lifecycle boundaries to the runner."""

        if on_started is not None:
            on_started(prompt)
        try:
            self.last_call = self.client.call(prompt, output_schema)
        except Exception as exc:
            if on_failed is not None:
                on_failed(exc)
            raise
        if on_completed is not None:
            on_completed(self.last_call)
        return self.last_call.parsed


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
        if applicability.case_shared_allowed:
            sources["case_shared"].append(item)
        elif applicability.permitted_relationship_ids:
            sources["multi_student_candidate"].append({**item, "matched_student_ids": applicability.permitted_subject_ids})
        else:
            for subject_id in applicability.permitted_subject_ids:
                bucket = sources["student_specific"].setdefault(subject_id, [])
                if not any(existing["source_id"] == source_id for existing in bucket):
                    bucket.append(item)
    subjects = {
        subject_id: {"display_name": subject.display_name, "candidate_number": subject.candidate_number}
        for subject_id, subject in sorted(run_input.subjects.items())
    }
    relationships = relationship_scope_prompt_view(run_input.relationship_scopes)
    schema = InvestigatorSemanticAssessment.model_json_schema()
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
            "A multi-student semantic item is permitted only when one admitted source is available for that existing multi-student relationship context. Do not combine separately restricted student evidence into a multi-student item.",
            "Relationship participation does not establish knowledge, communication, use, intent, or misconduct. Each participant may receive a different status and must be assessed independently.",
            "Do not create a relationship merely because scripts are similar or students shared a class, tutor, room, or system. Preserve ambiguity when attribution is unsafe.",
            "Do not omit configured students with no incriminating evidence; assess them as NOT_CURRENTLY_SUPPORTED where appropriate.",
            "Every semantic item must explicitly list the configured student or students it concerns in about_subject_ids. A multi-student item must use one admitted joint source for the existing participant context; never widen private material to another student.",
            "Do not ask for more evidence, request human input, or produce a follow-up question.",
            "Missing evidence means NOT_CURRENTLY_SUPPORTED, not another enquiry.",
            "A supported narrower violation does not require proof of stronger downstream conduct.",
            "Assess only the prohibited conduct actually defined by each rule.",
            "If possession itself is prohibited, proof of activation or use is not required.",
            "If communication is prohibited, do not require proof of its exact medium or extent.",
            "Evidence discipline: a document is a source; text in it is a source statement or recorded observation, not automatically a true proposition. Reliability, independence, specificity, and conflict matter. A claim is not automatically a fact; contradiction establishes inconsistency, not deception or intent; similarity does not establish copying or collaboration; association does not establish prohibited collaboration; opportunity does not establish use; anomaly does not establish misconduct; credential or device linkage does not establish the human actor; knowledge does not establish guilt; and absence of current support does not establish innocence.",
            "Misconduct requires relevant conduct plus applicable policy context. Policy is normative evaluation criteria, not factual evidence. Assess each student independently; relationship participation never propagates guilt.",
            "Return meaning-level semantic items only. Do not emit implementation details, internal identifiers, internal links, validator terms, or registry details. Every semantic item must declare about_subject_ids. Define every semantic item exactly once in semantic_items; semantic_items is the only place where semantic objects are defined. Reuse each local_ref wherever that item is referenced and do not redefine an existing local_ref. Evidence statements use basis_source_ids and inherit legal scope from their cited source(s); who is mentioned in a source does not change where that source is legally usable. Do not widen an evidence statement to another student or relationship merely because that student is mentioned in the source text. One semantic item should have one coherent evidential scope. Do not combine evidence from different student-specific source scopes into one semantic item. If Candidate A and Candidate B have separate evidence, create separate semantic items for A and B. Do not summarize separate A and B sources into one evidence_statement. Do not create a joint A/B proposition from separate A-only and B-only semantic items. If two students exhibit similar observations, preserve them as separate student-scoped items unless genuine existing relationship-scoped evidence supports one joint semantic item. Propositions use basis_item_refs and cannot combine incompatible private student scopes. Alternative explanations are hypothesis items in semantic_items and are referenced using alternative_item_refs. Supporting, conflicting, limiting, and alternative references must name semantic item local_refs.",
            "\nSAME-SCOPE EXAMPLE\nBAD: one evidence_statement using A_invigilator_report and B_invigilator_report to summarize both students. GOOD: define one evidence_statement using A_invigilator_report for Candidate A and a separate evidence_statement using B_invigilator_report for Candidate B. BAD: combine an A-private item and a B-private item into one joint proposition. GOOD: retain separate A and B propositions unless one genuine relationship-scoped basis supports a joint proposition.",
            "Return JSON only. Use exactly the current schema below. Do not add fields.",
            "\nCONFIGURED STUDENTS (REPORTING TARGETS ONLY)\n" + json.dumps(subjects, indent=2),
            "\nAPPLICABLE POLICY / CONFIGURED VIOLATIONS (NORMATIVE CONTEXT, NOT FACTUAL EVIDENCE)\n" + json.dumps(run_input.rule_preset.model_dump(mode="json"), indent=2),
            "\nADMITTED EVIDENCE SOURCES — CASE-WIDE / STUDENT-SPECIFIC / MULTI-STUDENT CANDIDATE (SOURCE STATEMENTS ARE NOT AUTOMATICALLY TRUE)\n" + json.dumps(sources, indent=2),
            "\nEXISTING MULTI-STUDENT RELATIONSHIP CONTEXTS (STRUCTURAL CONTEXT ONLY; NOT FINDINGS)\n" + json.dumps(relationships, indent=2),
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
