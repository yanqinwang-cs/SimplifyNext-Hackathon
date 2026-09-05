# Stage 7 acceptance audit

This audit covers the merged vNext product at base `18c6967b` and the Stage 7
acceptance additions. It establishes offline product readiness; it does not
validate live-model reasoning quality or AWS connectivity.

## Acceptance matrix

| ID | Expected behavior | Production path | Behavioral evidence | Result | Limitation |
|---|---|---|---|---|---|
| A1 | Onboarding has four slides and reaches case selection | `frontend/app/page.tsx` | Existing frontend onboarding coverage and rendered route inspection | PASS | No automated assertion covers every slide image asset |
| A2 | User cases persist with one independent Student 1 | `http_api.create_case`, `CaseRepository`, `/api/cases` | `stage7-real-flow.spec.ts` creates two cases, renames/adds/removes students, reloads workspace | PASS | Browser restart is covered by repository/unit tests rather than this run |
| A3 | Canonical samples remain read-only and retain configured students | `seed_sample_case`, `/api/samples/open` | Real browser opens Law Exam and Multi-Candidate samples; checks Candidate A and Candidates A-E | PASS | Reset-while-running is covered by backend tests |
| A4 | Uploaded text is admitted and readable without workspace content leakage | `addSource`, `add_direct_source`, public source reader | Real browser upload → HTTP persistence → live source reader; workspace response checked | PASS | PDF/other formats remain outside the documented prototype boundary |
| A5 | Zero-evidence assessment completes without a model call | `VNextProductionRunner._zero_evidence_assessment` | Real browser starts assessment; fake-provider log remains empty; report renders | PASS | No live-model conclusion quality is claimed |
| A6 | Substantive assessment uses the real bounded vNext path | `VNextProductionRunner`, `VNextInvestigationRunner`, Warden, report publication | Real browser upload → run → provider-boundary fake → report; fake log records one structured call | PASS | Provider response is controlled and schema-valid |
| A7 | Reports expose independent student findings and the human boundary | `build_report_record`, `reporting.py`, report page | Zero-evidence report renders all configured findings and states that NOT_CURRENTLY_SUPPORTED is not innocence | PASS | A full five-student report is covered by Stage 5B/6 backend tests |
| A8 | Source and run handles are opaque and cross-case/path probes fail safely | `public_views.py`, HTTP handler | Unknown case, encoded traversal, and unknown-origin browser HTTP checks | PASS | This is a local operator boundary, not authentication |
| A9 | Disallowed CORS preflight is rejected consistently | `InvestigatorApiHandler.do_OPTIONS` | Real browser negative HTTP check for DELETE preflight | PASS | Allowed-origin deployment remains loopback/local by default |
| A10 | Help is read-only and guide content is rendered as data | `WorkspaceAgent`, product guide route, Help page | Existing Help authorization/unit coverage plus real guide page coverage | PASS | Real conversational model compliance is deliberately not claimed |
| A11 | Runtime settings do not persist credentials in browser state | `runtime-settings` component and runtime API | Existing browser settings suite checks masked fields, clearing, storage redaction, and model apply/reset | PASS | The suite uses controlled API responses for the settings-only UI test |
| A12 | Historical reports retain frozen assessed content | `reporting.py`, historical source route | Stage 6 backend tests cover snapshots, pinned runs, stale metadata, and historical source reads | PASS | Browser acceptance does not mutate/delete live historical sources because no such public feature exists |

## Browser control inventory

The inspected normal controls are:

- onboarding: `Next`, `Back`, progress dots, `Skip introduction`, `Choose a case`, and `Full guide`;
- case selection: sample rows, case links, case-name input, and `Create case`;
- workspace: case-name `Edit`/`Save`/`Cancel`, `Runtime settings`, `Run assessment`, `Reset sample`, student `Rename`/`Remove`/`Add`, file input and `Add evidence`, source links, Help quick prompts, Help input and `Send`, and `View report`;
- Runtime settings: close/escape, masked credential fields, `Apply temporary credentials`, `Clear temporary credentials`, two approved model selectors, `Apply model settings`, and `Reset defaults`;
- source readers: `Back to case` and historical `Back to report`;
- report: `Back to case`, historical assessment links, source-version links, and retry/back controls on error;
- guide: `← Cases`.

Each control either navigates to a route or calls the typed functions in
`frontend/lib/case-service.ts`. The real browser suite exercises creation,
student mutation, upload, source reading, assessment start/polling, report
navigation, sample opening, and HTTP rejection through the live API.

## Provider isolation

`scripts/stage7_fake_server.py` patches only the provider boundary for an
isolated test process. The real HTTP handler, JSON repository, vNext input
construction, source-applicability checks, bounded runner, Warden, snapshot
publication, report projection, and frontend service calls remain active. The
fake records structured/native calls in a temporary repository file and has no
fallback client. `AWS_EC2_METADATA_DISABLED=true` is set for the test process.

Observed in each clean browser run:

- live AWS calls: 0;
- live model calls: 0;
- zero-evidence structured fake calls: 0;
- substantive assessment structured fake calls: 1.

The fake proves routing and orchestration wiring, not that Sonnet or Opus would
make a reasonable assessment on the actual case.

## Defects fixed during acceptance

1. Disallowed CORS preflight requests returned `204` without an explicit
   rejection. `do_OPTIONS` now returns `403`, matching normal request handling;
   the real browser negative-path test covers a DELETE preflight.
2. `uv build` failed because Hatchling saw `public_samples` both through the
   package tree and a redundant forced-include mapping. Removing that mapping
   makes the wheel and source distribution buildable and preserves both sample
   resource sets.
3. The frontend `npm run build` script now selects the validated webpack
   builder. The default Turbopack build attempted a restricted worker bind in
   the supported local environment; the webpack build is reproducible and
   passes the production build check.

## Validation and limitations

The required offline validation was run with isolated temporary repositories:

- `uv run pytest -q` — 472 passed;
- `uv sync` — passed;
- `npm ci`;
- `npm run typecheck` — passed;
- `npm run build` (`next build --webpack`) — passed;
- `npx playwright test tests/stage7-real-flow.spec.ts` — 4 passed on two clean runs;
- `uv build` plus isolated wheel install — passed; both packaged public sample manifests were importable;
- `git diff --check` and `git diff --cached --check` — passed.

Existing backend tests cover bounded corrective/fresh retry behavior, atomic
failure handling, historical reports, multi-student provenance, Help tool
permissions, runtime settings, and public-boundary contracts. This audit does
not claim that every possible loading race, provider outage, backend restart
during an in-flight run, or live model semantic outcome was exercised in a
browser. Those are explicitly offline-tested where deterministic and remain
outside the live readiness claim.

## Gate result

- offline product acceptance: PASS;
- unresolved product blockers: none observed;
- safe to request authorization for one paid Case 5A run: YES;
- paid Case 5A run: NOT EXECUTED;
- live AWS/model calls: 0;
- real-model reasoning quality: NOT VALIDATED;
- deployment assumption: loopback/local operator use with an explicit allowed-origin list, not an internet-facing authenticated service.
