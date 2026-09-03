"""Structural Investigator proposals for the future vNext path.

These models validate proposals only; they do not mutate a canonical graph.
Canonical application belongs at the future Warden boundary.
"""

from typing import Annotated, TypeAlias

from pydantic import BaseModel, ConfigDict, Field

from investigator.roles.investigator import (
    AddConflictCommand,
    AddDerivationCommand,
    AddEvidenceCommand,
    AddHypothesisCommand,
    AddPropositionCommand,
    AddSpecializationCommand,
    AddSupportCommand,
    AddUncertaintyCommand,
)


InvestigatorProposalUpdate: TypeAlias = Annotated[
    AddEvidenceCommand
    | AddPropositionCommand
    | AddHypothesisCommand
    | AddUncertaintyCommand
    | AddSupportCommand
    | AddConflictCommand
    | AddDerivationCommand
    | AddSpecializationCommand,
    Field(discriminator="operation"),
]


class InvestigatorProposal(BaseModel):
    """A graph-operation proposal, without control flow or graph mutation."""

    model_config = ConfigDict(extra="forbid")
    graph_updates: list[InvestigatorProposalUpdate] = Field(default_factory=list)
