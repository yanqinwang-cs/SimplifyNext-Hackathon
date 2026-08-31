# Structured-contract assurance

This is an offline harness for the repository's LLM-facing contracts. It imports the real Pydantic schemas and uses the same JSON normalization boundary; it never calls Bedrock or mutates authoritative state. `evaluate_raw` separates serialization failures from schema/cross-field failures and always retains the raw output.

Blind packages are public-input snapshots only. A worker may be called blind only when its filesystem access is independently restricted and recorded; otherwise the batch is `NOT_BLIND` and is excluded from compliance statistics.

The stable taxonomy is documented in `docs/schema-contracts.md`. Generated outputs belong in `experiments/contract_assurance/results/` and are ignored by git.
