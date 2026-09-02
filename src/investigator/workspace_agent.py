"""Human-facing Workspace Agent facade over deterministic case operations."""

from __future__ import annotations

from dataclasses import dataclass, field
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
        "GET_LATEST_FAILURE", "GET_LATEST_WORKSPACE_FAILURE", "GET_LAST_SAFE_STATE", "OPEN_TRACE", "RUN_INVESTIGATION",
        "PAUSE_INVESTIGATION", "RESUME_INVESTIGATION", "REQUEST_STEWARD_REVIEW", "RECOVER_FROM_SAFE_STATE", "RESET_TO_CLEAN_BASELINE",
    )
}
_TOOL_ARGUMENTS.update({"READ_SOURCE": _ReadSourceArguments, "GET_RUN": _RunArguments, "FULFIL_REQUEST": _EvidenceArguments, "MARK_REQUEST_UNAVAILABLE": _EvidenceArguments, "ADD_SOURCE": _SourceArguments})


class WorkspaceToolAuthorizationError(PermissionError):
    pass


class WorkspaceModelUnavailable(RuntimeError):
    pass


class WorkspaceAgent:
    """Small operational assistant; it never exposes semantic graph mutation tools."""

    READ_TOOLS = frozenset({
        "GET_CASE_STATUS", "GET_CASE_SUMMARY", "GET_CURRENT_GRAPH", "GET_CURRENT_FOCUS",
        "LIST_SOURCES", "READ_SOURCE", "GET_PENDING_REQUEST", "LIST_REQUEST_HISTORY",
        "LIST_RUNS", "GET_RUN", "GET_LATEST_FAILURE", "GET_LATEST_WORKSPACE_FAILURE", "GET_LAST_SAFE_STATE", "OPEN_TRACE",
    })
    ACTION_TOOLS = frozenset({
        "RUN_INVESTIGATION", "PAUSE_INVESTIGATION", "RESUME_INVESTIGATION", "ADD_SOURCE",
        "FULFIL_REQUEST", "MARK_REQUEST_UNAVAILABLE", "REQUEST_STEWARD_REVIEW",
        "RECOVER_FROM_SAFE_STATE", "RESET_TO_CLEAN_BASELINE",
    })
    FORBIDDEN_TOOLS = frozenset({
        "add_evidence", "add_proposition", "add_hypothesis", "add_uncertainty", "supports",
        "conflicts", "derived_from", "specializes", "depends_on", "targets", "archive", "reactivate",
    })

    def __init__(self, workflow: HumanEvidenceWorkflow, client: ModelClient | None = None) -> None:
        self.workflow = workflow
        self.client = client
        # Reuse the canonical model-screen registry; this does not make a call.
        self.model_spec = MODEL_REGISTRY["anthropic.claude-opus-4-5"]

    def invoke_tool(self, case_id: str, request: WorkspaceToolRequest | dict[str, Any]) -> dict[str, Any]:
        request = request if isinstance(request, WorkspaceToolRequest) else WorkspaceToolRequest.model_validate(request)
        if request.tool in self.FORBIDDEN_TOOLS:
            raise WorkspaceToolAuthorizationError(f"Workspace tool is not authorized: {request.tool}")
        argument_schema = _TOOL_ARGUMENTS.get(request.tool)
        if argument_schema is None:
            raise WorkspaceToolAuthorizationError(f"Unknown or unauthorized Workspace tool: {request.tool}")
        request.payload = argument_schema.model_validate(request.payload).model_dump(exclude_none=True)
        if request.tool in self.READ_TOOLS:
            return self._read_tool(case_id, request)
        if request.tool in self.ACTION_TOOLS:
            return self._action_tool(case_id, request)
        raise WorkspaceToolAuthorizationError(f"Unknown or unauthorized Workspace tool: {request.tool}")

    def chat(self, case_id: str, message: str) -> WorkspaceChatResponse:
        text = message.strip()
        if not text:
            raise ValueError("Workspace message must not be empty")
        self._append_chat(case_id, "human", text)
        if self.client is None:
            response = "The Workspace assistant is currently unavailable. Case operations remain accessible through the workspace controls."
            self._append_chat(case_id, "workspace", response)
            return WorkspaceChatResponse(response=response)
        turn_id = self._begin_workspace_turn(case_id, text)
        messages: list[dict[str, Any]] = [{"role": "user", "text": f"{self.system_prompt()}\n\nUser message:\n{text}"}]
        try:
            for _ in range(5):
                call = self.client.call_native(messages, self.tool_specs())
                self._record_requested_tools(case_id, turn_id, call.tool_uses)
                if not call.tool_uses:
                    response = " ".join(block.text for block in call.text_blocks).strip()
                    if not response:
                        raise RuntimeError("Workspace model returned neither a response nor a tool call")
                    self._finish_workspace_turn(case_id, turn_id, "completed", response)
                    self._append_chat(case_id, "workspace", response)
                    return WorkspaceChatResponse(response=response, recovery=self._has_recovery(case_id))
                messages.append({"role": "assistant", "text": " ".join(block.text for block in call.text_blocks), "tool_uses": [use.model_dump(mode="json") for use in call.tool_uses]})
                for tool_call in call.tool_uses:
                    result = self.invoke_tool(case_id, {"tool": tool_call.name, "payload": tool_call.arguments})
                    messages.append({"role": "tool", "call_id": tool_call.call_id, "result": result})
            raise RuntimeError("Workspace tool loop exceeded the five-round safety bound")
        except WorkspaceToolAuthorizationError as exc:
            self._finish_workspace_turn(case_id, turn_id, "failed", "The requested Workspace operation is not authorized.", type(exc).__name__)
            raise
        except Exception as exc:
            response = "The Workspace assistant could not respond. Case operations remain accessible through the workspace controls."
            self._finish_workspace_turn(case_id, turn_id, "failed", response, type(exc).__name__)
            self._append_chat(case_id, "workspace", response)
            self._append_chat(case_id, "system", f"Workspace model failure: {type(exc).__name__}: {exc}")
            return WorkspaceChatResponse(response=response)

    @staticmethod
    def system_prompt() -> str:
        tools = sorted(WorkspaceAgent.READ_TOOLS | WorkspaceAgent.ACTION_TOOLS)
        return "\n".join((
            "You are SimplifyNext Workspace Assistant, the human-facing operational interface for an academic-integrity investigation.",
            "Investigator performs local case reasoning; Steward performs global reassessment. Explain state, navigate history, mediate evidence, invoke operational tools, and explain/recover failures.",
            "You may inspect the whole case, but must not create or edit semantic Evidence, Proposition, Hypothesis, or Uncertainty nodes, determine guilt, or make a disciplinary outcome.",
            "Use a deterministic tool whenever the answer depends on current state. Do not guess status, failure cause, source inventory, pending request, safe state, or request/run linkage.",
            "Speak naturally to university investigators and hide internal graph IDs and schema jargon unless audit detail is explicitly requested.",
            f"Available Workspace tools: {', '.join(tools)}. Use provider-native tool calls for actions and ordinary natural-language text for the final answer.",
        ))

    def tool_specs(self) -> list[dict[str, Any]]:
        return [{"name": name, "description": f"Deterministic Workspace operation {name}.", "inputSchema": schema.model_json_schema()} for name, schema in sorted(_TOOL_ARGUMENTS.items()) if name in self.READ_TOOLS or name in self.ACTION_TOOLS]

    def _begin_workspace_turn(self, case_id: str, message: str) -> str:
        state = self.workflow.ensure_case(case_id)
        turn_id = f"workspace_{len(state.workspace_turn_history) + 1:06d}"
        state.workspace_turn_history.append({"workspace_turn_id": turn_id, "user_message": message, "model_status": "running", "requested_tool_names": [], "tool_execution_status": "pending"})
        self.workflow.repository.save(state)
        return turn_id

    def _record_requested_tools(self, case_id: str, turn_id: str, uses: list[ModelToolUse]) -> None:
        state = self.workflow.ensure_case(case_id)
        record = next(item for item in reversed(state.workspace_turn_history) if item["workspace_turn_id"] == turn_id)
        record["requested_tool_names"] = [use.name for use in uses]
        self.workflow.repository.save(state)

    def _finish_workspace_turn(self, case_id: str, turn_id: str, status: str, summary: str, failure_category: str | None = None) -> None:
        state = self.workflow.ensure_case(case_id)
        record = next(item for item in reversed(state.workspace_turn_history) if item["workspace_turn_id"] == turn_id)
        record.update({"model_status": status, "tool_execution_status": "completed" if status == "completed" else "failed", "failure_category": failure_category, "failure_summary": summary})
        self.workflow.repository.save(state)

    def _read_tool(self, case_id: str, request: WorkspaceToolRequest) -> dict[str, Any]:
        state = self.workflow.ensure_case(case_id)
        workspace = self.workflow.get_workspace(case_id)
        if request.tool == "GET_CASE_STATUS":
            return {key: workspace[key] for key in ("runtimeStatus", "currentActor", "caseStatus", "caseRevision")}
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
            failure = next((item for item in reversed(state.workspace_turn_history) if item.get("model_status") == "failed"), None)
            return {"available": failure is not None, "failure": failure, "human_summary": "The latest Workspace turn failed; no semantic graph mutation was performed." if failure else "No failed Workspace turn is recorded."}
        if request.tool == "GET_LAST_SAFE_STATE":
            return {"state": state.clean_checkpoint.get("state") if state.clean_checkpoint else None}
        if request.tool == "OPEN_TRACE":
            return {"runs": workspace.get("runs", []), "traces": state.trace_history}
        raise WorkspaceToolAuthorizationError(request.tool)

    def _action_tool(self, case_id: str, request: WorkspaceToolRequest) -> dict[str, Any]:
        if request.tool in {"RUN_INVESTIGATION", "RESUME_INVESTIGATION"}:
            return self.workflow.start_run(case_id)
        if request.tool == "PAUSE_INVESTIGATION":
            self.workflow.set_runtime(case_id, "PAUSED", "NONE")
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
            return {"status": "requested"}
        if request.tool == "RECOVER_FROM_SAFE_STATE":
            state = self.workflow.ensure_case(case_id)
            if not state.clean_checkpoint:
                raise EvidenceRequestConflict("No trustworthy safe state is available")
            state.trace_history.append({"event": "workspace_recovery", "recovery_action": "retained_current_canonical_state"})
            state.runtime_status = "IDLE"
            state.current_actor = "NONE"
            state.last_error = None
            self.workflow.repository.save(state)
            return self.workflow.get_workspace(case_id)
        if request.tool == "RESET_TO_CLEAN_BASELINE":
            from investigator.services.production_runner import reset_demo_case
            reset_demo_case(self.workflow, case_id)
            return self.workflow.get_workspace(case_id)
        raise WorkspaceToolAuthorizationError(request.tool)

    def _append_chat(self, case_id: str, role: str, text: str) -> None:
        state = self.workflow.ensure_case(case_id)
        state.workspace_chat_history.append({"role": role, "text": text})
        self.workflow.repository.save(state)

    def _has_recovery(self, case_id: str) -> bool:
        return any(item.get("event") == "workspace_recovery" for item in self.workflow.ensure_case(case_id).trace_history)
