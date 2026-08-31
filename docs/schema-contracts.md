# Schema contract rules

LLM-facing contracts are narrower than runtime models. IDs identify and separate text explains; syntax, vocabulary, referential availability, and cross-field invariants remain distinct. Existing invalid fixtures remain invalid unless an evolution record explicitly documents an intentional reclassification.

Deterministic assurance must exercise the production boundary before state application. Canonical placeholders and `REPLACE_WITH_...` sentinels are invalid. Do not silently repair fields, enums, IDs, missing reasons, branches, or semantic text. S5 requires human semantic approval; S6 is recorded separately and is not solved with arbitrary regexes.

Every contract extension requires a concrete legitimate need, downstream operation semantics, new invalid fixtures, prompt/template alignment, and before/after regression evidence. Validator correctness and model compliance are separate metrics.
