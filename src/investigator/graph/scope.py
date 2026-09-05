"""Assessment scope carried by semantic graph nodes."""

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator


class GraphScopeType(str, Enum):
    CASE = "case"
    SUBJECT = "subject"
    RELATIONSHIP = "relationship"


class GraphScope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scope_type: GraphScopeType
    subject_id: str | None = None
    relationship_id: str | None = None
    # ``relationship_ref`` is accepted only in model-facing proposals.  The
    # Graph Warden resolves it to ``relationship_id`` before graph apply.
    relationship_ref: str | None = None

    @model_validator(mode="after")
    def validate_scope_fields(self) -> "GraphScope":
        if self.scope_type is GraphScopeType.CASE and (self.subject_id is not None or self.relationship_id is not None or self.relationship_ref is not None):
            raise ValueError("CASE graph scope cannot contain subject_id or relationship_id")
        if self.scope_type is GraphScopeType.SUBJECT and (self.subject_id is None or self.relationship_id is not None or self.relationship_ref is not None):
            raise ValueError("SUBJECT graph scope requires subject_id and no relationship_id")
        if self.scope_type is GraphScopeType.RELATIONSHIP and (self.subject_id is not None or (self.relationship_id is None and self.relationship_ref is None) or (self.relationship_id is not None and self.relationship_ref is not None)):
            raise ValueError("RELATIONSHIP graph scope requires exactly one relationship_id or relationship_ref")
        return self


def scope_metadata(scope: GraphScope) -> dict[str, Any]:
    return {"assessment_scope": scope.model_dump(mode="json")}


def node_scope(metadata: dict[str, Any]) -> GraphScope:
    raw = metadata.get("assessment_scope")
    return GraphScope.model_validate(raw) if raw is not None else GraphScope(scope_type=GraphScopeType.CASE)


def scopes_compatible(left: GraphScope, right: GraphScope, relationships: dict[str, Any]) -> bool:
    # Scope compatibility is directional: a broad CASE source may inform a
    # narrower node, but private material may not be widened back to CASE.
    # ``case_subject`` is the legacy single-subject compatibility marker used
    # only when no configured student registry is present.
    if (left.scope_type is GraphScopeType.SUBJECT and left.subject_id == "case_subject") or (right.scope_type is GraphScopeType.SUBJECT and right.subject_id == "case_subject"):
        return True
    if left.scope_type is GraphScopeType.CASE:
        return True
    if right.scope_type is GraphScopeType.CASE:
        return False
    if left.scope_type is GraphScopeType.SUBJECT and right.scope_type is GraphScopeType.SUBJECT:
        return left.subject_id == right.subject_id
    if left.scope_type is GraphScopeType.RELATIONSHIP and right.scope_type is GraphScopeType.RELATIONSHIP:
        return left.relationship_id == right.relationship_id
    if left.scope_type is GraphScopeType.SUBJECT:
        # Student-private material cannot create or widen into a relationship.
        return False
    subject = right
    relationship = left
    record = relationships.get(relationship.relationship_id)
    return record is not None and subject.subject_id in record.subject_ids


def scope_allows_subject(scope: GraphScope, subject_id: str, relationships: dict[str, Any]) -> bool:
    if scope.scope_type is GraphScopeType.CASE:
        return True
    if scope.scope_type is GraphScopeType.SUBJECT:
        return scope.subject_id == subject_id
    record = relationships.get(scope.relationship_id)
    return record is not None and subject_id in record.subject_ids
