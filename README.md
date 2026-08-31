# SimplifyNext Investigator

This repository is a deterministic investigation-state kernel. It preserves evidence, provenance, claims, hypotheses, conflicts, and uncertainty as distinct concepts. No LLM or agent architecture has been selected; future AI components will operate on top of this state rather than define it.

The prototype uses typed Pydantic models and local JSON files under `data/cases/`.

```bash
uv sync
uv run pytest
```

