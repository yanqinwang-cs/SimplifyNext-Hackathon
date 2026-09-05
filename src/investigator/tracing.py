"""Small durable execution traces for interactive investigations."""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_session_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex[:8]


class TraceEvent(BaseModel):
    timestamp: str
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)


class InteractiveTrace(BaseModel):
    session_id: str
    case_id: str
    environment_id: str
    model_id: str
    started_at: str
    updated_at: str
    status: str
    initial_prompt: str
    investigator_seed_hypothesis: str | None = None
    initial_raw_model_output: Any | None = None
    initial_response: Any | None = None
    initial_metadata: Any | None = None
    events: list[TraceEvent] = Field(default_factory=list)
    current_case_state: Any | None = None
    latest_error: dict[str, Any] | None = None


class InteractiveTraceWriter:
    """Atomically writes one interactive trace under runs/<session_id>."""

    def __init__(self, runs_root: str | Path, trace: InteractiveTrace) -> None:
        self.directory = Path(runs_root) / trace.session_id
        self.trace_path = self.directory / "trace.json"
        self.trace = trace
        self.directory.mkdir(parents=True, exist_ok=False)

    def record(self, event_type: str, payload: dict[str, Any] | None = None) -> None:
        self.trace.events.append(TraceEvent(timestamp=utc_now(), type=event_type, payload=payload or {}))
        self.write()

    def update(self, **values: Any) -> None:
        self.trace = self.trace.model_copy(update={"updated_at": utc_now(), **values})
        self.write()

    def write(self) -> None:
        self.trace = self.trace.model_copy(update={"updated_at": utc_now()})
        with NamedTemporaryFile("w", encoding="utf-8", dir=self.directory, delete=False, suffix=".tmp") as handle:
            temporary = Path(handle.name)
            json.dump(self.trace.model_dump(mode="json"), handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(self.trace_path)
