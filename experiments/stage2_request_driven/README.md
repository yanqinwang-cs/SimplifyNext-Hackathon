# Stage 2B request-driven evidence release

This namespace is a deterministic offline harness for testing the human evidence-request boundary. `--dry-run` validates the frozen fixture/matcher manifest without model or AWS calls; `--scripted` exercises request, fulfilment, raw-source registration, semantic evidence creation, unavailable response, and handoff. Hidden fixture matching is audit-only and is never part of Investigator observations.

Examples:

```bash
uv run python -m experiments.stage2_request_driven.runner --dry-run
uv run python -m experiments.stage2_request_driven.runner --scripted
```

TODO: future large-case experiments may compare globally visible admitted sources with focus-sensitive review while preserving `SourceRegistry` as the canonical raw-source store. No source retrieval or ranking is implemented here.
