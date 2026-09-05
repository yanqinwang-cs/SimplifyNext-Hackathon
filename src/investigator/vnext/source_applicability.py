"""Deterministic, internal source applicability for multi-student runs.

This module never creates identities.  It only recognizes identifiers already
present in the configured student registry and records the conservative result
for the current run.
"""

from __future__ import annotations

from enum import Enum
import unicodedata
from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator

from investigator.graph import GraphScope, GraphScopeType
from investigator.models.assessment import AssessmentSubject, SubjectRelationship
from investigator.models.source import Source


class SourceApplicabilityClassification(str, Enum):
    CASE_SHARED = "case_shared"
    STUDENT_SPECIFIC = "student_specific"
    MULTI_STUDENT_CANDIDATE = "multi_student_candidate"
    SINGLE_STUDENT_DEFAULT = "single_student_default"


class SourceApplicability(BaseModel):
    """Internal run input describing how one admitted source may be used."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str = Field(min_length=1)
    matched_student_ids: list[str] = Field(default_factory=list)
    identifier_mentions: list[str] = Field(default_factory=list)
    classification: SourceApplicabilityClassification
    basis: str = Field(pattern=r"^(?:trusted_internal_scope|exact_identifier_match|single_student_default|no_identifier_match)$")
    trusted_scope: GraphScope | None = None
    permitted_subject_ids: list[str] = Field(default_factory=list)
    permitted_relationship_ids: list[str] = Field(default_factory=list)
    case_shared_allowed: bool = False

    @model_validator(mode="after")
    def matched_ids_are_unique(self) -> "SourceApplicability":
        if len(self.matched_student_ids) != len(set(self.matched_student_ids)):
            raise ValueError("matched_student_ids must be unique")
        return self


def normalize_identifier(value: str) -> str:
    """Normalize configured identifiers and source text without fuzzy matching."""

    normalized = unicodedata.normalize("NFKC", value).casefold()
    # Treat ordinary filename separators and punctuation as token boundaries.
    normalized = "".join(char if char.isalnum() else " " for char in normalized)
    return " ".join(normalized.split())


def _contains_identifier(haystack: str, identifier: str) -> bool:
    needle = tuple(normalize_identifier(identifier).split())
    if not needle:
        return False
    tokens = tuple(normalize_identifier(haystack).split())
    if len(needle) > len(tokens):
        return False
    return any(tokens[index:index + len(needle)] == needle for index in range(len(tokens) - len(needle) + 1))


def validate_configured_identifiers(subjects: Mapping[str, AssessmentSubject]) -> None:
    """Reject ambiguous normalized identifiers before a run begins."""

    seen: dict[str, str] = {}
    for subject_id, subject in sorted(subjects.items()):
        identifiers = [subject.display_name]
        if subject.candidate_number:
            identifiers.append(subject.candidate_number)
        for identifier in identifiers:
            normalized = normalize_identifier(identifier)
            if not normalized:
                raise ValueError(f"Configured identifier for {subject_id!r} must not be empty")
            owner = seen.get(normalized)
            if owner is not None and owner != subject_id:
                raise ValueError(
                    f"Configured student identifiers are ambiguous after normalization: "
                    f"{identifier!r} is shared by {owner!r} and {subject_id!r}"
                )
            seen[normalized] = subject_id


def _trusted_scope(
    source: Source,
    subjects: Mapping[str, AssessmentSubject],
    relationships: Mapping[str, SubjectRelationship],
) -> GraphScope | None:
    raw = source.metadata.get("assessment_scope")
    if raw is None:
        return None
    scope = GraphScope.model_validate(raw)
    if scope.scope_type is GraphScopeType.SUBJECT and scope.subject_id not in subjects:
        raise ValueError(f"Trusted source scope {source.id!r} references unknown student {scope.subject_id!r}")
    if scope.scope_type is GraphScopeType.RELATIONSHIP:
        relationship = relationships.get(scope.relationship_id or "")
        if relationship is None:
            raise ValueError(f"Trusted source scope {source.id!r} references unknown relationship {scope.relationship_id!r}")
    return scope


def _classification(match_count: int, student_count: int) -> tuple[SourceApplicabilityClassification, str]:
    if student_count == 1 and match_count == 0:
        return SourceApplicabilityClassification.SINGLE_STUDENT_DEFAULT, "single_student_default"
    if match_count == 0:
        return SourceApplicabilityClassification.CASE_SHARED, "no_identifier_match"
    if match_count == 1:
        return SourceApplicabilityClassification.STUDENT_SPECIFIC, "exact_identifier_match"
    return SourceApplicabilityClassification.MULTI_STUDENT_CANDIDATE, "exact_identifier_match"


def build_source_applicability(
    sources: Mapping[str, Source],
    subjects: Mapping[str, AssessmentSubject],
    relationships: Mapping[str, SubjectRelationship] | None = None,
) -> dict[str, SourceApplicability]:
    """Build a stable source index from names/content and configured IDs."""

    if any(key != source.id for key, source in sources.items()):
        raise ValueError("Source mapping keys must match source IDs")
    validate_configured_identifiers(subjects)
    relationships = relationships or {}
    result: dict[str, SourceApplicability] = {}
    for source_id, source in sorted(sources.items()):
        trusted = _trusted_scope(source, subjects, relationships)
        searchable = f"{source.name}\n{source.content or ''}"
        matched: list[str] = []
        for subject_id, subject in sorted(subjects.items()):
            identifiers = [subject.display_name]
            if subject.candidate_number:
                identifiers.append(subject.candidate_number)
            if any(_contains_identifier(searchable, identifier) for identifier in identifiers):
                matched.append(subject_id)
        classification, basis = _classification(len(matched), len(subjects))
        if trusted is not None:
            basis = "trusted_internal_scope"
            if trusted.scope_type is GraphScopeType.SUBJECT:
                classification = SourceApplicabilityClassification.STUDENT_SPECIFIC
            elif trusted.scope_type is GraphScopeType.RELATIONSHIP:
                classification = SourceApplicabilityClassification.MULTI_STUDENT_CANDIDATE
                relationship = relationships[trusted.relationship_id or ""]
                matched = sorted(set(relationship.subject_ids))
        permitted_subject_ids: list[str]
        permitted_relationship_ids: list[str] = []
        case_shared_allowed = False
        explicit_relationships = [
            relationship
            for relationship in relationships.values()
            if source_id in relationship.source_ids
        ]
        if trusted is not None:
            if trusted.scope_type is GraphScopeType.CASE:
                permitted_subject_ids = sorted(subjects)
                case_shared_allowed = True
            elif trusted.scope_type is GraphScopeType.SUBJECT:
                permitted_subject_ids = [trusted.subject_id or ""]
            else:
                relationship = relationships[trusted.relationship_id or ""]
                permitted_subject_ids = sorted(relationship.subject_ids)
                permitted_relationship_ids = [relationship.relationship_id]
        elif len(explicit_relationships) == 1:
            relationship = explicit_relationships[0]
            permitted_subject_ids = sorted(relationship.subject_ids)
            permitted_relationship_ids = [relationship.relationship_id]
        elif not matched:
            permitted_subject_ids = sorted(subjects)
            case_shared_allowed = True
        elif len(matched) == 1:
            permitted_subject_ids = list(matched)
        else:
            # Multiple textual mentions are provenance only.  Without an
            # explicit trusted relationship scope, they do not authorize the
            # source for any particular student.
            permitted_subject_ids = []
        result[source_id] = SourceApplicability(
            source_id=source_id,
            matched_student_ids=matched,
            identifier_mentions=list(matched),
            classification=classification,
            basis=basis,
            trusted_scope=trusted,
            permitted_subject_ids=permitted_subject_ids,
            permitted_relationship_ids=permitted_relationship_ids,
            case_shared_allowed=case_shared_allowed,
        )
    return result


def source_applies_to_student(
    applicability: SourceApplicability,
    subject_id: str,
    subjects: Mapping[str, AssessmentSubject],
) -> bool:
    """Return whether source material may contribute to one student's graph."""

    return subject_id in applicability.permitted_subject_ids


def source_jointly_identifies(
    applicability: SourceApplicability,
    participant_ids: set[str],
    relationships: Mapping[str, SubjectRelationship] | None = None,
) -> bool:
    """Require one source record to cover every relationship participant."""

    if not participant_ids:
        return False
    if applicability.trusted_scope is not None and applicability.trusted_scope.scope_type is GraphScopeType.RELATIONSHIP:
        if relationships is not None and applicability.trusted_scope.relationship_id in relationships:
            return participant_ids.issubset(set(relationships[applicability.trusted_scope.relationship_id].subject_ids))
        return participant_ids.issubset(set(applicability.permitted_subject_ids))
    return participant_ids.issubset(set(applicability.permitted_subject_ids)) and len(participant_ids) > 1


def source_applicability_snapshot(index: Mapping[str, SourceApplicability]) -> dict[str, dict[str, object]]:
    """Return an internal, deterministic artifact representation."""

    return {
        source_id: item.model_dump(mode="json")
        for source_id, item in sorted(index.items())
    }
