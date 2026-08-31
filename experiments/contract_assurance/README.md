# Structured-contract assurance

This is an offline harness for the repository's LLM-facing contracts. It imports the real Pydantic schemas and uses the same JSON normalization boundary; it never calls Bedrock or mutates authoritative state. `evaluate_raw` separates serialization failures from schema/cross-field failures and always retains the raw output.

Blind packages are public-input snapshots only. A worker may be called blind only when its filesystem access is independently restricted and recorded; otherwise the batch is `NOT_BLIND` and is excluded from compliance statistics.

The stable taxonomy is documented in `docs/schema-contracts.md`. Generated outputs belong in `experiments/contract_assurance/results/` and are ignored by git.

Worker instructions live under `blind/`. The harness does not claim blindness merely because a prompt says so; `BlindBatchAudit.qualifies_as_blind` requires recorded isolation evidence.

Evolution records belong under `evolution/` and are never rewritten when a contract changes.

## Offline cycle

Run `uv run python -c 'from pathlib import Path; from experiments.contract_assurance.runner import run_deterministic; run_deterministic(Path.cwd(), Path("experiments/contract_assurance/reports"), "<git-commit>")'` to refresh deterministic inventory and reports. This makes no model or network calls. Blind producer/adversary statistics must be supplied separately with `BlindBatchAudit`; unqualified batches are reported as `NOT_BLIND` and excluded.

The same run refreshes `fixtures/` with one canonical sample and its provenance-labelled mutations for every sampled contract. These manifests are reproducible regression inputs derived from the registered schemas.
