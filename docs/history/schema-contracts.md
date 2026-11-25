# Schema contract rules

LLM-facing contracts are narrower than runtime models. IDs identify and separate text explains; syntax, vocabulary, referential availability, and cross-field invariants remain distinct. Existing invalid fixtures remain invalid unless an evolution record explicitly documents an intentional reclassification.

Deterministic assurance must exercise the production boundary before state application. Canonical placeholders and `REPLACE_WITH_...` sentinels are invalid. Do not silently repair fields, enums, IDs, missing reasons, branches, or semantic text. S5 requires human semantic approval; S6 is recorded separately and is not solved with arbitrary regexes.

## Failure taxonomy

| Code | Meaning | Boundary
| --- | --- | ---
| S0 | Serialization / transport | Empty, malformed, fenced-with-extra-material, or non-object output
| S1 | Structural schema | Missing, extra, wrongly typed, or wrongly shaped fields
| S2 | Vocabulary / namespace | Invalid literals, enums, identifiers, or ID namespaces
| S3 | Referential / availability | Unknown references, unreleased evidence, or unavailable actions
| S4 | Cross-field contract | Incompatible branches, relationships, operations, placeholders, or invariants
| S5 | Legitimate operation not representable | A real operation cannot be represented by the current contract
| S6 | Reasoning / semantic quality | Semantic quality outside deterministic structural assurance

S0–S4 are deterministic contract failures. S5 requires explicit human semantic approval before a contract extension; S6 remains a separately reported limitation.

Every contract extension requires a concrete legitimate need, downstream operation semantics, new invalid fixtures, prompt/template alignment, and before/after regression evidence. Validator correctness and model compliance are separate metrics.
