# Pre-live Case 5A readiness

This is a preparation checklist only. It does not authorize or execute a live
assessment.

## Intended run

- Product sample: `Multi-Candidate Collaboration Review` (`multi-candidate-working`).
- Investigator model: logical `anthropic.claude-sonnet-4-5`.
- Bedrock invocation profile: `us.anthropic.claude-sonnet-4-5-20250929-v1:0`.
- Region: `us-east-1`.
- Bedrock transport: 10-second connect timeout and 300-second read timeout;
  a provider read timeout is a transport failure and does not trigger an
  automatic paid retry.
- Configured students: Candidates A, B, C, D, and E.
- Visible admitted evidence: the canonical 14 Case 5A files from
  `src/investigator/public_samples/multi_candidate/sources/`.
- Evaluator-only material: excluded from the sample and from Investigator,
  Help, traces, and reports.

## Operator steps

1. Start the backend in vNext mode and the frontend using the commands in the
   README.
2. Open the sample from Cases and confirm the five configured candidates and
   canonical source list.
3. Open Runtime settings and confirm the intended Investigator model. Load
   temporary AWS credentials only for the running local session if the default
   chain is not being used. Do not record or commit credential values.
4. Run exactly one assessment. Do not start a model sweep or an automatic
   repeat.
5. Preserve the run result, sanitized trace, public report, and the historical
   source-version links produced by that one run.
6. After a completed, failed, interrupted, or stopped run, use the
   handle-bound **Download trace** control in the assessment status section.
   The downloaded file is the complete sanitized JSONL run artifact; no inline
   trace panel is rendered.

## Questions to evaluate after the run

- Were all five students assessed independently for every configured violation?
- Were source statements kept distinct from established propositions and policy?
- Did any relationship or shared source improperly propagate a conclusion?
- Are supporting, limiting, and unresolved points traceable to the admitted
  source versions?
- Does the report preserve uncertainty and the final human-judgment boundary?
- Did any bounded retry occur, and was its cause recorded truthfully?

## Failure classification

- configuration/access error: provider or inference-profile setup;
- transport/API failure: AWS or model availability;
- parse/schema/Warden failure: adapter or structured-output contract;
- valid but poor reasoning: model/prompt performance;
- public leakage or wrong source version: application defect.

A provider timeout is operational transport failure, not a semantic reasoning
result. Do not silently retry a timed-out paid inference.

The demonstrated Case 5A scope failure is different from a transport failure.
An `INCOMPATIBLE_SCOPE` `DERIVED_FROM` defect that changes the provenance of a
semantic claim is classified as semantic-affecting. It receives at most one
fresh clean Investigator execution from the original admitted evidence. The
failed assessment, proposal, graph, and conclusions are not reused; only
deterministic scope constraints are supplied to the fresh prompt. If that
retry fails with another semantic-affecting defect, the run fails without a
third model call. Safe structural graph defects retain the existing single
proposal-only correction path, with no additional investigation and no loop.

This behavior does not weaken the Warden, source-applicability, supporting-
material, or cross-student validators. It does not accept private A/B evidence
as relationship provenance, change assessment statuses, or claim that the
next live Case 5A attempt will succeed. Every vNext model call now has its own
chronological start, completed/failed, raw-output, parsed-output, metadata, and
call-kind record before deterministic validation. Retry decisions, complete
Warden issues, post-apply validation failures, counters, and final results or
failures remain in the append-only trace. The full sanitized JSONL file
contains no credentials or evaluator-only material, has no arbitrary
truncation, and is available for both successful and failed runs;
raw unsanitized traces are never served.

Do not silently retry a failed run, run another model, or interpret
`NOT_CURRENTLY_SUPPORTED` as innocence. A later 5B/5C run is not implied by
this checklist.
