# SimplifyNext Investigator

This repository is a deterministic investigation-state kernel and a bounded vNext prototype. It preserves evidence, provenance, claims, hypotheses, conflicts, and uncertainty as distinct concepts. The normal product path is a finite Investigator assessment with deterministic validation and human-facing read-only Help; it is not an autonomous multi-agent system.

The prototype uses typed Pydantic models and local JSON files under `data/cases/`. A minimal model-call abstraction and deterministic mock support controlled structured experiments; the product workflow uses a bounded Investigator path rather than an open-ended agent loop.

Structured model outputs must keep identifiers separate from explanatory text: IDs identify existing records, while prose fields explain them. Future experiment schemas should use explicit identifier types or enums, and deterministic code should validate existence and namespace boundaries.

In model-generated schemas, fields such as `id`, `parent_id`, `hypothesis_id`, `uncertainty_id`, `selected_action_id`, and fields ending in `_ids` or `_evidence_ids` contain bare machine IDs only. Fields such as `statement`, `reason`, `description`, and `rationale` contain explanatory text only.

For every future structured LLM interface, validate raw output, JSON, schema, fields, cross-field relationships, and references before applying state changes. Undefined operations do not execute; a last-resort `other` operation may report a missing operation for later design work, but it cannot mutate state.

```bash
uv sync
uv run pytest
```

With AWS environment variables and `BEDROCK_MODEL_ID` configured, run one live smoke call with:

```bash
uv run python scripts/smoke_bedrock.py
```

The original Streamlit investigation prototype remains available with `uv run streamlit run app/streamlit_app.py` for compatibility. The supported product workflow is the vNext HTTP/UI path described above.

The reusable `InvestigationSession` / `InvestigationService` cycle remains available for the earlier deterministic kernel. The vNext product path is finite and report-oriented; LangGraph and autonomous tool loops are not required for local operation.

The state kernel also contains a provisional hypothesis tree. Broad parent hypotheses and narrower evidence-based children are represented structurally; removing or weakening a child leaves its parent unchanged. Hypotheses are never evidence, and `specificity_basis` records evidence IDs that supposedly justify narrowing. This is deterministic state representation, not a graph or search algorithm; semantic adequacy of the basis is deferred to later experiments.

Interactive runs are written incrementally to `runs/<session_id>/trace.json`, preserving raw model output, human decisions, and a current `CaseState` snapshot. These local runtime traces are gitignored; resume/recovery is not implemented yet.

`CaseGraph` in `src/investigator/graph/` is a parallel semantic representation of evidence, propositions, hypotheses, and uncertainties. It is not workflow chronology: the trace remains the audit/event record, and `CaseState` remains authoritative for runtime behavior. Graph edges use explicit directions for supports, conflicts, specializes (child to parent), depends-on, targets, and derived-from; qualitative strength is descriptive only, not probability.

Graph IDs use typed namespaces (`E...` or `A..._RELEASE` for evidence, `P...`, `H...`, and global `U...` for uncertainties). Uncertainty IDs do not encode ownership; their subject is represented by a `TARGETS` edge, which may point to evidence, a proposition, or a hypothesis. `SPECIALIZES` permits one active parent and many children, rejects cycles, and ignores archived parent edges in active traversal. The persistent graph retains archived nodes/edges; active views exclude them.

The investigator seeds H1 and may pause, stop, correct evidence, or correct interpretation at any UI boundary. Ordinary bounded enquiries and routine revisions proceed autonomously; new hypotheses are reported, while hypothesis removal, conclusions, and unresolved stopping remain human decisions. Initial alternatives declare `competing_root` or `specialization`; specializations require released evidence.

The vNext product path adds explicit runtime state, multi-student source applicability, bounded graph validation, immutable historical report projections, and a local operator UI. Live provider connectivity and real-model reasoning quality are evaluated separately from the offline acceptance suite.

## Run the local vNext prototype

Start the backend in the supported local mode:

```bash
SIMPLIFYNEXT_RUN_MODE=vnext uv run python -m investigator.http_api --repository data/cases --host 127.0.0.1 --port 8000
```

Every assessment run records a sanitized execution trace. The latest run can
be downloaded directly from the case workspace, whether it completed or
failed. The trace does not expose credentials or unsanitized provider
payloads. A Bedrock read timeout is treated as a provider transport failure
and is not automatically retried as a new paid inference.

In another terminal, start the frontend:

```bash
cd frontend
npm ci
NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000 npm run dev -- --hostname 127.0.0.1 --port 3000
```

Open `http://127.0.0.1:3000`. The supported deployment boundary is a local
operator process. The API uses an explicit allowed-origin list, but that is not
authentication. Zero-evidence assessments need no provider credentials; a
substantive live assessment requires the normal AWS credential chain or the
temporary process-local credentials in Runtime settings and an approved model
selection.

The final offline acceptance and pre-live checklist are in
`docs/stage-7-acceptance-audit.md` and `docs/pre-5a-readiness.md`.
