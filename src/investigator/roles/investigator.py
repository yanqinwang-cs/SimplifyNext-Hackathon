from enum import Enum
from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator

from investigator.graph import EdgeStrength


class InvestigatorOperation(str, Enum):
    ADD_EVIDENCE = "add_evidence"
    ADD_PROPOSITION = "add_proposition"
    ADD_HYPOTHESIS = "add_hypothesis"
    ADD_UNCERTAINTY = "add_uncertainty"
    ADD_SUPPORT = "add_support"
    ADD_CONFLICT = "add_conflict"
    ADD_DERIVATION = "add_derivation"
    ADD_SPECIALIZATION = "add_specialization"
    MOVE_FOCUS = "move_focus"


class _InvestigatorCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str

    @field_validator("reason")
    @classmethod
    def reason_must_be_concrete(cls, value: str) -> str:
        if not value.strip() or "REPLACE_WITH_" in value or "{{" in value:
            raise ValueError("reason must be a non-empty concrete audit rationale")
        return value


class AddPropositionCommand(_InvestigatorCommand):
    operation: Literal["add_proposition"] = "add_proposition"
    node_id: str | None = Field(default=None, pattern=r"^P\d+(?:\.\d+)*$")
    local_ref: str | None = Field(default=None, pattern=r"^[A-Za-z][A-Za-z0-9_]*$")
    statement: str
    derived_from_node_ids: list[str] = Field(default_factory=list)
    derived_from_node_refs: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def sources_are_unique(self) -> "AddPropositionCommand":
        if not self.derived_from_node_ids and not self.derived_from_node_refs:
            raise ValueError("a proposition needs at least one source node ID or local reference")
        values = [*self.derived_from_node_ids, *self.derived_from_node_refs]
        if len(values) != len(set(values)):
            raise ValueError("derived_from_node_ids must not contain duplicates")
        return self


class AddEvidenceCommand(_InvestigatorCommand):
    operation: Literal["add_evidence"] = "add_evidence"
    node_id: str | None = Field(default=None, pattern=r"^E\d+(?:\.\d+)*$")
    local_ref: str | None = Field(default=None, pattern=r"^[A-Za-z][A-Za-z0-9_]*$")
    statement: str
    source_id: str = Field(min_length=1)


class AddHypothesisCommand(_InvestigatorCommand):
    operation: Literal["add_hypothesis"] = "add_hypothesis"
    node_id: str | None = Field(default=None, pattern=r"^H\d+(?:\.\d+)*$")
    local_ref: str | None = Field(default=None, pattern=r"^[A-Za-z][A-Za-z0-9_]*$")
    statement: str


class AddUncertaintyCommand(_InvestigatorCommand):
    operation: Literal["add_uncertainty"] = "add_uncertainty"
    node_id: str | None = Field(default=None, pattern=r"^U\d+(?:\.\d+)*$")
    local_ref: str | None = Field(default=None, pattern=r"^[A-Za-z][A-Za-z0-9_]*$")
    statement: str
    target_node_id: str | None = Field(default=None, pattern=r"^(?:E\d+(?:\.\d+)*|A\d+_RELEASE|P\d+(?:\.\d+)*|H\d+(?:\.\d+)*)$")
    target_node_ref: str | None = Field(default=None, pattern=r"^[A-Za-z][A-Za-z0-9_]*$")

    @model_validator(mode="after")
    def target_is_identified(self) -> "AddUncertaintyCommand":
        if (self.target_node_id is None) == (self.target_node_ref is None):
            raise ValueError("an uncertainty needs exactly one target node ID or local reference")
        return self


class _RelationCommand(_InvestigatorCommand):
    source_node_id: str | None = Field(default=None, pattern=r"^(?:E\d+(?:\.\d+)*|A\d+_RELEASE|P\d+(?:\.\d+)*)$")
    target_node_id: str | None = Field(default=None, pattern=r"^(?:P\d+(?:\.\d+)*|H\d+(?:\.\d+)*)$")
    source_node_ref: str | None = Field(default=None, pattern=r"^[A-Za-z][A-Za-z0-9_]*$")
    target_node_ref: str | None = Field(default=None, pattern=r"^[A-Za-z][A-Za-z0-9_]*$")
    strength: EdgeStrength | None = None

    @model_validator(mode="after")
    def endpoints_are_identified(self) -> "_RelationCommand":
        if (self.source_node_id is None) == (self.source_node_ref is None):
            raise ValueError("a relation needs exactly one source node ID or local reference")
        if (self.target_node_id is None) == (self.target_node_ref is None):
            raise ValueError("a relation needs exactly one target node ID or local reference")
        return self


class AddSupportCommand(_RelationCommand):
    operation: Literal["add_support"] = "add_support"


class AddConflictCommand(_RelationCommand):
    operation: Literal["add_conflict"] = "add_conflict"


class AddDerivationCommand(_InvestigatorCommand):
    operation: Literal["add_derivation"] = "add_derivation"
    derived_proposition_id: str | None = Field(default=None, pattern=r"^P\d+(?:\.\d+)*$")
    source_node_id: str | None = Field(default=None, pattern=r"^(?:E\d+(?:\.\d+)*|A\d+_RELEASE|P\d+(?:\.\d+)*)$")
    derived_proposition_ref: str | None = Field(default=None, pattern=r"^[A-Za-z][A-Za-z0-9_]*$")
    source_node_ref: str | None = Field(default=None, pattern=r"^[A-Za-z][A-Za-z0-9_]*$")

    @model_validator(mode="after")
    def endpoints_are_identified(self) -> "AddDerivationCommand":
        if (self.derived_proposition_id is None) == (self.derived_proposition_ref is None):
            raise ValueError("a derivation needs exactly one proposition ID or local reference")
        if (self.source_node_id is None) == (self.source_node_ref is None):
            raise ValueError("a derivation needs exactly one source node ID or local reference")
        return self


class AddSpecializationCommand(_InvestigatorCommand):
    operation: Literal["add_specialization"] = "add_specialization"
    child_hypothesis_id: str | None = Field(default=None, pattern=r"^H\d+(?:\.\d+)*$")
    parent_hypothesis_id: str | None = Field(default=None, pattern=r"^H\d+(?:\.\d+)*$")
    child_hypothesis_ref: str | None = Field(default=None, pattern=r"^[A-Za-z][A-Za-z0-9_]*$")
    parent_hypothesis_ref: str | None = Field(default=None, pattern=r"^[A-Za-z][A-Za-z0-9_]*$")

    @model_validator(mode="after")
    def endpoints_are_identified(self) -> "AddSpecializationCommand":
        if (self.child_hypothesis_id is None) == (self.child_hypothesis_ref is None):
            raise ValueError("a specialization needs exactly one child hypothesis ID or local reference")
        if (self.parent_hypothesis_id is None) == (self.parent_hypothesis_ref is None):
            raise ValueError("a specialization needs exactly one parent hypothesis ID or local reference")
        return self


class MoveFocusCommand(_InvestigatorCommand):
    operation: Literal["move_focus"] = "move_focus"
    focus_node_id: str | None = None
    focus_node_ref: str | None = Field(default=None, pattern=r"^[A-Za-z][A-Za-z0-9_]*$")

    @model_validator(mode="after")
    def destination_is_identified(self) -> "MoveFocusCommand":
        if (self.focus_node_id is None) == (self.focus_node_ref is None):
            raise ValueError("a focus move needs exactly one focus node ID or local reference")
        return self


InvestigatorUpdate: TypeAlias = Annotated[
    AddEvidenceCommand | AddPropositionCommand | AddHypothesisCommand | AddUncertaintyCommand |
    AddSupportCommand | AddConflictCommand | AddDerivationCommand |
    AddSpecializationCommand | MoveFocusCommand,
    Field(discriminator="operation"),
]

INVESTIGATOR_UPDATE_ADAPTER = TypeAdapter(InvestigatorUpdate)


class InvestigatorUpdateResponse:
    """Schema facade backed directly by the production InvestigatorUpdate adapter."""

    model_fields: dict[str, object] = {}
    model_config = {"extra": "forbid"}
    _adapter = INVESTIGATOR_UPDATE_ADAPTER

    @classmethod
    def model_json_schema(cls) -> dict[str, object]:
        return cls._adapter.json_schema()

    @classmethod
    def model_validate(cls, value: object) -> object:
        return cls._adapter.validate_python(value)
