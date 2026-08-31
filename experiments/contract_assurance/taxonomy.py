from enum import StrEnum


class FailureCode(StrEnum):
    S0 = "S0"
    S1 = "S1"
    S2 = "S2"
    S3 = "S3"
    S4 = "S4"
    S5 = "S5"
    S6 = "S6"


DESCRIPTIONS = {
    FailureCode.S0: "serialization / transport",
    FailureCode.S1: "structural schema",
    FailureCode.S2: "vocabulary / namespace",
    FailureCode.S3: "referential / availability",
    FailureCode.S4: "cross-field contract",
    FailureCode.S5: "legitimate operation not representable",
    FailureCode.S6: "reasoning / semantic quality",
}
