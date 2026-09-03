from enum import Enum
from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator

from investigator.graph import EdgeStrength, GraphScope


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


class AddEvidenceCommand(_InvestigatorCommand):
    operation: Literal["add_evidence"] = "add_evidence"
    node_id: str | None = Field(default=None, pattern=r"^(?:E\d+(?:\.\d+)*|A\d+_RELEASE)$")
    local_ref: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    statement: str
    source_ids: list[str] = Field(min_length=1)
    scope: GraphScope | None = None

    @field_validator("source_ids")
    @classmethod
    def source_ids_are_unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("source_ids must not contain duplicates")
        if any(not item or not item.startswith("S") for item in value):
            raise ValueError("source_ids must identify raw SourceRegistry records")
        return value


class AddPropositionCommand(_InvestigatorCommand):
    operation: Literal["add_proposition"] = "add_proposition"
    node_id: str | None = Field(default=None, pattern=r"^P\d+(?:\.\d+)*$")
    local_ref: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    statement: str
    derived_from_node_ids: list[str] = Field(min_length=1)
    scope: GraphScope | None = None

    @model_validator(mode="after")
    def sources_are_unique(self) -> "AddPropositionCommand":
        if len(self.derived_from_node_ids) != len(set(self.derived_from_node_ids)):
            raise ValueError("derived_from_node_ids must not contain duplicates")
        return self


class AddHypothesisCommand(_InvestigatorCommand):
    operation: Literal["add_hypothesis"] = "add_hypothesis"
    node_id: str | None = Field(default=None, pattern=r"^H\d+(?:\.\d+)*$")
    local_ref: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    statement: str
    scope: GraphScope | None = None


class AddUncertaintyCommand(_InvestigatorCommand):
    operation: Literal["add_uncertainty"] = "add_uncertainty"
    node_id: str | None = Field(default=None, pattern=r"^U\d+(?:\.\d+)*$")
    local_ref: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    statement: str
    target_node_id: str = Field(pattern=r"^(?:E\d+(?:\.\d+)*|A\d+_RELEASE|P\d+(?:\.\d+)*|H\d+(?:\.\d+)*|node_[0-9a-f]{12,64}|[a-z][a-z0-9_-]{0,63})$")
    scope: GraphScope | None = None


class _RelationCommand(_InvestigatorCommand):
    source_node_id: str = Field(pattern=r"^(?:E\d+(?:\.\d+)*|A\d+_RELEASE|P\d+(?:\.\d+)*|node_[0-9a-f]{12,64}|[a-z][a-z0-9_-]{0,63})$")
    target_node_id: str = Field(pattern=r"^(?:P\d+(?:\.\d+)*|H\d+(?:\.\d+)*|node_[0-9a-f]{12,64}|[a-z][a-z0-9_-]{0,63})$")
    strength: EdgeStrength | None = None


class AddSupportCommand(_RelationCommand):
    operation: Literal["add_support"] = "add_support"


class AddConflictCommand(_RelationCommand):
    operation: Literal["add_conflict"] = "add_conflict"


class AddDerivationCommand(_InvestigatorCommand):
    operation: Literal["add_derivation"] = "add_derivation"
    derived_proposition_id: str = Field(pattern=r"^(?:P\d+(?:\.\d+)*|node_[0-9a-f]{12,64}|[a-z][a-z0-9_-]{0,63})$")
    source_node_id: str = Field(pattern=r"^(?:E\d+(?:\.\d+)*|A\d+_RELEASE|P\d+(?:\.\d+)*|node_[0-9a-f]{12,64}|[a-z][a-z0-9_-]{0,63})$")


class AddSpecializationCommand(_InvestigatorCommand):
    operation: Literal["add_specialization"] = "add_specialization"
    child_hypothesis_id: str = Field(pattern=r"^(?:H\d+(?:\.\d+)*|node_[0-9a-f]{12,64}|[a-z][a-z0-9_-]{0,63})$")
    parent_hypothesis_id: str = Field(pattern=r"^(?:H\d+(?:\.\d+)*|node_[0-9a-f]{12,64}|[a-z][a-z0-9_-]{0,63})$")


class MoveFocusCommand(_InvestigatorCommand):
    operation: Literal["move_focus"] = "move_focus"
    focus_node_id: str


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
