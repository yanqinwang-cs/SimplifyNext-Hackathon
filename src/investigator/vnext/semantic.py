"""Semantic Investigator IR and deterministic graph compilation."""

from __future__ import annotations

from enum import Enum
import re

from pydantic import BaseModel, ConfigDict, Field, model_validator

from investigator.graph import GraphScope, GraphScopeType
from investigator.vnext.models import (
    AssessmentRulePreset,
    AssessmentStatus,
    AlternativeExplanation,
    Confidence,
    FurthestJustifiedConclusion,
    InvestigatorAssessment,
    InvestigatorProposal,
    SubjectAssessment,
    ViolationAssessment,
    VNextRunInput,
)
from investigator.roles.investigator import AddConflictCommand, AddEvidenceCommand, AddHypothesisCommand, AddPropositionCommand, AddSupportCommand, AddUncertaintyCommand
from investigator.vnext.source_applicability import source_applies_to_student, source_jointly_identifies


class SemanticItemKind(str, Enum):
    EVIDENCE_STATEMENT = "evidence_statement"
    PROPOSITION = "proposition"
    HYPOTHESIS = "hypothesis"
    UNCERTAINTY = "uncertainty"


class SemanticRole(str, Enum):
    SUPPORTING = "supporting"
    CONFLICTING = "conflicting"
    LIMITING = "limiting"
    ALTERNATIVE = "alternative"
    UNRESOLVED = "unresolved"


class SemanticItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    local_ref: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    kind: SemanticItemKind
    statement: str = Field(min_length=1)
    about_subject_ids: list[str] = Field(default_factory=list)
    basis_source_ids: list[str] = Field(default_factory=list)
    basis_item_refs: list[str] = Field(default_factory=list)
    semantic_role: SemanticRole | None = None
    target_ref: str | None = None

    @model_validator(mode="after")
    def refs_are_unique(self) -> "SemanticItem":
        for name in ("about_subject_ids", "basis_source_ids", "basis_item_refs"):
            values = getattr(self, name)
            if len(values) != len(set(values)):
                raise ValueError(f"{name} must be unique")
        return self

    @model_validator(mode="after")
    def kind_has_appropriate_basis(self) -> "SemanticItem":
        if self.kind is SemanticItemKind.EVIDENCE_STATEMENT and not self.basis_source_ids:
            raise ValueError("evidence_statement requires basis_source_ids")
        if self.kind is SemanticItemKind.PROPOSITION:
            if not self.basis_item_refs:
                raise ValueError("proposition requires basis_item_refs")
        return self


class SemanticViolationAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    violation_id: str = Field(min_length=1)
    status: AssessmentStatus
    supporting_item_refs: list[str] = Field(default_factory=list)
    conflicting_item_refs: list[str] = Field(default_factory=list)
    limiting_item_refs: list[str] = Field(default_factory=list)
    alternative_item_refs: list[str] = Field(default_factory=list)
    unresolved_points: list[str] = Field(default_factory=list)
    reasoning_summary: str = Field(min_length=1)
    confidence: Confidence


class SemanticSubjectAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject_id: str = Field(min_length=1)
    violation_assessments: list[SemanticViolationAssessment] = Field(min_length=1)
    furthest_conclusion: FurthestJustifiedConclusion


class InvestigatorSemanticAssessment(BaseModel):
    """Assessment-only schema exposed to the normal vNext model call."""

    model_config = ConfigDict(extra="forbid")

    subject_assessments: list[SemanticSubjectAssessment] = Field(min_length=1)
    semantic_items: list[SemanticItem] = Field(default_factory=list)
    alternative_explanations: list[SemanticItem] = Field(default_factory=list)

    @model_validator(mode="after")
    def refs_are_unique(self) -> "InvestigatorSemanticAssessment":
        refs = [item.local_ref for item in self.semantic_items + self.alternative_explanations]
        if len(refs) != len(set(refs)):
            raise ValueError("semantic item local_ref values must be unique")
        return self


class SemanticValidationError(ValueError):
    """The model's semantic assessment cannot be represented safely."""


def _scope_for_item(
    item: SemanticItem,
    run_input: VNextRunInput,
    resolved_scopes: dict[str, GraphScope],
) -> GraphScope:
    about = set(item.about_subject_ids)
    if not about.issubset(run_input.subjects):
        raise SemanticValidationError(f"{item.local_ref} references unknown subject IDs")
    unknown_sources = set(item.basis_source_ids) - set(run_input.sources)
    if unknown_sources:
        raise SemanticValidationError(f"{item.local_ref} references unknown source IDs: {sorted(unknown_sources)}")
    if item.kind is SemanticItemKind.PROPOSITION:
        unknown_basis = set(item.basis_item_refs) - set(resolved_scopes)
        if unknown_basis:
            raise SemanticValidationError(f"{item.local_ref} requires earlier basis_item_refs: {sorted(unknown_basis)}")
        basis_scopes = [resolved_scopes[ref] for ref in item.basis_item_refs]
        if len(about) > 1:
            matches = [scope for scope in run_input.relationship_scopes.values() if set(scope.student_ids) == about]
            if len(matches) != 1:
                raise SemanticValidationError(f"{item.local_ref} has no exact relationship scope")
            relationship_scope = GraphScope(scope_type=GraphScopeType.RELATIONSHIP, relationship_ref=matches[0].local_ref)
            if not any(scope.model_dump(mode="json") == relationship_scope.model_dump(mode="json") for scope in basis_scopes):
                raise SemanticValidationError(f"{item.local_ref} requires a relationship-valid joint source basis for this participant set")
            if any(not scope_allows_semantic_scope(scope, about, run_input) for scope in basis_scopes):
                raise SemanticValidationError(f"{item.local_ref} has a basis item outside its participant scope")
            return relationship_scope
        if len(about) == 1:
            subject_id = next(iter(about))
            if any(not scope_allows_semantic_scope(scope, about, run_input) for scope in basis_scopes):
                raise SemanticValidationError(f"{item.local_ref} has a basis item outside subject {subject_id!r}")
            return GraphScope(scope_type=GraphScopeType.SUBJECT, subject_id=subject_id)
        if any(scope.scope_type is not GraphScopeType.CASE for scope in basis_scopes):
            raise SemanticValidationError(f"{item.local_ref} has no case-compatible basis")
        return GraphScope(scope_type=GraphScopeType.CASE)
    if len(about) == 1:
        subject_id = next(iter(about))
        if any(not source_applies_to_student(run_input.source_applicability[source_id], subject_id, run_input.subjects) for source_id in item.basis_source_ids):
            raise SemanticValidationError(f"{item.local_ref} cites a source not permitted for {subject_id!r}")
        return GraphScope(scope_type=GraphScopeType.SUBJECT, subject_id=subject_id)
    if len(about) > 1:
        matches = [scope for scope in run_input.relationship_scopes.values() if set(scope.student_ids) == about]
        if len(matches) != 1:
            raise SemanticValidationError(f"{item.local_ref} has no exact relationship scope")
        if not any(source_jointly_identifies(run_input.source_applicability[source_id], about, run_input.subject_relationships) for source_id in item.basis_source_ids):
            raise SemanticValidationError(f"{item.local_ref} lacks a relationship-valid joint source")
        return GraphScope(scope_type=GraphScopeType.RELATIONSHIP, relationship_ref=matches[0].local_ref)
    if not item.basis_source_ids or not all(run_input.source_applicability[source_id].case_shared_allowed for source_id in item.basis_source_ids):
        raise SemanticValidationError(f"{item.local_ref} has no case-compatible basis")
    return GraphScope(scope_type=GraphScopeType.CASE)


def scope_allows_semantic_scope(scope: GraphScope, subjects: set[str], run_input: VNextRunInput) -> bool:
    if scope.scope_type is GraphScopeType.CASE:
        return True
    if scope.scope_type is GraphScopeType.SUBJECT:
        return len(subjects) == 1 and scope.subject_id in subjects
    relationship = next((item for item in run_input.relationship_scopes.values() if item.local_ref == scope.relationship_ref), None)
    return relationship is not None and subjects.issubset(set(relationship.student_ids))


def semantic_item_has_source_ancestry(
    local_ref: str,
    items: dict[str, SemanticItem],
    seen: set[str] | None = None,
) -> bool:
    seen = seen or set()
    if local_ref in seen or local_ref not in items:
        return False
    seen.add(local_ref)
    item = items[local_ref]
    if item.kind is SemanticItemKind.EVIDENCE_STATEMENT:
        return bool(item.basis_source_ids)
    if item.kind is not SemanticItemKind.PROPOSITION or not item.basis_item_refs:
        return False
    return all(semantic_item_has_source_ancestry(ref, items, seen.copy()) for ref in item.basis_item_refs)


def _ref_name(value: str, used: set[str]) -> str:
    normalized = re.sub(r"[^a-z0-9_]+", "_", value.lower()).strip("_") or "item"
    normalized = normalized[:60]
    candidate = normalized
    index = 2
    while candidate in used:
        candidate = f"{normalized}_{index}"
        index += 1
    used.add(candidate)
    return candidate


def compile_semantic_assessment(assessment: InvestigatorSemanticAssessment, run_input: VNextRunInput, *, preset: AssessmentRulePreset | None = None) -> InvestigatorAssessment:
    """Validate semantic permissions and compile one Warden proposal."""

    preset = preset or run_input.rule_preset
    expected_subjects = list(run_input.subjects) or ["case_subject"]
    expected_violations = [item.violation_id for item in preset.violations]
    items = assessment.semantic_items + assessment.alternative_explanations
    by_ref = {item.local_ref: item for item in items}
    if len(by_ref) != len(items):
        raise SemanticValidationError("semantic item local_ref values must be unique")
    scopes: dict[str, GraphScope] = {}
    for item in items:
        scopes[item.local_ref] = _scope_for_item(item, run_input, scopes)
    by_subject = {item.subject_id: item for item in assessment.subject_assessments}
    if set(by_subject) != set(expected_subjects):
        raise SemanticValidationError("semantic assessment must cover exactly the configured students")
    for subject_id in expected_subjects:
        violations = by_subject[subject_id].violation_assessments
        if [item.violation_id for item in violations] != expected_violations:
            raise SemanticValidationError(f"{subject_id!r} must cover each configured violation exactly once")
        for violation in violations:
            refs = violation.supporting_item_refs + violation.conflicting_item_refs + violation.limiting_item_refs + violation.alternative_item_refs
            if any(ref not in by_ref for ref in refs):
                raise SemanticValidationError(f"{subject_id!r}/{violation.violation_id} references an unknown semantic item")
            if violation.status in {AssessmentStatus.SUPPORTED, AssessmentStatus.PARTIALLY_SUPPORTED, AssessmentStatus.CONFLICTED} and not violation.supporting_item_refs:
                raise SemanticValidationError(f"{subject_id!r}/{violation.violation_id} requires supporting material")
            for field_name, allowed_kinds in (
                ("supporting_item_refs", {SemanticItemKind.EVIDENCE_STATEMENT, SemanticItemKind.PROPOSITION}),
                ("conflicting_item_refs", {SemanticItemKind.EVIDENCE_STATEMENT, SemanticItemKind.PROPOSITION}),
                ("limiting_item_refs", {SemanticItemKind.EVIDENCE_STATEMENT, SemanticItemKind.PROPOSITION}),
                ("alternative_item_refs", {SemanticItemKind.HYPOTHESIS}),
            ):
                for ref in getattr(violation, field_name):
                    item = by_ref[ref]
                    if item.kind not in allowed_kinds:
                        label = field_name.removesuffix("_item_refs").replace("_", " ")
                        raise SemanticValidationError(f"{label} material {ref!r} has invalid semantic item kind")
                    if item.about_subject_ids and subject_id not in item.about_subject_ids:
                        raise SemanticValidationError(f"{ref!r} is not about subject {subject_id!r}")
                    if field_name != "alternative_item_refs" and not semantic_item_has_source_ancestry(ref, by_ref):
                        raise SemanticValidationError(f"{field_name} {ref!r} has no admitted source ancestry")

    updates: list[object] = []
    local_refs: dict[str, str] = {}
    used_refs: set[str] = set()
    evaluation_refs: dict[tuple[str, str], str] = {}
    evidence_count = proposition_count = hypothesis_count = uncertainty_count = 0
    for subject_id in expected_subjects:
        for violation_id in expected_violations:
            hypothesis_count += 1
            ref = _ref_name(f"evaluation_{subject_id}_{violation_id}", used_refs)
            local_refs[ref] = ref
            updates.append(AddHypothesisCommand(node_id=f"H{hypothesis_count}", local_ref=ref, statement=f"Evaluation target for {subject_id} and configured violation {violation_id}.", scope=GraphScope(scope_type=GraphScopeType.SUBJECT, subject_id=subject_id), reason="Create the deterministic evaluation target for the configured assessment pair."))
            evaluation_refs[(subject_id, violation_id)] = ref
    for item in items:
        ref = _ref_name(item.local_ref, used_refs)
        local_refs[item.local_ref] = ref
        scope = scopes[item.local_ref]
        if item.kind is SemanticItemKind.EVIDENCE_STATEMENT:
            evidence_count += 1
            updates.append(AddEvidenceCommand(node_id=f"E{evidence_count}", local_ref=ref, statement=item.statement, source_ids=item.basis_source_ids, scope=scope, reason="Record the model-declared source-backed evidence statement."))
        elif item.kind is SemanticItemKind.PROPOSITION:
            if not item.basis_item_refs or any(value not in local_refs for value in item.basis_item_refs):
                raise SemanticValidationError(f"proposition {item.local_ref!r} requires earlier basis_item_refs")
            proposition_count += 1
            updates.append(AddPropositionCommand(node_id=f"P{proposition_count}", local_ref=ref, statement=item.statement, derived_from_node_ids=[local_refs[value] for value in item.basis_item_refs], scope=scope, reason="Compile the model-declared proposition from earlier semantic material."))
        elif item.kind is SemanticItemKind.HYPOTHESIS:
            hypothesis_count += 1
            updates.append(AddHypothesisCommand(node_id=f"H{hypothesis_count}", local_ref=ref, statement=item.statement, scope=scope, reason="Compile the model-declared alternative hypothesis."))
        else:
            if item.target_ref is None or item.target_ref not in local_refs:
                raise SemanticValidationError(f"uncertainty {item.local_ref!r} requires an earlier target_ref")
            uncertainty_count += 1
            updates.append(AddUncertaintyCommand(node_id=f"U{uncertainty_count}", local_ref=ref, statement=item.statement, target_node_id=local_refs[item.target_ref], scope=scope, reason="Record the model-declared unresolved uncertainty."))
    compiled_subjects: list[SubjectAssessment] = []
    for subject_id in expected_subjects:
        source_subject = by_subject[subject_id]
        compiled_violations: list[ViolationAssessment] = []
        for violation in source_subject.violation_assessments:
            target_ref = evaluation_refs[(subject_id, violation.violation_id)]
            for item_ref in violation.supporting_item_refs:
                updates.append(AddSupportCommand(source_node_id=local_refs[item_ref], target_node_id=target_ref, reason="Attach explicitly declared supporting material to the evaluation target."))
            for item_ref in violation.conflicting_item_refs:
                updates.append(AddConflictCommand(source_node_id=local_refs[item_ref], target_node_id=target_ref, reason="Attach explicitly declared conflicting material to the evaluation target."))
            compiled_violations.append(ViolationAssessment(violation_id=violation.violation_id, status=violation.status, supporting_node_ids=[local_refs[item_ref] for item_ref in violation.supporting_item_refs], conflicting_node_ids=[local_refs[item_ref] for item_ref in violation.conflicting_item_refs], mitigating_node_ids=[local_refs[item_ref] for item_ref in violation.limiting_item_refs], unresolved_points=violation.unresolved_points, reasoning_summary=violation.reasoning_summary, confidence=violation.confidence))
        alternative_refs = list(
            ref
            for violation in source_subject.violation_assessments
            for ref in violation.alternative_item_refs
        )
        alternative_refs.extend(
            item.local_ref
            for item in assessment.alternative_explanations
            if subject_id in item.about_subject_ids
        )
        alternatives = [
            AlternativeExplanation(
                statement=by_ref[item_ref].statement,
                source_node_ids=list(by_ref[item_ref].basis_source_ids),
            )
            for item_ref in alternative_refs
        ]
        compiled_subjects.append(SubjectAssessment(
            subject_id=subject_id,
            violation_assessments=compiled_violations,
            furthest_conclusion=source_subject.furthest_conclusion,
            alternative_explanations=alternatives,
        ))
    return InvestigatorAssessment(proposal=InvestigatorProposal(graph_updates=updates), subject_assessments=compiled_subjects)
