"""Human-facing Workspace Agent facade over deterministic case operations."""

from __future__ import annotations

from dataclasses import dataclass, field
from copy import deepcopy
from copy import copy
import threading
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from investigator.llm import AnthropicModelClient, ModelClient, ModelToolUse, create_model_client, configured_provider
from investigator.services.evidence_requests import HumanEvidenceWorkflow
from investigator.help import product_guide
from investigator.runtime_settings import effective_model, record_help_model_use
from investigator.llm.bedrock import BedrockModelClient, is_provider_timeout, redact_sensitive_text


class WorkspaceChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1)

    @field_validator("message")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Workspace message must not be empty")
        return value


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

    def clear_case(self, case_id: str) -> None:
        self._sessions.pop(case_id, None)


class WorkspaceToolRequest(BaseModel):
    """Structured boundary for future tools; semantic graph writes are absent by design."""

    model_config = ConfigDict(extra="forbid")
    tool: str
    payload: dict[str, Any] = Field(default_factory=dict)


class _NoArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _ReadSourceArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sourceHandle: str


_TOOL_ARGUMENTS: dict[str, type[BaseModel]] = {
    name: _NoArguments for name in ("LIST_SOURCES", "GET_CASE_GUIDANCE_CONTEXT", "GET_PRODUCT_GUIDE")
}
_TOOL_ARGUMENTS["READ_SOURCE"] = _ReadSourceArguments


class WorkspaceToolAuthorizationError(PermissionError):
    pass


class WorkspaceModelUnavailable(RuntimeError):
    pass


class WorkspaceAgent:
    """Small operational assistant; it never exposes semantic graph mutation tools."""

    VNEXT_READ_TOOLS = frozenset({"GET_CASE_GUIDANCE_CONTEXT", "GET_PRODUCT_GUIDE", "READ_SOURCE", "LIST_SOURCES"})
    FORBIDDEN_TOOLS = frozenset(
        {
            "add_evidence",
            "add_proposition",
            "add_hypothesis",
            "add_uncertainty",
            "supports",
            "conflicts",
            "derived_from",
            "specializes",
            "depends_on",
            "targets",
            "archive",
            "reactivate",
        }
    )

    def __init__(
        self,
        workflow: HumanEvidenceWorkflow,
        client: ModelClient | None = None,
        session_store: WorkspaceSessionStore | None = None,
    ) -> None:
        self.workflow = workflow
        self.client = client
        self.session_store = session_store or WorkspaceSessionStore()
        self.model_spec = effective_model("workspace_help")
        self._case_locks: dict[str, threading.Lock] = {}
        self._case_locks_guard = threading.Lock()

    def _case_lock(self, case_id: str) -> threading.Lock:
        with self._case_locks_guard:
            return self._case_locks.setdefault(case_id, threading.Lock())

    def invoke_tool(self, case_id: str, request: WorkspaceToolRequest | dict[str, Any]) -> dict[str, Any]:
        request = request if isinstance(request, WorkspaceToolRequest) else WorkspaceToolRequest.model_validate(request)
        if request.tool in self.FORBIDDEN_TOOLS or request.tool not in self.VNEXT_READ_TOOLS:
            raise WorkspaceToolAuthorizationError(f"Workspace tool is not authorized: {request.tool}")
        argument_schema = _TOOL_ARGUMENTS.get(request.tool)
        if argument_schema is None:
            raise WorkspaceToolAuthorizationError(f"Unknown or unauthorized Workspace tool: {request.tool}")
        request.payload = argument_schema.model_validate(request.payload).model_dump(exclude_none=True)
        return self._read_tool(case_id, request)

    def chat(self, case_id: str, message: str) -> WorkspaceChatResponse:
        self.workflow.repository.require_case(case_id)
        if not message.strip():
            raise ValueError("Workspace message must not be empty")
        # One turn at a time per case: an interleaved turn would leave a tool call without its result in the shared
        # history, and every later message would then fail the pairing check.
        with self._case_lock(case_id):
            return self._chat_locked(case_id, message)

    def _chat_locked(self, case_id: str, message: str) -> WorkspaceChatResponse:
        attempts = 2
        session = self.session_store.session(case_id)
        clean_conversation = deepcopy(session["conversation"])
        clean_chat_history = deepcopy(session["chat_history"])
        clean_turns = deepcopy(session["turns"])
        for attempt in range(attempts):
            session["conversation"] = deepcopy(clean_conversation)
            session["chat_history"] = deepcopy(clean_chat_history)
            session["turns"] = deepcopy(clean_turns)
            try:
                return self._chat_once(case_id, message)
            except Exception as exc:
                # A provider timeout already cost a full paid call; repeating it would bill the same wait twice.
                if attempt + 1 == attempts or is_provider_timeout(exc):
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
        turn_model = effective_model("workspace_help")
        turn_client = self._client_for_model(turn_model)
        turn_id = self._begin_workspace_turn(case_id, text, turn_model.name)
        messages: list[dict[str, Any]] = [{"role": "system", "text": self.system_prompt()}, *session["conversation"]]
        try:
            for _ in range(3):
                self._assert_tool_result_pairing(messages)
                call = turn_client.call_native(messages, self.tool_specs())
                self._record_requested_tools(case_id, turn_id, call.tool_uses)
                if not call.tool_uses:
                    response = " ".join(block.text for block in call.text_blocks).strip()
                    if not response:
                        raise RuntimeError("Workspace model returned neither a response nor a tool call")
                    session["conversation"].append({"role": "assistant", "text": response})
                    self._finish_workspace_turn(case_id, turn_id, "completed", response)
                    record_help_model_use(case_id, turn_model.name, "completed")
                    self._append_chat(case_id, "workspace", response)
                    return WorkspaceChatResponse(response=response, recovery=self._has_recovery(case_id))
                assistant_message = {
                    "role": "assistant",
                    "text": " ".join(block.text for block in call.text_blocks),
                    "tool_uses": [use.model_dump(mode="json") for use in call.tool_uses],
                }
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
            self._finish_workspace_turn(
                case_id, turn_id, "failed", "The requested Workspace operation is not authorized.", type(exc).__name__
            )
            record_help_model_use(case_id, turn_model.name, "failed")
            raise
        except Exception as exc:
            self._finish_workspace_turn(
                case_id, turn_id, "failed", "The Workspace assistant could not respond.", type(exc).__name__
            )
            record_help_model_use(case_id, turn_model.name, "failed")
            raise

    def _client_for_model(self, model_spec: Any) -> ModelClient:
        if self.client is None:
            return create_model_client(model_spec)
        if isinstance(self.client, (BedrockModelClient, AnthropicModelClient)):
            return create_model_client(model_spec, provider=configured_provider())
        isolated = copy(self.client)
        if hasattr(isolated, "model_id"):
            isolated.model_id = model_spec.provider_model_id(configured_provider())
        if hasattr(isolated, "region"):
            isolated.region = model_spec.region
        return isolated

    def system_prompt(self) -> str:
        tools = sorted(self.VNEXT_READ_TOOLS)
        return "\n".join(
            (
                "You are the SimplifyNext Help Assistant, a read-only human-facing guide.",
                "WORKSPACE HELP RESPONSE POLICY: Workspace Help exists to simplify the case, not reproduce the report. Give the simplest accurate answer to the user's actual question.",
                "Use plain language and be concise by default. There is no minimum response length; if one sentence answers the question accurately, one sentence is enough.",
                "If the user explicitly asks for the shortest summary or shortest possible summary, answer in exactly one sentence containing only the current result or conclusion and the single most important remaining uncertainty; omit evidence recap, background, headings, bullets, next steps, and generic caveats. Apply this special rule only to explicit shortest-summary requests, while preserving material qualifications needed for accuracy.",
                "Answer with relevant case context only. Do not mention every candidate unless needed, turn a simple question into a candidate-by-candidate report summary, or mirror/reproduce the report structure by default. Prefer synthesis over enumeration. Do not add headings, numbered lists, background, or generic caveats unless they materially help.",
                "Provide substantially more detail only when the user explicitly asks for more detail, a breakdown, supporting evidence, all relevant information, or a comprehensive explanation. Follow-up questions may rely on the existing conversation instead of restating prior information.",
                "You may read bounded case guidance context, the authoritative product guide, and source documents, but must never mutate the case, add or remove evidence or students, rename students, run or recover an assessment, change a report, call Steward or Graph Warden, or make a disciplinary judgment.",
                "Never require a number of sources. Preserve accuracy and evidence discipline: distinguish source statements from truth, claims from propositions, similarity from collaboration, opportunity from use, association from misconduct, device or credential linkage from proof of the human actor, and absence of support from innocence. Brevity must not hide material qualifications or overstate a finding.",
                "When discussing whether to stop, consider whether unresolved uncertainty could materially change the assessment, whether feasible verification would distinguish explanations, whether more material would merely repeat existing evidence, and whether collection would be disproportionate or intrusive. Do not order the case closed; final procedural judgment remains human.",
                "For product-use questions, use GET_PRODUCT_GUIDE as the authoritative guide. Briefly redirect unrelated questions to the investigation, current evidence, or product guide.",
                f"Available read-only tools: {', '.join(tools)}. Return ordinary natural-language text only; never expose tool names, handles, internal IDs, graph terminology, paths, provider errors, or model configuration details.",
            )
        )

    def tool_specs(self) -> list[dict[str, Any]]:
        return [
            {
                "name": name,
                "description": f"Deterministic Workspace operation {name}.",
                "inputSchema": schema.model_json_schema(),
            }
            for name, schema in sorted(_TOOL_ARGUMENTS.items())
            if name in self.VNEXT_READ_TOOLS
        ]

    @staticmethod
    def _safe_tool_error(exc: Exception) -> str:
        return redact_sensitive_text(f"{type(exc).__name__}: {exc}")

    @staticmethod
    def _assert_tool_result_pairing(messages: list[dict[str, Any]]) -> None:
        """Verify the provider-neutral history has no orphaned tool uses."""
        for index, message in enumerate(messages):
            uses = message.get("tool_uses", []) if message.get("role") == "assistant" else []
            for offset, use in enumerate(uses, start=1):
                result = messages[index + offset] if index + offset < len(messages) else None
                if not result or result.get("role") != "tool" or result.get("call_id") != use.get("call_id"):
                    raise RuntimeError(f"Workspace conversation has an unpaired tool use: {use.get('call_id')}")

    def _begin_workspace_turn(self, case_id: str, message: str, model: str) -> str:
        session = self.session_store.session(case_id)
        turn_id = f"workspace_{len(session['turns']) + 1:06d}"
        session["turns"].append(
            {
                "workspace_turn_id": turn_id,
                "user_message": message,
                "model_status": "running",
                "model": model,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "requested_tool_names": [],
                "tool_execution_status": "pending",
            }
        )
        return turn_id

    def _record_requested_tools(self, case_id: str, turn_id: str, uses: list[ModelToolUse]) -> None:
        record = next(
            item
            for item in reversed(self.session_store.session(case_id)["turns"])
            if item["workspace_turn_id"] == turn_id
        )
        record["requested_tool_names"] = [use.name for use in uses]

    def _finish_workspace_turn(
        self, case_id: str, turn_id: str, status: str, summary: str, failure_category: str | None = None
    ) -> None:
        record = next(
            item
            for item in reversed(self.session_store.session(case_id)["turns"])
            if item["workspace_turn_id"] == turn_id
        )
        record.update(
            {
                "model_status": status,
                "tool_execution_status": "completed" if status == "completed" else "failed",
                "failure_category": failure_category,
                "failure_summary": summary,
                "ended_at": datetime.now(timezone.utc).isoformat(),
            }
        )

    def _read_tool(self, case_id: str, request: WorkspaceToolRequest) -> dict[str, Any]:
        state = self.workflow.repository.require_case(case_id)
        if request.tool == "GET_CASE_GUIDANCE_CONTEXT":
            from investigator.public_views import safe_help_context

            return safe_help_context(self.workflow, case_id)
        if request.tool == "GET_PRODUCT_GUIDE":
            return {"guide": product_guide()}
        if request.tool == "LIST_SOURCES":
            from investigator.public_views import public_source_handle, document_format

            return {
                "sources": [
                    {
                        "sourceHandle": public_source_handle(case_id, source.id),
                        "fileName": source.name,
                        "documentFormat": document_format(source.name),
                    }
                    for source in state.sources.values()
                ]
            }
        if request.tool == "READ_SOURCE":
            from investigator.public_views import resolve_source_handle, document_format

            source = state.sources[resolve_source_handle(state, str(request.payload.get("sourceHandle")))]
            return {
                "source": {
                    "sourceHandle": request.payload["sourceHandle"],
                    "fileName": source.name,
                    "documentFormat": document_format(source.name),
                    "content": source.content or "",
                }
            }
        raise WorkspaceToolAuthorizationError(request.tool)

    def _append_chat(self, case_id: str, role: str, text: str) -> None:
        self.session_store.session(case_id)["chat_history"].append({"role": role, "text": text})

    def _has_recovery(self, case_id: str) -> bool:
        return any(
            item.get("event") == "workspace_recovery" for item in self.workflow.ensure_case(case_id).trace_history
        )

    def chat_history(self, case_id: str) -> list[dict[str, str]]:
        return [dict(item) for item in self.session_store.session(case_id)["chat_history"]]
