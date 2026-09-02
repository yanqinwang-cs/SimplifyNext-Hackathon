"""Human-facing Workspace Agent facade over deterministic case operations."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from investigator.graph import GraphNodeType
from investigator.llm import ModelClient
from investigator.models.source import Source
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
    tool: Literal[
        "GET_CASE_STATUS", "GET_CASE_SUMMARY", "GET_CURRENT_GRAPH", "GET_CURRENT_FOCUS",
        "LIST_SOURCES", "READ_SOURCE", "GET_PENDING_REQUEST", "LIST_REQUEST_HISTORY",
        "LIST_RUNS", "GET_RUN", "GET_LATEST_FAILURE", "GET_LAST_SAFE_STATE", "OPEN_TRACE",
        "RUN_INVESTIGATION", "PAUSE_INVESTIGATION", "RESUME_INVESTIGATION", "ADD_SOURCE",
        "FULFIL_REQUEST", "MARK_REQUEST_UNAVAILABLE", "REQUEST_STEWARD_REVIEW",
        "RECOVER_FROM_SAFE_STATE",
    ]
    payload: dict[str, Any] = Field(default_factory=dict)


class WorkspaceToolAuthorizationError(PermissionError):
    pass


class WorkspaceAgent:
    """Small operational assistant; it never exposes semantic graph mutation tools."""

    READ_TOOLS = frozenset({
        "GET_CASE_STATUS", "GET_CASE_SUMMARY", "GET_CURRENT_GRAPH", "GET_CURRENT_FOCUS",
        "LIST_SOURCES", "READ_SOURCE", "GET_PENDING_REQUEST", "LIST_REQUEST_HISTORY",
        "LIST_RUNS", "GET_RUN", "GET_LATEST_FAILURE", "GET_LAST_SAFE_STATE", "OPEN_TRACE",
    })
    ACTION_TOOLS = frozenset({
        "RUN_INVESTIGATION", "PAUSE_INVESTIGATION", "RESUME_INVESTIGATION", "ADD_SOURCE",
        "FULFIL_REQUEST", "MARK_REQUEST_UNAVAILABLE", "REQUEST_STEWARD_REVIEW",
        "RECOVER_FROM_SAFE_STATE",
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
        if request.tool in self.READ_TOOLS:
            return self._read_tool(case_id, request)
        if request.tool in self.ACTION_TOOLS:
            return self._action_tool(case_id, request)
        raise WorkspaceToolAuthorizationError(f"Unknown or unauthorized Workspace tool: {request.tool}")

    def chat(self, case_id: str, message: str) -> WorkspaceChatResponse:
        text = message.strip()
        lowered = text.lower()
        if not text:
            raise ValueError("Workspace message must not be empty")
        if any(word in lowered for word in ("run investigation", "continue", "resume")):
            self.invoke_tool(case_id, WorkspaceToolRequest(tool="RESUME_INVESTIGATION"))
            response = "The investigation has been resumed. I’ll show the next request or status here when it is available."
            actions = ["View current case"]
        elif "stop" in lowered or "pause" in lowered:
            self.invoke_tool(case_id, WorkspaceToolRequest(tool="PAUSE_INVESTIGATION"))
            response = "The investigation is paused. No semantic case reasoning was changed by this action."
            actions = ["Resume investigation"]
        elif "need" in lowered or "request" in lowered:
            pending = self.invoke_tool(case_id, WorkspaceToolRequest(tool="GET_PENDING_REQUEST"))["request"]
            if pending:
                response = f"The investigation needs: {pending.get('informationSought') or pending.get('information_sought')}. This matters because {pending.get('reason')}."
            else:
                response = "There is no outstanding request for human information."
            actions = ["Show sources", "Show history"]
        elif "source" in lowered or "evidence" in lowered or "case" in lowered or "happening" in lowered:
            summary = self.invoke_tool(case_id, WorkspaceToolRequest(tool="GET_CASE_SUMMARY"))
            response = summary["human_summary"]
            actions = ["Show sources", "Show history"]
        elif "fail" in lowered or "error" in lowered or "debug" in lowered or "commit" in lowered:
            failure = self.invoke_tool(case_id, WorkspaceToolRequest(tool="GET_LATEST_FAILURE"))
            state = self.workflow.ensure_case(case_id)
            if failure.get("available") and state.runtime_status == "FAILED" and state.clean_checkpoint:
                self.invoke_tool(case_id, WorkspaceToolRequest(tool="RECOVER_FROM_SAFE_STATE"))
                failure = self.invoke_tool(case_id, WorkspaceToolRequest(tool="GET_LATEST_FAILURE"))
            response = failure["human_summary"]
            actions = ["Open debug details"] if failure.get("available") else []
        elif "steward" in lowered or "review" in lowered:
            response = "I can request a Case Steward review of the current investigation state."
            self.invoke_tool(case_id, WorkspaceToolRequest(tool="REQUEST_STEWARD_REVIEW"))
            actions = ["View current case"]
        elif "what are you" in lowered or "agents" in lowered:
            response = "I’m the Workspace Agent: I handle the human interface, evidence workflow, debugging, recovery, and history. The Investigator performs local reasoning, while the Case Steward manages global reassessment. Final disciplinary judgment remains human."
            actions = []
        else:
            response = "I can explain the current case, show sources and history, run or pause the investigation, mediate an evidence request, or explain the latest failure."
            actions = ["Explain current case", "Show latest failure", "Run investigation"]
        self._append_chat(case_id, "human", text)
        self._append_chat(case_id, "workspace", response)
        return WorkspaceChatResponse(response=response, actions=actions, recovery=self._has_recovery(case_id))

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
            restored = self.workflow._restore_clean_checkpoint(state)
            restored.trace_history.append({"event": "workspace_recovery", "recovery_action": "restored_last_safe_state"})
            restored.runtime_status = "IDLE"
            restored.current_actor = "NONE"
            self.workflow.repository.save(restored)
            return self.workflow.get_workspace(case_id)
        raise WorkspaceToolAuthorizationError(request.tool)

    def _append_chat(self, case_id: str, role: str, text: str) -> None:
        state = self.workflow.ensure_case(case_id)
        state.workspace_chat_history.append({"role": role, "text": text})
        self.workflow.repository.save(state)

    def _has_recovery(self, case_id: str) -> bool:
        return any(item.get("event") == "workspace_recovery" for item in self.workflow.ensure_case(case_id).trace_history)
