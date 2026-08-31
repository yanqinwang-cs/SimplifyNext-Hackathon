# SimplifyNext Investigator

This repository is a deterministic investigation-state kernel. It preserves evidence, provenance, claims, hypotheses, conflicts, and uncertainty as distinct concepts. No LLM or agent architecture has been selected; future AI components will operate on top of this state rather than define it.

The prototype uses typed Pydantic models and local JSON files under `data/cases/`. A minimal model-call abstraction and deterministic mock now support controlled structured experiments. No agent architecture has been selected; Gate 1 experiments will be added separately.

```bash
uv sync
uv run pytest
```

With AWS environment variables and `BEDROCK_MODEL_ID` configured, run one live smoke call with:

```bash
uv run python scripts/smoke_bedrock.py
```

The state kernel also contains a provisional hypothesis tree. Broad parent hypotheses and narrower evidence-based children are represented structurally; removing or weakening a child leaves its parent unchanged. Hypotheses are never evidence, and `specificity_basis` records evidence IDs that supposedly justify narrowing. This is deterministic state representation, not a graph or search algorithm; semantic adequacy of the basis is deferred to later experiments.
