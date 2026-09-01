# Stage 1 integrated screen (stage1-v2)

This experiment is a small sequential Investigator + Case Steward trajectory
harness. It uses four compact public fixtures: C1 tests a common source making a
shared signal less discriminating; C2 tests a failed specific child with a
surviving parent; C3 tests declining clarification value; and C4 tests genuine
unresolved stopping.

The public starting graphs and actions are intentionally compact: C1 reviews a
distributed practice solution; C2 verifies a named tutor session; C3 offers
clarification or timestamp review; and C4 offers archive, revision-metadata,
and comparison-source enquiries in any order. Released evidence is injected
only by the trusted deterministic environment after an action executes.
and a deterministic environment whose hidden release records are never placed
in model observations.

The existing cycle remains the single-writer state machine: the Investigator
works locally, the trusted environment releases evidence, and the Steward
manages global focus/status. The trajectory log, rather than the semantic
graph, records chronology and model outputs. Live runs are capped at three
Investigator turns per tenure, ten model calls, and twenty orchestration steps.

Each Investigator or Steward turn permits one bounded retry for invalid structured output; both attempts are recorded. A trusted, exhausted Steward may terminate with `STOP_UNRESOLVED` when consequential uncertainty remains, or `READY_FOR_HUMAN_DECISION` when no consequential investigative uncertainty requires more work. The latter is a neutral handoff and never an automated disciplinary judgment.

Validate the fixtures and prompts without AWS:

```bash
uv run python -m experiments.integrated_screen.runner --dry-run
```

The live command accepts `--investigator-model`, `--steward-model`,
`--fixtures`, and `--output-dir`. It writes immutable `manifest.json`,
`raw_traces.jsonl`, and per-fixture `trajectory_results.json` artifacts under
the selected output directory. Stage 1 does not
establish a disciplinary outcome, replace human judgement, or provide a
production orchestration architecture. Stage 1 validates integrated control
flow and trajectory behavior; it does not establish superiority over the prior
single-model architecture. That comparison belongs to Stage 2.
