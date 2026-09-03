"""Human-facing Workspace Agent facade over deterministic case operations."""

from __future__ import annotations

from dataclasses import dataclass, field
from copy import deepcopy
import os
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from investigator.graph import GraphNodeType
from investigator.llm import ModelClient, ModelToolUse
from investigator.services.evidence_requests import EvidenceRequestConflict, HumanEvidenceWorkflow
from investigator.sources import SourceRegistry
from investigator.model_registry import MODEL_REGISTRY
from investigator.state.case_state import CaseState


class WorkspaceChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1)


@dataclass(frozen=True)
class WorkspaceChatResponse:
    response: str
    actions: list[str] = field(default_factory=list)
    recovery: bool = False


class WorkspaceSessionStore:
    """Process-local conversational state; it is intentionally not case persistence."""

    def __init__(self) -> None:
        self._sessions: dict[str, dict[str, Any]] = {}

    def session(self, case_id: str) -> dict[str, Any]:
        return self._sessions.setdefault(case_id, {"conversation": [], "chat_history": [], "turns": []})

    def clear(self) -> None:
        self._sessions.clear()


class WorkspaceToolRequest(BaseModel):
    """Structured boundary for future tools; semantic graph writes are absent by design."""

    model_config = ConfigDict(extra="forbid")
    tool: str
    payload: dict[str, Any] = Field(default_factory=dict)


class _NoArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _ReadSourceArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str


class _RunArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str


class _EvidenceArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str
    case_revision: int | None = None
    sources: list[dict[str, Any]] = Field(default_factory=list)
    note: str | None = None


class _SourceArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)


_TOOL_ARGUMENTS: dict[str, type[BaseModel]] = {
    name: _NoArguments for name in (
        "GET_CASE_STATUS", "GET_CASE_SUMMARY", "GET_CURRENT_GRAPH", "GET_CURRENT_FOCUS",
        "LIST_SOURCES", "GET_PENDING_REQUEST", "LIST_REQUEST_HISTORY", "LIST_RUNS",
        "GET_LATEST_FAILURE", "GET_LATEST_WORKSPACE_FAILURE", "GET_LATEST_RUN_TRACE", "GET_LAST_SAFE_STATE", "OPEN_TRACE", "RUN_INVESTIGATION",
        "PAUSE_INVESTIGATION", "RESUME_INVESTIGATION", "REQUEST_STEWARD_REVIEW", "RECOVER_FROM_SAFE_STATE", "RESET_TO_CLEAN_BASELINE",
    )
}
_TOOL_ARGUMENTS.update({"READ_SOURCE": _ReadSourceArguments, "GET_RUN": _RunArguments, "FULFIL_REQUEST": _EvidenceArguments, "MARK_REQUEST_UNAVAILABLE": _EvidenceArguments, "ADD_SOURCE": _SourceArguments})
_TOOL_ARGUMENTS["GET_CASE_GUIDANCE_CONTEXT"] = _NoArguments


class WorkspaceToolAuthorizationError(PermissionError):
    pass


class WorkspaceModelUnavailable(RuntimeError):
    pass


class WorkspaceAgent:
    """Small operational assistant; it never exposes semantic graph mutation tools."""

    READ_TOOLS = frozenset({
        "GET_CASE_STATUS", "GET_CASE_SUMMARY", "GET_CURRENT_GRAPH", "GET_CURRENT_FOCUS",
        "LIST_SOURCES", "READ_SOURCE", "GET_PENDING_REQUEST", "LIST_REQUEST_HISTORY",
        "LIST_RUNS", "GET_RUN", "GET_LATEST_FAILURE", "GET_LATEST_WORKSPACE_FAILURE", "GET_LATEST_RUN_TRACE", "GET_LAST_SAFE_STATE", "OPEN_TRACE",
    })
    VNEXT_READ_TOOLS = frozenset({"GET_CASE_GUIDANCE_CONTEXT", "READ_SOURCE", "LIST_SOURCES"})
    ACTION_TOOLS = frozenset({
        "RUN_INVESTIGATION", "PAUSE_INVESTIGATION", "RESUME_INVESTIGATION", "ADD_SOURCE",
        "FULFIL_REQUEST", "MARK_REQUEST_UNAVAILABLE", "REQUEST_STEWARD_REVIEW",
        "RECOVER_FROM_SAFE_STATE", "RESET_TO_CLEAN_BASELINE",
    })
    FORBIDDEN_TOOLS = frozenset({
        "add_evidence", "add_proposition", "add_hypothesis", "add_uncertainty", "supports",
        "conflicts", "derived_from", "specializes", "depends_on", "targets", "archive", "reactivate",
    })

    def __init__(self, workflow: HumanEvidenceWorkflow, client: ModelClient | None = None, session_store: WorkspaceSessionStore | None = None) -> None:
        self.workflow = workflow
        self.client = client
        self.session_store = session_store or WorkspaceSessionStore()
        # Reuse the canonical model-screen registry; this does not make a call.
        configured_id = os.environ.get("WORKSPACE_MODEL_ID")
        default_name = "anthropic.claude-haiku-4-5" if workflow.run_mode == "vnext" else "anthropic.claude-opus-4-5"
        self.model_spec = next((spec for spec in MODEL_REGISTRY.values() if spec.invocation_id == configured_id), None) if configured_id else None
        self.model_spec = self.model_spec or MODEL_REGISTRY[default_name]
        self.is_vnext = workflow.run_mode == "vnext"

    def invoke_tool(self, case_id: str, request: WorkspaceToolRequest | dict[str, Any]) -> dict[str, Any]:
        request = request if isinstance(request, WorkspaceToolRequest) else WorkspaceToolRequest.model_validate(request)
        allowed_reads = self.VNEXT_READ_TOOLS if self.is_vnext else self.READ_TOOLS
        if request.tool in self.FORBIDDEN_TOOLS or (self.is_vnext and request.tool not in allowed_reads):
            raise WorkspaceToolAuthorizationError(f"Workspace tool is not authorized: {request.tool}")
        argument_schema = _TOOL_ARGUMENTS.get(request.tool)
        if argument_schema is None:
            raise WorkspaceToolAuthorizationError(f"Unknown or unauthorized Workspace tool: {request.tool}")
        request.payload = argument_schema.model_validate(request.payload).model_dump(exclude_none=True)
        if request.tool in allowed_reads:
            return self._read_tool(case_id, request)
        if not self.is_vnext and request.tool in self.ACTION_TOOLS:
            return self._action_tool(case_id, request)
        raise WorkspaceToolAuthorizationError(f"Unknown or unauthorized Workspace tool: {request.tool}")

    def chat(self, case_id: str, message: str) -> WorkspaceChatResponse:
        attempts = 2 if self.is_vnext else 1
        session = self.session_store.session(case_id)
        clean_conversation = deepcopy(session["conversation"])
        clean_chat_history = deepcopy(session["chat_history"])
        clean_turns = deepcopy(session["turns"])
        for attempt in range(attempts):
            if self.is_vnext:
                session["conversation"] = deepcopy(clean_conversation)
                session["chat_history"] = deepcopy(clean_chat_history)
                session["turns"] = deepcopy(clean_turns)
            try:
                return self._chat_once(case_id, message)
            except Exception:
                if attempt + 1 == attempts:
                    session["conversation"] = deepcopy(clean_conversation)
                    session["chat_history"] = deepcopy(clean_chat_history)
                    session["turns"] = deepcopy(clean_turns)
                    self._append_chat(case_id, "human", message.strip())
                    response = "Help is temporarily unavailable. The case and assessment are unaffected."
                    session["conversation"].append({"role": "user", "text": message.strip()})
                    session["conversation"].append({"role": "assistant", "text": response})
                    self._append_chat(case_id, "workspace", response)
                    return WorkspaceChatResponse(response=response)
        raise AssertionError("Workspace chat ended without a response")

    def _chat_once(self, case_id: str, message: str) -> WorkspaceChatResponse:
        text = message.strip()
        if not text:
            raise ValueError("Workspace message must not be empty")
        session = self.session_store.session(case_id)
        self._append_chat(case_id, "human", text)
        session["conversation"].append({"role": "user", "text": text})
        if self.client is None:
            response = "The Workspace assistant is currently unavailable. Case operations remain accessible through the workspace controls."
            session["conversation"].append({"role": "assistant", "text": response})
            self._append_chat(case_id, "workspace", response)
            return WorkspaceChatResponse(response=response)
        turn_id = self._begin_workspace_turn(case_id, text)
        messages: list[dict[str, Any]] = [{"role": "system", "text": self.system_prompt()}, *session["conversation"]]
        try:
            for _ in range(3 if self.is_vnext else 5):
                self._assert_tool_result_pairing(messages)
                call = self.client.call_native(messages, self.tool_specs())
                self._record_requested_tools(case_id, turn_id, call.tool_uses)
                if not call.tool_uses:
                    response = " ".join(block.text for block in call.text_blocks).strip()
                    if not response:
                        raise RuntimeError("Workspace model returned neither a response nor a tool call")
                    session["conversation"].append({"role": "assistant", "text": response})
                    self._finish_workspace_turn(case_id, turn_id, "completed", response)
                    self._append_chat(case_id, "workspace", response)
                    return WorkspaceChatResponse(response=response, recovery=self._has_recovery(case_id))
                assistant_message = {"role": "assistant", "text": " ".join(block.text for block in call.text_blocks), "tool_uses": [use.model_dump(mode="json") for use in call.tool_uses]}
                session["conversation"].append(assistant_message)
                messages.append(assistant_message)
                for tool_call in call.tool_uses:
                    try:
                        result = self.invoke_tool(case_id, {"tool": tool_call.name, "payload": tool_call.arguments})
                    except Exception as exc:
                        result = {"status": "error", "error": self._safe_tool_error(exc)}
                    tool_message = {"role": "tool", "call_id": tool_call.call_id, "result": result}
                    session["conversation"].append(tool_message)
                    messages.append(tool_message)
            raise RuntimeError("Workspace tool loop exceeded the configured safety bound")
        except WorkspaceToolAuthorizationError as exc:
            self._finish_workspace_turn(case_id, turn_id, "failed", "The requested Workspace operation is not authorized.", type(exc).__name__)
            raise
        except Exception as exc:
            if self.is_vnext:
                raise
            response = "The Workspace assistant could not respond. Case operations remain accessible through the workspace controls."
            self._finish_workspace_turn(case_id, turn_id, "failed", response, type(exc).__name__)
            self._append_chat(case_id, "workspace", response)
            self._append_chat(case_id, "system", f"Workspace model failure: {type(exc).__name__}: {exc}")
            return WorkspaceChatResponse(response=response)

    def system_prompt(self) -> str:
        tools = sorted(self.VNEXT_READ_TOOLS if self.is_vnext else self.READ_TOOLS | self.ACTION_TOOLS)
        if self.is_vnext:
            return "\n".join((
                "You are the SimplifyNext Help Assistant, a read-only human-facing guide.",
                "Explain the current case, latest assessment, evidence discipline, uncertainty, and product workflow.",
                "You may read bounded case guidance context and sources, but must never mutate the case, run an assessment, pause/resume, fulfil requests, call Steward, or make a disciplinary judgment.",
                "Never require a number of sources. Distinguish source statements from truth, similarity from collaboration, opportunity from use, association from misconduct, and absence of support from innocence.",
                f"Available read-only tools: {', '.join(tools)}. Return ordinary natural-language text only.",
            ))
        return "\n".join((
            "You are SimplifyNext Workspace Assistant, the human-facing operational interface for an academic-integrity investigation.",
            "Investigator performs local case reasoning; Steward performs global reassessment. Explain state, navigate history, mediate evidence, invoke operational tools, and explain/recover failures.",
            "You may inspect the whole case, but must not create or edit semantic Evidence, Proposition, Hypothesis, or Uncertainty nodes, determine guilt, or make a disciplinary outcome.",
            "Use a deterministic tool whenever the answer depends on current state. Do not guess status, failure cause, source inventory, pending request, safe state, or request/run linkage.",
            "Speak naturally to university investigators and hide internal graph IDs and schema jargon unless audit detail is explicitly requested.",
            f"Available Workspace tools: {', '.join(tools)}. Use provider-native tool calls for actions and ordinary natural-language text for the final answer.",
        ))

    def tool_specs(self) -> list[dict[str, Any]]:
        allowed = self.VNEXT_READ_TOOLS if self.is_vnext else self.READ_TOOLS | self.ACTION_TOOLS
        return [{"name": name, "description": f"Deterministic Workspace operation {name}.", "inputSchema": schema.model_json_schema()} for name, schema in sorted(_TOOL_ARGUMENTS.items()) if name in allowed]

    @staticmethod
    def _safe_tool_error(exc: Exception) -> str:
        summary = f"{type(exc).__name__}: {exc}"
        return re.sub(r"(?i)(access[_ -]?key|secret|session[_ -]?token|authorization|credential)(?:\s*[:=]\s*)\S+", r"\1=[REDACTED]", summary)

    @staticmethod
    def _assert_tool_result_pairing(messages: list[dict[str, Any]]) -> None:
        """Verify the provider-neutral history has no orphaned tool uses."""
        for index, message in enumerate(messages):
            uses = message.get("tool_uses", []) if message.get("role") == "assistant" else []
            for offset, use in enumerate(uses, start=1):
                result = messages[index + offset] if index + offset < len(messages) else None
                if not result or result.get("role") != "tool" or result.get("call_id") != use.get("call_id"):
                    raise RuntimeError(f"Workspace conversation has an unpaired tool use: {use.get('call_id')}")

    def _begin_workspace_turn(self, case_id: str, message: str) -> str:
        session = self.session_store.session(case_id)
        turn_id = f"workspace_{len(session['turns']) + 1:06d}"
        session["turns"].append({"workspace_turn_id": turn_id, "user_message": message, "model_status": "running", "requested_tool_names": [], "tool_execution_status": "pending"})
        return turn_id

    def _record_requested_tools(self, case_id: str, turn_id: str, uses: list[ModelToolUse]) -> None:
        record = next(item for item in reversed(self.session_store.session(case_id)["turns"]) if item["workspace_turn_id"] == turn_id)
        record["requested_tool_names"] = [use.name for use in uses]

    def _finish_workspace_turn(self, case_id: str, turn_id: str, status: str, summary: str, failure_category: str | None = None) -> None:
        record = next(item for item in reversed(self.session_store.session(case_id)["turns"]) if item["workspace_turn_id"] == turn_id)
        record.update({"model_status": status, "tool_execution_status": "completed" if status == "completed" else "failed", "failure_category": failure_category, "failure_summary": summary})

    def _read_tool(self, case_id: str, request: WorkspaceToolRequest) -> dict[str, Any]:
        state = self.workflow.ensure_case(case_id)
        workspace = self.workflow.get_workspace(case_id)
        if request.tool == "GET_CASE_STATUS":
            return {key: workspace[key] for key in ("runtimeStatus", "currentActor", "caseStatus", "caseRevision")}
        if request.tool == "GET_CASE_GUIDANCE_CONTEXT":
            return self.workflow.get_guidance_context(case_id)
        if request.tool == "GET_CASE_SUMMARY":
            nodes = state.reasoning_graph.nodes.values() if state.reasoning_graph else []
            counts = {kind.value: sum(node.node_type is kind for node in nodes) for kind in GraphNodeType}
            pending = next((item for item in reversed(state.evidence_request_history) if item.status.value == "pending"), None)
            return {"human_summary": f"The case is {workspace['institutionalStatus'].lower()} and the investigation is {workspace['runtimeStatus'].lower().replace('_', ' ')}. There are {len(state.sources)} readable sources and {len(state.trace_history)} recorded operational events." + (" One human information request is waiting for a response." if pending else " There is no pending human information request."), "status": workspace, "graph_counts": counts}
        if request.tool == "GET_CURRENT_GRAPH":
            return {"graph": state.reasoning_graph.model_dump(mode="json") if state.reasoning_graph else None}
        if request.tool == "GET_CURRENT_FOCUS":
            return {"focus": state.focus_node_id, "recent": state.focus_recent_node_ids}
        if request.tool == "LIST_SOURCES":
            return {"sources": [source.model_dump(mode="json") for source in state.sources.values()]}
        if request.tool == "READ_SOURCE":
            source = state.sources.get(str(request.payload.get("source_id")))
            if source is None:
                raise KeyError("Source not found")
            return {"source": source.model_dump(mode="json")}
        if request.tool == "GET_PENDING_REQUEST":
            return {"request": workspace.get("pendingEvidenceRequest")}
        if request.tool == "LIST_REQUEST_HISTORY":
            return {"requests": workspace.get("requestHistory", [])}
        if request.tool == "LIST_RUNS":
            return {"runs": workspace.get("runs", [])}
        if request.tool == "GET_RUN":
            run = next((item for item in workspace.get("runs", []) if item.get("run_id") == request.payload.get("run_id")), None)
            return {"run": run}
        if request.tool == "GET_LATEST_FAILURE":
            failure = next((item for item in reversed(state.trace_history) if item.get("event") in {"failed", "investigator_failed", "steward_failed"}), None)
            if failure is None:
                return {"available": False, "human_summary": "No failed run is recorded."}
            recovered = any(item.get("event") == "workspace_recovery" for item in state.trace_history)
            return {"available": True, "failure": failure, "human_summary": "The latest investigation turn failed, but no invalid semantic change was committed. " + ("The Workspace recovered from the previous safe state." if recovered else "The technical details are available in the debug trace.")}
        if request.tool == "GET_LATEST_WORKSPACE_FAILURE":
            failure = next((item for item in reversed(self.session_store.session(case_id)["turns"]) if item.get("model_status") == "failed"), None)
            return {"available": failure is not None, "failure": failure, "human_summary": "The latest Workspace turn failed; no semantic graph mutation was performed." if failure else "No failed Workspace turn is recorded."}
        if request.tool == "GET_LATEST_RUN_TRACE":
            latest = workspace.get("latestRun")
            if not latest:
                return {"available": False, "human_summary": "No investigation run is recorded."}
            return {"available": True, "run_id": latest["run_id"], "trace_path": latest.get("trace_path") or f"/api/cases/{case_id}/runs/{latest['run_id']}/raw-traces", "human_summary": "The latest run's sanitized raw trace is available at the trace link."}
        if request.tool == "GET_LAST_SAFE_STATE":
            return {"state": state.model_dump(mode="json"), "revision": state.revision, "source": "latest_committed_safe_state"}
        if request.tool == "OPEN_TRACE":
            return {"runs": workspace.get("runs", []), "traces": state.trace_history}
        raise WorkspaceToolAuthorizationError(request.tool)

    def _action_tool(self, case_id: str, request: WorkspaceToolRequest) -> dict[str, Any]:
        if request.tool in {"RUN_INVESTIGATION", "RESUME_INVESTIGATION"}:
            return self.workflow.start_run(case_id)
        if request.tool == "PAUSE_INVESTIGATION":
            self.workflow.set_runtime(case_id, "PAUSED", "NONE")
            state = self.workflow.ensure_case(case_id)
            self.workflow.record_workspace_event(case_id, {"type": "run_paused", "runtime_status": "PAUSED", "case_revision": state.revision, "human_summary": "The investigation is paused and can be resumed from the current verified state."})
            return self.workflow.get_workspace(case_id)
        if request.tool == "ADD_SOURCE":
            state = self.workflow.ensure_case(case_id)
            source = SourceRegistry.register_raw_source(state, str(request.payload.get("display_name") or "Supplied source"), str(request.payload.get("content") or ""), dict(request.payload.get("metadata") or {}))
            self.workflow.repository.save(state)
            return {"source": source.model_dump(mode="json")}
        if request.tool in {"FULFIL_REQUEST", "MARK_REQUEST_UNAVAILABLE"}:
            status = "fulfilled" if request.tool == "FULFIL_REQUEST" else "unavailable"
            result = self.workflow.respond(case_id, str(request.payload["request_id"]), {"request_id": request.payload["request_id"], "status": status, "note": request.payload.get("note")}, request.payload.get("sources", []), request.payload.get("case_revision"))
            return {"request": result.model_dump(mode="json")}
        if request.tool == "REQUEST_STEWARD_REVIEW":
            state = self.workflow.ensure_case(case_id)
            state.trace_history.append({"event": "workspace_steward_review_requested", "actor": "human"})
            self.workflow.repository.save(state)
            self.workflow.record_workspace_event(case_id, {"type": "steward_review_requested", "runtime_status": state.runtime_status, "case_revision": state.revision, "human_summary": "A Case Steward review was requested."})
            return {"status": "requested"}
        if request.tool == "RECOVER_FROM_SAFE_STATE":
            state = self.workflow.ensure_case(case_id)
            previous_revision = state.revision
            state.trace_history.append({"event": "workspace_recovery", "recovery_action": "retained_current_canonical_state"})
            state.runtime_status = "IDLE"
            state.current_actor = "NONE"
            state.last_error = None
            self.workflow.repository.save(state)
            self.workflow.record_workspace_event(case_id, {"type": "recovery_completed", "runtime_status": "IDLE", "case_revision": state.revision, "human_summary": f"Recovery succeeded at revision {state.revision}; no clean reset occurred."})
            return {"recovery": {"succeeded": True, "previous_safe_revision": previous_revision, "current_revision": state.revision, "runtime": state.runtime_status, "clean_reset": False}, "workspace": self.workflow.get_workspace(case_id)}
        if request.tool == "RESET_TO_CLEAN_BASELINE":
            from investigator.services.production_runner import reset_demo_case
            reset_demo_case(self.workflow, case_id)
            return self.workflow.get_workspace(case_id)
        raise WorkspaceToolAuthorizationError(request.tool)

    def _append_chat(self, case_id: str, role: str, text: str) -> None:
        self.session_store.session(case_id)["chat_history"].append({"role": role, "text": text})

    def _has_recovery(self, case_id: str) -> bool:
        return any(item.get("event") == "workspace_recovery" for item in self.workflow.ensure_case(case_id).trace_history)

    def chat_history(self, case_id: str) -> list[dict[str, str]]:
        return [dict(item) for item in self.session_store.session(case_id)["chat_history"]]
