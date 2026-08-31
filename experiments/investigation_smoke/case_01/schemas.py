"""Compatibility exports for the reusable investigation contracts."""

from investigator.services.contracts import (
    ControlledRunTrace,
    HypothesisProposal,
    InitialExpansionHypothesis,
    InitialExpansionResponse,
    NextStepResponse,
    InitialHypothesisProposal,
    InitialResponse,
    NewUncertainty,
    NextActionResponse,
    ReleaseRecord,
    RevisionResponse,
)

__all__ = [
    "ControlledRunTrace", "HypothesisProposal", "InitialHypothesisProposal", "InitialExpansionHypothesis", "InitialExpansionResponse", "InitialResponse", "NextStepResponse", "NewUncertainty",
    "NextActionResponse", "ReleaseRecord", "RevisionResponse",
]
