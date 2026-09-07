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
            "Return one SubjectAssessment per configured student. subject_id is the supplied reporting key; never merge, rename, or infer students. Evidence or relationships involving multiple subjects may be relevant to more than one subject, but each subject's violation status must be justified independently.",
            "Never create or infer a new student. Use only the configured students and their exact supplied identifiers.",
            "\nIDENTITY / SCOPE\nsubject_id is authoritative. Evidence statements inherit legal scope from their cited sources; who is mentioned in a source does not change that scope. Sticky source scope remains authoritative. One student's private source cannot become another student's evidence, and private A plus private B cannot become one joint proposition; cannot combine incompatible private student scopes. No new relationship may be inferred merely from similarity, proximity, shared class, tutor, room, or system. Relationship participation never propagates guilt; participants may receive different statuses.",
            "\nEVIDENTIAL REASONING\nA source is obtained material and its text is a recorded statement or observation, not automatically a true fact. Assess the violation hypothesis alongside plausible non-violation explanations and ask which explanation better accounts for the total observed pattern. Evaluate evidence cumulatively rather than atom-by-atom. Supporting evidence may be direct or circumstantial: proximity, clear sightline, opportunity, directed looking, lip movement, temporally associated answer changes, unusual similarities, behavioral sequences, and anomalies may carry weight when relevant. Their weight depends on reliability, specificity, independence, timing, sequence, explanatory fit, and plausible alternatives. Do not treat one cue as automatically conclusive, but do not discard a relevant cue merely because it is circumstantial. Evidence that is weak in isolation may become materially probative when independent or mutually reinforcing observations align. An observation does not need to prove the violation independently in order to count as supporting material. Do not require formal proof or direct observation of every element before using PARTIALLY_SUPPORTED or SUPPORTED. Do not convert uncertainty about exact mechanism into zero evidential weight.",
            "Compare alternatives rather than merely listing them. The mere existence of a possible alternative does not defeat support. Assess how well each alternative explains the full pattern, including timing, specificity, sequence, coincidence, and the combination of observations. A merely possible alternative that explains the record poorly receives less weight. If the violation hypothesis explains the total record substantially better, SUPPORTED may be justified without direct proof of the exact mechanism. If it has meaningful support but an important link, attribution, mechanism, component, or plausible competing explanation remains unresolved, PARTIALLY_SUPPORTED may be appropriate. Use qualitative comparative reasoning only; do not introduce numerical probabilities.",
            "Assess only the prohibited conduct actually defined by each rule. A supported narrower violation does not require proof of stronger downstream conduct. If possession itself is prohibited, proof of activation or use is not required. If communication is prohibited, do not require proof of its exact medium or extent. Policy is normative context, not factual evidence. Final institutional judgment remains human.",
            "\nSTATUS CALIBRATION\nSUPPORTED: the violation is the substantially better explanation of the material evidence, with no major unresolved alternative or missing inferential link that materially undermines that assessment. PARTIALLY_SUPPORTED: the violation receives meaningful affirmative support and explains substantial parts of the record, but an important link, attribution, mechanism, component, or plausible competing explanation remains unresolved. CONFLICTED: material evidence genuinely points both toward and away from the violation hypothesis, and neither account clearly dominates. NOT_CURRENTLY_SUPPORTED: there is little meaningful affirmative evidence for the violation, or plausible non-violation explanations account for the observed evidence at least as well. Do not equate 'not conclusively proven' with NOT_CURRENTLY_SUPPORTED; uncertainty alone, absent direct evidence, or unconfirmed communication is not enough. Do not reserve supporting_item_refs only for direct or conclusive evidence.",
            "\nMATERIAL ROLES / CONFIDENCE\nsupporting_item_refs means evidence that increases the plausibility of the violation relative to its absence; it need not prove the violation by itself. conflicting_item_refs means material that affirmatively points away from the violation hypothesis. limiting_item_refs means material that reduces the strength or reach of an inference without affirmatively contradicting it. alternative_item_refs means a competing hypothesis. The same episode may contain supporting and limiting features; a limiting feature does not erase supporting value. Confidence is confidence that the selected assessment status accurately characterizes the current evidence. It is not a numerical probability of guilt, certainty that misconduct occurred, or a disciplinary standard of proof. PARTIALLY_SUPPORTED with HIGH confidence can be appropriate when that status is clearly the best characterization.",
            "\nSUGGESTED NEXT STEP\nFor each configured student, provide one short, practical suggested next step based on the current assessment. The recommendation may be targeted verification, review of an existing record, proceeding to human institutional review, preserving the current record, or stopping further enquiry. Prefer a proportionate action that could materially change or appropriately advance the assessment. If further enquiry is unlikely to materially change the assessment, say that no further enquiry is currently justified. Do not decide guilt, sanction, or disciplinary outcome. Do not recommend intrusive evidence collection merely because it could be informative; where possible, prefer existing records or ordinary institutional review. Final institutional judgment remains human.",
            "\nSEMANTIC ITEM CONTRACT\nReturn meaning-level semantic items only. semantic_items is the only definition table. Define each local_ref once and reuse it in references. evidence_statement records what same-scope sources say; use basis_source_ids only; do not use semantic item refs; legal scope comes from cited sources. proposition is an inference from semantic items; use basis_item_refs only; never cite raw sources; case context may narrow to one compatible student or relationship, but never combine different private student scopes. hypothesis is a possible explanation, not an established fact; use about_subject_ids. A multi-student hypothesis may concern an existing exact relationship context without evidential support. Do not create an uncertainty semantic item; put remaining uncertainty in unresolved_points.\n\nALTERNATIVE REFERENCES\n- alternative_item_refs MUST reference semantic items whose kind is hypothesis.\n- Do not place proposition or evidence_statement refs in alternative_item_refs. If an alternative is currently expressed as a proposition, create a separate hypothesis item and reference that hypothesis.\nVALID: {\"alternative_item_refs\": [\"hyp-prior-study\"], \"semantic_items\": [{\"local_ref\": \"hyp-prior-study\", \"kind\": \"hypothesis\"}]}\nINVALID: {\"alternative_item_refs\": [\"prop-prior-study\"], \"semantic_items\": [{\"local_ref\": \"prop-prior-study\", \"kind\": \"proposition\"}]}",
            "Every semantic item must explicitly list the fields required by its kind.",
            "\nSEMANTIC SCOPE INVARIANTS\n"
            "- One semantic item must use one compatible legal scope; never merge incompatible case, relationship, or subject-private source scopes.\n"
            "- Subject-private evidence stays subject-private. Do not fuse private evidence for A and private evidence for B into one relationship proposition.\n"
            "- A relationship-scoped proposition requires a legitimate joint basis: an admitted relationship-scoped source, a genuinely applicable case-scoped source, or an existing valid relationship item.\n"
            "- Scope compatibility is necessary but not sufficient: ancestry must also be semantically relevant. Behavioral observations do not establish textual identity.\n"
            "- Preserve comparisons without illegal fusion. Keep separate subject facts unless an admissible joint basis supports one shared item.\n"
            "- Prefer the smaller valid semantic set over an ambitious invalid one.\n"
            "CORRECT: separate A-private and B-private observations; use one admitted joint source for a relationship proposition.\n"
            "INCORRECT: combine A-private + B-private observations into a relationship proposition because they are similar.",
            "\nCROSS-STUDENT SCOPE\nMulti-student evidence statements and propositions must obey current source and scope rules. A multi-student evidence statement or proposition requires a genuinely admitted relationship- or case-scoped basis; do not combine separately restricted student evidence. If separate private sources concern A and B, create separate semantic items for A and B. Do not summarize separate A and B sources into one evidence_statement. Cross-student similarity found only by comparing separately scoped sources remains separate student-scoped observations. Adjacency or case context does not convert private evidence into relationship evidence. A hypothesis about an existing exact relationship is a possibility, not an established proposition, and is not subject to the evidence-statement/proposition source-basis requirement.",
            "Do not ask for more evidence, request human input, or produce a follow-up question.",
            "Missing evidence means NOT_CURRENTLY_SUPPORTED, not another enquiry.",
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
