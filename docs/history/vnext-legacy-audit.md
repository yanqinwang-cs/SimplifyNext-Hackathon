# vNext legacy-feature audit

Audit date: 2026-09-03
Branch: `codex/investigator-one-turn-screen-goal`
Starting HEAD: `51ee577864e7f8aaaa4b939daa5f3fa47feb65be`

This is a reachability audit, not a deletion plan. The labels used below are:

- `VNEXT_REQUIRED` — needed by the current default vNext production path.
- `LEGACY_ISOLATED` — retained compatibility, experiments, or tests that are not on the default vNext path.
- `LEGACY_PRODUCT_LEAK` — visible or callable during normal product use and capable of confusing users or changing behavior.
- `UNCLEAR` — ownership or reachability needs a follow-up decision.

## Executive summary

The default HTTP server is vNext, and its run path is finite: it builds input
from persistent case sources, makes one Investigator assessment (with at most
one proposal-only corrective retry), applies the shared Graph Warden, persists
the result, and ends. It does not instantiate Steward or resume an unfinished
reasoning graph.

The main remaining product leaks are the Workspace surface and its frontend:
they still expose `RUN_INVESTIGATION`, pause/resume, Steward-review, evidence
request fulfilment, current focus, legacy runtime statuses, and the singular
`vnext_furthest_conclusion` display. These are not used by the core vNext
assessment algorithm, but several are reachable through the default API.

The legacy cycle, Steward, focus/archive, and request-driven evidence machinery
can remain for compatibility and tests, but should not be presented as the
default vNext workflow. No code was deleted or changed by this audit.

## Default vNext production path

The default server path is:

`src/investigator/http_api.py:create_server()`
→ `VNextProductionRunner.run()`
→ `run_input_from_case_state()` / `VNextRunInput.from_case_state()`
→ `VNextInvestigatorModel.__call__()` and `build_prompt()`
→ `VNextInvestigationRunner.run()`
→ `GraphWarden.apply()`
→ `VNextProductionRunner._persist_success()`
→ `HumanEvidenceWorkflow` run files and `CaseRepository` state.

`create_server()` selects `vnext` when no explicit mode is supplied. It uses
`SIMPLIFYNEXT_RUN_MODE` when present; allowed values are `vnext` and `legacy`.
Passing a `run_callback` without an explicit mode defaults to `legacy`. The
CLI `main()` supplies no callback, so it is vNext by default. In vNext mode,
the API run endpoint starts `VNextProductionRunner().run`; in legacy mode it
uses `default_production_run`.

`VNextInvestigationRunner` starts from `clean_reasoning_graph()` built from
current `CaseState.sources`. It does not read the persisted reasoning graph
as assessment input. The production adapter permits one additional call only
to repair deterministic proposal errors; it does not resume a legacy cycle.

## HTTP endpoint audit

| Route | Handler | Writes state? | Reachable from current frontend? | Classification |
|---|---|---:|---:|---|
| `GET /api/cases/{id}/workspace` | `InvestigatorApiHandler.do_GET` | No, except create-on-missing through `ensure_case` | Yes | `LEGACY_PRODUCT_LEAK` — response includes focus, requests, legacy statuses, and runs |
| `POST /api/cases/{id}/run` | `do_POST` → `HumanEvidenceWorkflow.start_run` | Yes | Yes | `VNEXT_REQUIRED` in default mode; legacy fallback remains `LEGACY_PRODUCT_LEAK` |
| `POST /api/cases/{id}/workspace/chat` | `do_POST` → `WorkspaceAgent.chat` | Yes for chat/operational actions; model tool actions can add sources, run, pause, fulfil, reset, or request Steward review | Yes | `LEGACY_PRODUCT_LEAK` |
| `GET /api/cases/{id}/traces` | `do_GET` | No | Service exists; not directly used by current page | `LEGACY_ISOLATED` / debug history |
| `GET /api/cases/{id}/runs` | `do_GET` | No | Yes indirectly through workspace response | `VNEXT_REQUIRED` for run audit, but legacy fields leak |
| `GET /api/cases/{id}/runs/{run}/raw-traces` | `do_GET` | No | Yes through debug links | `LEGACY_ISOLATED` debug trace |
| `POST /api/cases/{id}/evidence-requests/{request}/fulfil` | `do_POST` → `HumanEvidenceWorkflow.respond` | Yes: registers raw Source and resolves request; may restart configured run | Yes | `LEGACY_PRODUCT_LEAK` |
| `POST /api/cases/{id}/evidence-requests/{request}/unavailable` | `do_POST` → `respond` | Yes: resolves request and may restart configured run | Yes | `LEGACY_PRODUCT_LEAK` |
| `GET /api/debug/aws-credentials/status` | `do_GET` | No | Yes through frontend service, if enabled | `LEGACY_PRODUCT_LEAK` when enabled; otherwise isolated |
| `POST /api/debug/aws-credentials` | `do_POST` | Process-local credential override | Yes through frontend service, if enabled | `LEGACY_PRODUCT_LEAK` when enabled; debug-only otherwise |
| `DELETE /api/debug/aws-credentials` | `do_DELETE` | Clears process-local override | Yes through frontend service, if enabled | `LEGACY_PRODUCT_LEAK` when enabled; debug-only otherwise |

There is no direct HTTP source-creation endpoint. Source creation is exposed
through the Workspace `ADD_SOURCE` tool and through evidence-request fulfilment.

## Frontend audit

`frontend/app/page.tsx` is the current page and calls
`frontend/lib/case-service.ts`. It visibly handles:

| Feature | Classification | Finding |
|---|---|---|
| Run investigation / run again | `LEGACY_PRODUCT_LEAK` | Calls the shared `/run` endpoint; this is valid for vNext but does not distinguish vNext from legacy mode. |
| `WAITING_FOR_EVIDENCE` | `LEGACY_PRODUCT_LEAK` | Copy says “Investigator needs information from you” and presents request actions. |
| Provide evidence / Mark unavailable | `LEGACY_PRODUCT_LEAK` | Request cards are tied to EvidenceRequest IDs and the legacy request lifecycle. |
| `RUNNING_STEWARD` / “Case Steward is running” | `LEGACY_PRODUCT_LEAK` | Still rendered even though default vNext does not call Steward. |
| `RUNNING_INVESTIGATOR`, `STOPPED`, `PAUSED` | `LEGACY_PRODUCT_LEAK` | Legacy runtime vocabulary remains in the public frontend contract and copy. |
| Current focus | `LEGACY_PRODUCT_LEAK` | Workspace payload and page present focus, which is not a vNext control concept. |
| Investigation history and request history | `LEGACY_PRODUCT_LEAK` | Displays old request lifecycle alongside vNext run history. |
| Debug trace links | `LEGACY_ISOLATED` | Appropriate for an explicit debug surface, but currently linked from the normal page. |
| AWS credential controls | `LEGACY_PRODUCT_LEAK` when enabled | Debug credential API is callable by the frontend; it should remain operator-only. |
| `frontend/lib/mock-case.ts` | `LEGACY_ISOLATED` | Static prototype data; current page uses API service calls instead. |
| `app/streamlit_app.py` and `app/case_service.py` | `LEGACY_ISOLATED` | Separate prototype surface, not the Next.js/API default. |

The frontend is hardcoded to `getWorkspace("case-01")`; this is a prototype
limitation, not itself a legacy feature. It must be removed before multi-case
or five-subject product use.

## Runtime status reachability

| Status | Emitted by vNext? | Legacy path? | Frontend handles it? | Persistence | Classification |
|---|---:|---:|---:|---:|---|
| `IDLE` | No terminal vNext run uses it; initial/default state only | Yes | Yes | Yes | `LEGACY_PRODUCT_LEAK` in the shared UI contract; otherwise `VNEXT_REQUIRED` as initial state |
| `RUNNING` | Yes, during default vNext run | No | Yes | Yes | `VNEXT_REQUIRED` |
| `COMPLETED` | Yes, after vNext persistence | No | Yes | Yes | `VNEXT_REQUIRED` |
| `FAILED` | Yes | Yes | Yes | Yes | `VNEXT_REQUIRED` |
| `RUNNING_INVESTIGATOR` | No in vNext; legacy start status | Yes | Yes | Yes | `LEGACY_PRODUCT_LEAK` |
| `RUNNING_STEWARD` | No in vNext | Yes | Yes | Yes | `LEGACY_PRODUCT_LEAK` |
| `WAITING_FOR_EVIDENCE` | No vNext run waits; request service can set it | Yes | Yes | Yes | `LEGACY_PRODUCT_LEAK` |
| `STOPPED` | No | Yes | Yes | Yes | `LEGACY_PRODUCT_LEAK` |
| `PAUSED` | No | Yes | Yes | Yes | `LEGACY_PRODUCT_LEAK` |

`CaseState.runtime_status` is an unconstrained string, so persistence can store
all of these values. A normal vNext API call cannot enter Steward, paused,
stopped, or waiting-for-evidence states unless a separate Workspace/request
action or legacy mode is used.

## Steward and legacy Investigator reachability

`src/investigator/roles/steward.py`, `src/investigator/cycle.py`,
`src/investigator/roles/focus.py`, and the Steward portions of
`src/investigator/roles/coordinator.py` are `LEGACY_ISOLATED` for the core
vNext runner. They remain imported by legacy cycle/coordinator code and are
covered by legacy tests.

The default vNext path imports the coordinator only as the graph mutation
adapter used inside `GraphWarden`; it does not call `review_with_steward`,
Steward decisions, focus movement, archive/reactivate, or cycle transitions.
`WorkspaceAgent` can nevertheless expose `REQUEST_STEWARD_REVIEW`,
`PAUSE_INVESTIGATION`, `RESUME_INVESTIGATION`, recovery/reset, and request
actions through model-selected tools. Therefore those operational entry points
are `LEGACY_PRODUCT_LEAK`, not unreachable code.

Legacy Investigator concepts (`move_focus`, `continue_local`,
`local_exhausted`, `request_information`, `request_evidence`, `request_open`,
`request_verify`, `request_clarify`, `request_compute`, and resume semantics)
remain in shared schemas/prompts for the cycle and compatibility paths. The
vNext Investigator schema is separate and exposes only typed graph proposals
and per-subject assessment output; its prompt explicitly forbids requesting
human input or follow-up questions. Shared low-level graph commands used by
vNext are not themselves legacy leaks.

## EvidenceRequest system

`EvidenceRequest` and `HumanEvidenceWorkflow` are `LEGACY_PRODUCT_LEAK` when
used through the default Workspace/API surface, and `LEGACY_ISOLATED` when
used only by cycle compatibility tests. The current vNext Investigator never
creates or waits on an EvidenceRequest.

The legacy path allocates application-owned request IDs, enforces one pending
request, stores request history outside the graph, and sets
`WAITING_FOR_EVIDENCE`. Fulfilment calls `SourceRegistry.register_raw_source`
and adds a raw Source to `CaseState`; it does not automatically add semantic
graph evidence. Both fulfilment and unavailable responses set the workflow to
`IDLE` and invoke `resume_callback`, which is legacy resume behavior. This is
exactly the mutation path the future direct evidence UI should avoid inheriting.

## Source-ingestion paths

| Caller/path | Source ID allocation | Metadata/scope | Revision/provenance | vNext-safe? |
|---|---|---|---|---|
| `CaseService.add_source()` | Caller supplies Source ID | Preserves full `Source.metadata`; no scope-specific enforcement | `_mutate` increments revision and saves; no source provenance policy beyond model validation | `UNCLEAR` — reusable service, but no HTTP contract and no explicit subject-scope policy |
| `WorkspaceAgent._action_tool(ADD_SOURCE)` | `SourceRegistry.register_raw_source()` allocates next `S#` | Preserves supplied metadata; no automatic assessment scope | Saves CaseState; reachable through Workspace model tool | `LEGACY_PRODUCT_LEAK` |
| Evidence fulfilment `HumanEvidenceWorkflow.respond()` | `SourceRegistry.register_raw_source()` allocates next `S#` | Preserves supplied metadata; no automatic scope | Increments revision, resolves request, saves, may resume legacy run | `LEGACY_PRODUCT_LEAK` |
| `VNextRunInput.from_case_state()` | None; reads existing `CaseState.sources` | Preserves source records; graph starts clean | Input validation checks source keys and identity references | `VNEXT_REQUIRED` |
| `clean_reasoning_graph()` | None; creates graph SOURCE nodes from sources | Copies `source_type`, readability, and any existing `assessment_scope` metadata | Does not persist graph by itself | `VNEXT_REQUIRED` |
| `app/case_service.py` prototype | Static/mock data | UI-only state | No production repository | `LEGACY_ISOLATED` |

`SourceRegistry` is also used by the legacy fulfilment and Workspace paths.
Its current source metadata is generic; there is no documented, enforced
subject/relationship scope convention for newly ingested sources. The vNext
scope contract applies to semantic graph nodes and per-subject references,
not automatically to raw Source records.

## CaseState field audit

| Field/group | Classification | vNext read/write behavior |
|---|---|---|
| `case_id`, `title`, `description` | `VNEXT_REQUIRED` | Case identity and prompt context. |
| `assessment_context`, `subjects`, `subject_relationships` | `VNEXT_REQUIRED` | Read to build validated `VNextRunInput`; persisted as case inputs. |
| `sources` | `VNEXT_REQUIRED` | Clean vNext graph and prompt input. |
| `assessment_rule_preset_id` | `VNEXT_REQUIRED` | Used by preset resolution. |
| `evidence`, `entities`, `claims`, `hypotheses`, `transformations`, `uncertainties`, `conflicts` | `LEGACY_ISOLATED` | Legacy deterministic case kernel fields; not used to construct vNext assessment input. |
| `evidence_correction_history` | `LEGACY_ISOLATED` | Legacy correction history; vNext uses run traces/results. |
| `evidence_request_history` | `LEGACY_PRODUCT_LEAK` | Not read by vNext assessment, but start/run blocking, workspace output, and request endpoints expose/mutate it. |
| `case_status` | `VNEXT_REQUIRED` | Start-run gate and workspace status; currently shared with legacy stop/inactive semantics. |
| `runtime_status`, `current_actor`, `last_error`, `last_trace_step` | `VNEXT_REQUIRED` for audit/runtime; `LEGACY_PRODUCT_LEAK` for obsolete values | vNext writes `RUNNING`, `COMPLETED`, and `FAILED`; shared service can write legacy statuses. |
| `trace_history` | `VNEXT_REQUIRED` for audit | vNext run events and failures are appended; legacy events may coexist. |
| `reasoning_graph` | `LEGACY_PRODUCT_LEAK` / legacy runtime audit | Not read as vNext input; legacy cycle and Workspace/debug can read/write it. |
| `focus_node_id`, `focus_recent_node_ids`, `focus_recent_region_node_ids` | `LEGACY_PRODUCT_LEAK` | Not used by vNext clean assessment; exposed by Workspace and maintained by legacy cycle. |
| `clean_checkpoint` | `LEGACY_ISOLATED` | Used by legacy recovery/checkpoint behavior; not used by vNext runner. |
| `revision`, `last_updated_at` | `VNEXT_REQUIRED` | Concurrency, persistence, and run provenance. |

## Reasoning-graph persistence and contamination

The vNext runner does not accept `CaseState.reasoning_graph` as an input and
does not call `save_reasoning_state()`. `clean_reasoning_graph()` constructs a
new one-graph assessment baseline from current raw sources, so old graph nodes,
focus, archive status, and legacy local-region state cannot contaminate the
vNext proposal context through the runner.

Successful vNext results are persisted to the run directory as
`vnext_result.json` and summarized in `run_result.json`; they are not written
back into `CaseState.reasoning_graph`. Legacy Workspace/debug tools can still
read `reasoning_graph`, and legacy cycle code can write it. Presenting that
field as current reasoning would be a `LEGACY_PRODUCT_LEAK` and should be
removed or clearly labeled in a future UI pass.

## Workspace audit

Workspace is implemented by `WorkspaceAgent`, not by the vNext assessment
runner. Read tools can inspect status, summary, graph, focus, sources,
requests, runs, failures, safe state, and traces. Action tools can run/resume,
pause, add a raw source, fulfil/unavailable a request, request Steward review,
recover, and reset the demo case.

The agent is prohibited from direct semantic graph mutation tools and does not
itself create Evidence/Proposition/Hypothesis/Uncertainty nodes. It can still
mutate CaseState indirectly through `ADD_SOURCE`, request fulfilment, run
control, recovery, reset, and trace/workspace events. It can also make up to
five provider-native tool rounds per chat turn, which is a Workspace model
loop, not a vNext Investigator loop. These controls are `LEGACY_PRODUCT_LEAK`
for the intended “inputs → run → result” default product, although the
read-only portions are useful explanatory/debug infrastructure.

## Report/result display mismatch

Phase 2 result data is authoritative in `VNextRunResult.subject_assessments`
with one conclusion per subject. The production summary also stores
`vnext_subject_conclusions`, but the frontend type and page primarily display
the legacy-shaped singular `vnext_furthest_conclusion`. This is a
`LEGACY_PRODUCT_LEAK`: it can concatenate conclusions in the backend while
presenting them as one global conclusion. It should be corrected in the
future result-display pass, not by changing the vNext result model here.

## Highest-priority legacy mutation risks

| Path | Trigger | Fields mutated | Intended? | Risk |
|---|---|---|---|---|
| Default `/run` in explicit `legacy` mode | `POST /api/cases/{id}/run` or Workspace run | runtime, trace/run files, legacy graph/request state | Only for compatibility | HIGH — environment mode can change semantics |
| Workspace `ADD_SOURCE` | Model-selected `ADD_SOURCE` tool | `sources`, optional legacy `reasoning_graph` SOURCE node, revision | Operationally intended, but not direct vNext evidence contract | HIGH |
| Evidence fulfilment | Provide evidence UI or `FULFIL_REQUEST` | `sources`, request history, revision, runtime, trace; may resume run | Intended for legacy request lifecycle | HIGH |
| Evidence unavailable | Mark unavailable UI/tool | request history, revision, runtime, trace; may resume run | Intended for legacy request lifecycle | HIGH — must not be treated as vNext evidence input without explicit design |
| Workspace pause/resume | UI/model action | runtime/current actor, events; resume starts another run | Legacy control semantics | MEDIUM |
| Workspace Steward review | UI/model action | trace/workspace events; exposes obsolete review concept | Legacy audit workflow | MEDIUM |
| Workspace reset/recovery | UI/model action | broad CaseState fields via reset, or runtime/traces via recovery | Debug/recovery only | HIGH if exposed in normal product |
| Legacy cycle/coordinator | Direct legacy service/client use | reasoning graph, focus, statuses, requests, legacy collections | Compatibility/testing | LOW from default vNext, HIGH if wired into product mode |

## User-confusion risks

| Feature | Risk | Reason |
|---|---|---|
| “Investigator needs information from you” | HIGH | Implies the default finite assessment is an autonomous request loop. |
| “Case Steward is running” | HIGH | Suggests Steward participates in default vNext execution. |
| Provide evidence / Mark unavailable cards | HIGH | Makes request-driven fulfilment appear to be the primary evidence-ingestion model. |
| Current focus and local investigation wording | MEDIUM | Carries old focus/archive mental model into a shared clean assessment. |
| Pause/resume/stopped labels | MEDIUM | Implies resumable unfinished reasoning, which vNext intentionally avoids. |
| Singular global furthest conclusion | MEDIUM | Hides the per-subject Phase 2 result structure. |
| Debug traces and AWS controls | LOW to HIGH if enabled broadly | Useful to operators, confusing or risky in a normal investigator view. |

## Frontend/backend contract mismatches

`frontend/lib/types.ts` still exposes request history, current focus, legacy
runtime statuses, `currentActor`, and singular `vnext_furthest_conclusion`.
The backend workspace response supplies those fields because it still fronts
the legacy-compatible `HumanEvidenceWorkflow`. They are compatibility fields
but are also product confusion when shown by the default page. `EvidenceRequest`
uses frontend camelCase `informationSought` while the backend persistence model
uses `information_sought`; the public adapter currently bridges this shape.

## Tests and retained areas

The test suite intentionally covers Stage 1/2 request-driven workflows,
legacy Investigator cycle, Steward/procedure/focus/archive behavior, current
production runners, vNext graph contracts, identity, multi-subject assessment,
and the Workspace agent. These tests are not product leaks. The relevant
production reachability distinction is that vNext tests exercise
`VNextProductionRunner`/`VNextInvestigationRunner`, while cycle and Steward
tests exercise legacy services directly.

Safe to leave alone for now: legacy environments and fixtures, Stage 1/2
compatibility, Steward schemas/tests, focus/archive implementation, and raw
case-kernel fields. They should not be deleted until callers and historical
reproducibility requirements are intentionally retired.

## Recommended isolation sequence

1. **P0 — isolate mutation:** make the default product path unable to select
   legacy mode accidentally; keep legacy mode explicit and operator-only.
2. **P1 — hide obsolete controls:** remove request cards, Steward/pause/resume,
   focus, and legacy-status copy from the normal vNext page; retain a separate
   debug/compatibility surface.
3. **P2 — add direct source ingestion:** expose a vNext-safe source endpoint
   with explicit case revision, provenance, and future subject/relationship
   scope handling, without EvidenceRequest lifecycle semantics.
4. **P3 — build the new context/subject/relationship/evidence UI** on that
   direct contract and stop hardcoding `case-01`.
5. **P4 — run the five-subject controlled fixtures through the real vNext
   production path and verify per-subject results.**
6. **P5 — retire additional compatibility only after import/reachability and
   historical test requirements are reviewed.**

## Unresolved decisions

- Whether Workspace remains in the default product at all, or becomes an
  explicitly labeled explanatory/debug surface.
- Whether `ADD_SOURCE` should remain a Workspace action after direct ingestion
  exists.
- The canonical subject/relationship scope contract for raw Source metadata.
- Whether legacy mode should be available in the shipped server or only in
  tests/explicit operator tooling.
- How the report/frontend should render `subject_assessments` and whether the
  old singular summary field should remain as a compatibility projection.

## Validation

No repository code was changed by this audit; only this document was added.
No AWS, Bedrock, live Investigator, or Workspace model calls were made.
