"""Run-local evidence-backed relationship scopes."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator

from investigator.models.assessment import SubjectRelationship


class RelationshipScopeProposal(BaseModel):
    """Model-facing relationship declaration; ``local_ref`` is never canonical."""

    model_config = ConfigDict(extra="forbid")

    local_ref: str = Field(pattern=r"^R[1-9][0-9]*$")
    student_ids: list[str] = Field(min_length=2)
    basis_source_ids: list[str] = Field(min_length=1)

    @field_validator("student_ids", "basis_source_ids")
    @classmethod
    def values_are_unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("relationship declaration values must be unique")
        return value


class RunRelationshipScope(BaseModel):
    """Internal registry entry exposed to prompt construction without its ID."""

    model_config = ConfigDict(extra="forbid")

    local_ref: str = Field(pattern=r"^R[1-9][0-9]*$")
    relationship_id: str = Field(min_length=1)
    student_ids: list[str] = Field(min_length=2)
    relationship_type: str = Field(min_length=1)
    source_ids: list[str] = Field(min_length=1)
    description: str | None = None


def deterministic_relationship_id(case_id: str, student_ids: Iterable[str]) -> str:
    participants = "\x1f".join(sorted(set(student_ids)))
    digest = hashlib.sha256(f"simplifynext:relationship:{case_id}:{participants}".encode()).hexdigest()
    return f"rel_{digest[:24]}"


def existing_relationship_scopes(
    relationships: Mapping[str, SubjectRelationship],
) -> dict[str, RunRelationshipScope]:
    return {
        f"R{index}": RunRelationshipScope(
            local_ref=f"R{index}",
            relationship_id=relationship_id,
            student_ids=list(relationship.subject_ids),
            relationship_type=relationship.relationship_type,
            source_ids=list(relationship.source_ids),
            description=relationship.description,
        )
        for index, (relationship_id, relationship) in enumerate(sorted(relationships.items()), start=1)
    }


def relationship_scope_prompt_view(scopes: Mapping[str, RunRelationshipScope]) -> dict[str, dict[str, object]]:
    """Remove canonical relationship IDs before model-facing serialization."""

    return {
        local_ref: {
            "relationship_ref": local_ref,
            "participants": list(scope.student_ids),
            "relationship_type": scope.relationship_type,
            "source_ids": list(scope.source_ids),
            "description": scope.description,
            "boundary": "provisional structural scope only; not a finding or proof of communication, collaboration, or guilt",
        }
        for local_ref, scope in sorted(scopes.items())
    }
