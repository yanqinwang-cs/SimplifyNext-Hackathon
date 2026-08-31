# SimplifyNext Investigator

This repository is a deterministic investigation-state kernel. It preserves evidence, provenance, claims, hypotheses, conflicts, and uncertainty as distinct concepts. No LLM or agent architecture has been selected; future AI components will operate on top of this state rather than define it.

The prototype uses typed Pydantic models and local JSON files under `data/cases/`. A minimal model-call abstraction and deterministic mock now support controlled structured experiments. No agent architecture has been selected; Gate 1 experiments will be added separately.

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

The UI-only Streamlit prototype can be launched with `uv run streamlit run app/streamlit_app.py`. Its chat and review controls use session-state placeholders only; reasoning integration will come later.

The reusable `InvestigationSession` / `InvestigationService` cycle is: start → action review → execute → revision review → apply → next action → repeat. Streamlit provides the human review points; the controlled CLI runner auto-approves its first cycle. Free-form steering remains unconnected, and LangGraph is not required.

The state kernel also contains a provisional hypothesis tree. Broad parent hypotheses and narrower evidence-based children are represented structurally; removing or weakening a child leaves its parent unchanged. Hypotheses are never evidence, and `specificity_basis` records evidence IDs that supposedly justify narrowing. This is deterministic state representation, not a graph or search algorithm; semantic adequacy of the basis is deferred to later experiments.

Interactive runs are written incrementally to `runs/<session_id>/trace.json`, preserving raw model output, human decisions, and a current `CaseState` snapshot. These local runtime traces are gitignored; resume/recovery is not implemented yet.

`CaseGraph` in `src/investigator/graph/` is a parallel semantic representation of evidence, propositions, hypotheses, and uncertainties. It is not workflow chronology: the trace remains the audit/event record, and `CaseState` remains authoritative for runtime behavior. Graph edges use explicit directions for supports, conflicts, specializes (child to parent), depends-on, targets, and derived-from; qualitative strength is descriptive only, not probability.

Graph IDs use typed namespaces (`E...` or `A..._RELEASE` for evidence, `P...`, `H...`, and `U...` or `H...:U...` for uncertainties). `SPECIALIZES` permits one active parent and many children, rejects cycles, and ignores archived parent edges in active traversal. The persistent graph retains archived nodes/edges; active views exclude them.

The investigator seeds H1 and may pause, stop, correct evidence, or correct interpretation at any UI boundary. Ordinary bounded enquiries and routine revisions proceed autonomously; new hypotheses are reported, while hypothesis removal, conclusions, and unresolved stopping remain human decisions. Initial alternatives declare `competing_root` or `specialization`; specializations require released evidence.

The provisional Case Steward foundation adds typed investigator/steward authority boundaries, deterministic graph-health features, snapshots, and an offline sequential coordinator. It does not add a live steward model, agent loop, or new agent architecture.
