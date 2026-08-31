# Controlled Case 01 investigation smoke test

This experiment is a two-call, one-release smoke test using the existing closed-book examination case. The first call produces broad hypotheses and selects exactly one fixed enquiry (A1–A4). The harness releases only that enquiry’s synthetic artefact, then a second call revises the hypothesis tree once. No third enquiry, automatic scoring, hidden builder truth, or autonomous loop is included.

The fixed catalogue is:

- A1: verify tutoring claim → `A1_tutoring_verification_packet.md`
- A2: review behaviour and question timing → `A2_exam_event_log.xlsx`
- A3: compare with prior observed behaviour → `A3_prior_behaviour_statements.md`
- A4: targeted oral explanation → `A4_student_interview_record.md`

The original evidence IDs remain `E1`–`E7`. A released artefact is represented as one observation with an immutable ID such as `A2_RELEASE`; prior hypotheses are serialized separately and are never evidence. Narrower children must cite `specificity_basis_evidence_ids`, while broad parents remain available if a child is weakened or removed. Assessment context defines policy and resource capabilities; it is not evidence that a resource was used. Repeated investigation may cause hypothesis/evidence references to accumulate, duplicate, or become stale; evaluate explicit links, maintenance, pruning, archival, provenance-preserving summaries, or active-context selection only if later gates demonstrate that failure. The experiment records reasoning quality and revision discipline, not whether a model reaches one privileged hidden conclusion.

Manual runs (one model at a time):

```bash
uv run python experiments/investigation_smoke/case_01/runner.py --model zai.glm-4.7-flash
uv run python experiments/investigation_smoke/case_01/runner.py --model us.amazon.nova-2-lite-v1:0
```

Results are written to timestamped directories under `experiments/investigation_smoke/results/`. Review manually using the qualitative labels Good / Weak / Fail for:

- Initial state: broad, grounded, appropriately uncertain, and competing explanations preserved.
- Enquiry: targets consequential uncertainty and can discriminate between explanations rather than “prove” a preferred one.
- Revision: incorporates the released artefact, narrows only when justified, preserves viable parents, and avoids treating prior hypotheses as evidence.

The artefacts are synthetic and the builder truth is intentionally not encoded as a required final answer.
