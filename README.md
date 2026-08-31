# SimplifyNext Investigator

This repository is a deterministic investigation-state kernel. It preserves evidence, provenance, claims, hypotheses, conflicts, and uncertainty as distinct concepts. No LLM or agent architecture has been selected; future AI components will operate on top of this state rather than define it.

The prototype uses typed Pydantic models and local JSON files under `data/cases/`. A minimal model-call abstraction and deterministic mock now support controlled structured experiments. No agent architecture has been selected; Gate 1 experiments will be added separately.

```bash
uv sync
uv run pytest
```
