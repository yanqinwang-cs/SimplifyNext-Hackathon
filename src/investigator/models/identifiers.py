from typing import Annotated, Literal

from pydantic import StringConstraints


EvidenceId = Annotated[
    str,
    StringConstraints(pattern=r"^(?:E[1-9][0-9]*|A[1-4]_RELEASE)$"),
]
HypothesisId = Annotated[
    str,
    StringConstraints(pattern=r"^H[1-9][0-9]*(?:\.[1-9][0-9]*)*$"),
]
UncertaintyId = Annotated[
    str,
    StringConstraints(pattern=r"^U[1-9][0-9]*(?:\.[1-9][0-9]*)*$"),
]
Case1ActionId = Literal["A1", "A2", "A3", "A4"]
