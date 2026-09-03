"""Typed identity and relationship records for an assessment world."""

from collections.abc import Collection, Mapping
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class AssessmentContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assessment_id: str = Field(min_length=1)
    title: str | None = None
    assessment_type: str | None = None
    venue: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def end_is_not_before_start(self) -> "AssessmentContext":
        if self.start_time is not None and self.end_time is not None and self.end_time < self.start_time:
            raise ValueError("Assessment end_time must be greater than or equal to start_time")
        return self


class AssessmentSubject(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject_id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    candidate_number: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SubjectRelationship(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relationship_id: str = Field(min_length=1)
    subject_ids: list[str] = Field(min_length=2)
    relationship_type: str = Field(min_length=1)
    source_ids: list[str] = Field(default_factory=list)
    description: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("subject_ids")
    @classmethod
    def subject_ids_are_distinct(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("subject_ids must not contain duplicates")
        return value

    @field_validator("source_ids")
    @classmethod
    def source_ids_are_distinct(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("source_ids must not contain duplicates")
        return value


def validate_identity_references(
    subjects: Mapping[str, AssessmentSubject],
    relationships: Mapping[str, SubjectRelationship],
    source_ids: Collection[str],
) -> None:
    """Validate identity mappings shared by CaseState and VNextRunInput."""
    if any(key != subject.subject_id for key, subject in subjects.items()):
        raise ValueError("Subject mapping keys must match subject IDs")
    if any(key != relationship.relationship_id for key, relationship in relationships.items()):
        raise ValueError("Subject relationship mapping keys must match relationship IDs")
    known_subject_ids = set(subjects)
    known_source_ids = set(source_ids)
    for relationship in relationships.values():
        for subject_id in relationship.subject_ids:
            if subject_id not in known_subject_ids:
                raise ValueError(f"Unknown subject ID in relationship: {subject_id!r}")
        for source_id in relationship.source_ids:
            if source_id not in known_source_ids:
                raise ValueError(f"Unknown source ID in subject relationship: {source_id!r}")
