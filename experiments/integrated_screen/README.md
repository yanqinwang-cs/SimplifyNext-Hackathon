# Stage 1 integrated screen

This experiment is a small sequential Investigator + Case Steward trajectory
harness. It uses four compact public fixtures (C1 useful release, C2 specific
child failure, C3 declining clarification value, and C4 unresolved stopping)
and a deterministic environment whose hidden release records are never placed
in model observations.

The existing cycle remains the single-writer state machine: the Investigator
works locally, the trusted environment releases evidence, and the Steward
manages global focus/status. The trajectory log, rather than the semantic
graph, records chronology and model outputs. Live runs are capped at three
Investigator turns per tenure, ten model calls, and twenty orchestration steps.

Validate the fixtures and prompts without AWS:

```bash
uv run python -m experiments.integrated_screen.runner --dry-run
```

The live command accepts `--investigator-model`, `--steward-model`,
`--fixtures`, and `--output-dir`. It writes immutable `manifest.json`,
`raw_traces.jsonl`, and per-fixture `trajectory_results.json` artifacts under
the selected output directory. Stage 1 does not
establish a disciplinary outcome, replace human judgement, or provide a
production orchestration architecture.
