"""Offline, fixture-driven sequential Steward evaluator.

This module deliberately has no model or AWS dependency.  Producers receive only
the public observation and return one raw decision at a time.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from investigator.graph import CaseGraph, EdgeRelation, GraphStatus
from investigator.llm import normalize_json_text
from investigator.roles import GraphInvestigationCoordinator, InvestigationFocus, StewardDecision, StewardReviewContext
from experiments.steward_screen.prompt import build_prompt


class StewardObservation(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid", frozen=True)
    observation_id: str
    description: str
    graph: CaseGraph
    focus: InvestigationFocus
    participants: list[Any] = Field(default_factory=list)
    review_context: StewardReviewContext | None = None


class IssueKind(str, Enum):
    STALE_ACTIVE = "STALE_ACTIVE"
    RELEVANT_ARCHIVED = "RELEVANT_ARCHIVED"
    NEGLECTED_ACTIVE = "NEGLECTED_ACTIVE"
    OVER_SPECIFIC_FOCUS = "OVER_SPECIFIC_FOCUS"
    CURRENT_FOCUS_STALE = "CURRENT_FOCUS_STALE"


class TerminalMode(str, Enum):
    QUIESCENCE = "QUIESCENCE"
    STOP_UNRESOLVED = "STOP_UNRESOLVED"


class StewardIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    issue_id: str
    kind: IssueKind
    target_node_id: str
    parent_node_id: str | None = None
    allowed_destination_node_ids: list[str] = Field(default_factory=list)
    depends_on_issue_ids: list[str] = Field(default_factory=list)


class TrajectoryFixture(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")
    fixture_id: str
    description: str
    graph: CaseGraph
    focus: InvestigationFocus
    participants: list[Any] = Field(default_factory=list)
    review_context: StewardReviewContext | None = None
    issues: list[StewardIssue] = Field(default_factory=list)
    must_remain_active_node_ids: set[str] = Field(default_factory=set)
    must_remain_archived_node_ids: set[str] = Field(default_factory=set)
    terminal_mode: TerminalMode = TerminalMode.QUIESCENCE
    step_cap: int = 8
    required_edges: list[tuple[str, str, str]] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_fixture(self) -> "TrajectoryFixture":
        ids = set(self.graph.nodes)
        if not self.issues and self.terminal_mode is TerminalMode.STOP_UNRESOLVED and self.review_context is None:
            raise ValueError("STOP_UNRESOLVED fixtures require trusted review context")
        if not {self.focus.node_id, *self.must_remain_active_node_ids, *self.must_remain_archived_node_ids} <= ids:
            raise ValueError("fixture references an unknown node")
        if any(self.graph.nodes[n].status is not GraphStatus.ACTIVE for n in self.must_remain_active_node_ids): raise ValueError("must_remain_active nodes must start active")
        if any(self.graph.nodes[n].status is not GraphStatus.ARCHIVED for n in self.must_remain_archived_node_ids): raise ValueError("must_remain_archived nodes must start archived")
        actual_edges = {(e.source_id, e.relation.value, e.target_id) for e in self.graph.edges.values()}
        if not set(self.required_edges) <= actual_edges: raise ValueError("fixture structural basis is absent")
        issue_ids = {i.issue_id for i in self.issues}
        if len(issue_ids) != len(self.issues) or any(set(i.depends_on_issue_ids) - issue_ids for i in self.issues):
            raise ValueError("issue IDs or dependencies are invalid")
        for issue in self.issues:
            if issue.target_node_id not in ids or set(issue.allowed_destination_node_ids) - ids:
                raise ValueError("issue references an unknown node")
            node = self.graph.nodes[issue.target_node_id]
            if issue.kind is IssueKind.STALE_ACTIVE and node.status is not GraphStatus.ACTIVE: raise ValueError("STALE_ACTIVE must start active")
            if issue.kind is IssueKind.RELEVANT_ARCHIVED and node.status is not GraphStatus.ARCHIVED: raise ValueError("RELEVANT_ARCHIVED must start archived")
            if issue.kind is IssueKind.NEGLECTED_ACTIVE and node.status is GraphStatus.ARCHIVED:
                allowed = any(self.issues_by_id(dep).kind is IssueKind.RELEVANT_ARCHIVED and self.issues_by_id(dep).target_node_id == issue.target_node_id for dep in issue.depends_on_issue_ids)
                if not allowed: raise ValueError("NEGLECTED_ACTIVE must start active unless directly dependent on reactivation")
            if issue.kind in {IssueKind.OVER_SPECIFIC_FOCUS, IssueKind.CURRENT_FOCUS_STALE} and self.focus.node_id != issue.target_node_id: raise ValueError("focus issue must target current focus")
            if issue.kind is IssueKind.OVER_SPECIFIC_FOCUS:
                if issue.parent_node_id not in ids or self.graph.nodes[issue.parent_node_id].status is not GraphStatus.ACTIVE or not any(e.source_id == issue.target_node_id and e.target_id == issue.parent_node_id and e.relation is EdgeRelation.SPECIALIZES for e in self.graph.edges.values()):
                    raise ValueError("invalid immediate SPECIALIZES parent")
            if issue.kind is IssueKind.CURRENT_FOCUS_STALE and (not issue.allowed_destination_node_ids or issue.target_node_id in issue.allowed_destination_node_ids):
                raise ValueError("current-focus issue needs distinct destinations")
            if issue.kind in {IssueKind.OVER_SPECIFIC_FOCUS, IssueKind.CURRENT_FOCUS_STALE} and node.status is not GraphStatus.ACTIVE: raise ValueError("focus issue target must start active")
            if issue.kind is IssueKind.CURRENT_FOCUS_STALE and any(self.graph.nodes[d].status is not GraphStatus.ACTIVE for d in issue.allowed_destination_node_ids): raise ValueError("focus destinations must start active")
            if issue.kind is IssueKind.STALE_ACTIVE and issue.target_node_id in self.must_remain_active_node_ids: raise ValueError("contradictory stale protection")
            if issue.kind is IssueKind.RELEVANT_ARCHIVED and issue.target_node_id in self.must_remain_archived_node_ids: raise ValueError("contradictory reactivation protection")
            if issue.kind is IssueKind.NEGLECTED_ACTIVE and issue.target_node_id in self.must_remain_archived_node_ids: raise ValueError("contradictory neglected protection")
            if issue.kind is IssueKind.CURRENT_FOCUS_STALE and issue.target_node_id in self.must_remain_active_node_ids: raise ValueError("contradictory focus retirement protection")
        # dependency graph must be acyclic
        def visit(x: str, path: set[str]) -> None:
            if x in path: raise ValueError("issue dependency cycle")
            for dep in next(i for i in self.issues if i.issue_id == x).depends_on_issue_ids: visit(dep, path | {x})
        for i in self.issues: visit(i.issue_id, set())
        if self.terminal_mode is TerminalMode.STOP_UNRESOLVED and self.review_context is None: raise ValueError("trusted review context required")
        if self.terminal_mode is TerminalMode.STOP_UNRESOLVED:
            review = self.review_context
            if not review.global_frontier_assessed or review.neglected_candidate_node_ids or review.obvious_useful_region_remains or review.materially_usable_action_ids or (review.local_exhaustion_required and not review.local_frontier_exhausted):
                raise ValueError("STOP_UNRESOLVED fixture context cannot support stopping")
            for identifier in review.active_unresolved_ids:
                if identifier not in ids or self.graph.nodes[identifier].node_type.value != "uncertainty" or self.graph.nodes[identifier].status is not GraphStatus.ACTIVE: raise ValueError("STOP_UNRESOLVED unresolved IDs must be active uncertainties")
        return self

    def issues_by_id(self, issue_id: str) -> StewardIssue:
        return next(i for i in self.issues if i.issue_id == issue_id)

    def observation(self, graph: CaseGraph | None = None, focus: InvestigationFocus | None = None) -> StewardObservation:
        return StewardObservation(observation_id=self.fixture_id, description=self.description, graph=graph or self.graph, focus=focus or self.focus, participants=self.participants, review_context=self.review_context)


class Producer(Protocol):
    def __call__(self, prompt: str) -> Any: ...


class ScriptedProducer:
    def __init__(self, outputs: list[Any]): self.outputs, self.index = outputs, 0
    def __call__(self, prompt: str) -> Any:
        if self.index >= len(self.outputs): return None
        output = self.outputs[self.index]; self.index += 1; return output


FAILURE_CODES = {"SCHEMA_FAILURE", "NULL_OR_NO_DECISION", "MULTIPLE_OPERATIONS_RETURNED", "INVENTED_IDENTIFIER", "ILLEGAL_SHIFT", "ILLEGAL_REACTIVATE", "BAD_GENERALIZATION", "ILLEGAL_ARCHIVE", "HARMFUL_ARCHIVE", "HARMFUL_REACTIVATION", "PREMATURE_STOP", "STALE_KEEP_LOOP", "NO_PROGRESS_LOOP", "OSCILLATION", "STEP_CAP_WITH_PENDING_ISSUES"}
_ADAPTER = TypeAdapter(StewardDecision)


def _resolved(issue: StewardIssue, c: GraphInvestigationCoordinator) -> bool:
    n = c.graph.nodes[issue.target_node_id]
    if issue.kind is IssueKind.STALE_ACTIVE: return n.status is GraphStatus.ARCHIVED
    if issue.kind is IssueKind.RELEVANT_ARCHIVED: return n.status is GraphStatus.ACTIVE
    if issue.kind is IssueKind.NEGLECTED_ACTIVE: return n.status is GraphStatus.ACTIVE and c.focus.node_id == n.id
    if issue.kind is IssueKind.OVER_SPECIFIC_FOCUS: return n.status is GraphStatus.ACTIVE and c.focus.node_id == issue.parent_node_id
    return n.status is GraphStatus.ARCHIVED and c.focus.node_id in issue.allowed_destination_node_ids


def _fingerprint(c: GraphInvestigationCoordinator, issues: list[StewardIssue]) -> tuple:
    statuses = issue_states(issues, c)
    return (c.focus.node_id, tuple(sorted((n.id, n.status.value) for n in c.graph.nodes.values())), c.stopped, tuple((i.issue_id, statuses[i.issue_id].value) for i in issues))


@dataclass
class TrajectoryResult:
    steps: list[dict] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    termination: str = "step_cap"


class IssueState(str, Enum):
    BLOCKED = "BLOCKED"
    ACTIONABLE = "ACTIONABLE"
    RESOLVED = "RESOLVED"


def issue_states(issues: list[StewardIssue], c: GraphInvestigationCoordinator) -> dict[str, IssueState]:
    resolved = {i.issue_id: _resolved(i, c) for i in issues}
    return {i.issue_id: IssueState.BLOCKED if any(not resolved[d] for d in i.depends_on_issue_ids) else IssueState.RESOLVED if resolved[i.issue_id] else IssueState.ACTIONABLE for i in issues}


def _parse_raw(raw: Any) -> Any:
    if isinstance(raw, str):
        import json
        text = normalize_json_text(raw)
        return json.loads(text)
    return raw


def run_fixture(fixture: TrajectoryFixture, producer: Producer) -> TrajectoryResult:
    c = GraphInvestigationCoordinator(deepcopy(fixture.graph), fixture.focus.model_copy(deep=True)); result = TrajectoryResult(); seen = {_fingerprint(c, fixture.issues)}; unchanged_keep = 0; no_progress = 0
    def add_failure(code: str) -> None:
        if code not in result.failures: result.failures.append(code)
    for step in range(1, fixture.step_cap + 1):
        obs = fixture.observation(c.graph, c.focus); raw = producer(build_prompt(obs))
        if raw is None or raw == "": result.failures.append("NULL_OR_NO_DECISION"); result.termination = "failure"; break
        if isinstance(raw, list): result.failures.append("MULTIPLE_OPERATIONS_RETURNED"); result.termination = "failure"; break
        try: decision = _ADAPTER.validate_python(_parse_raw(raw))
        except ValueError as exc:
            result.failures.append("MULTIPLE_OPERATIONS_RETURNED" if "Extra data" in str(exc) else "SCHEMA_FAILURE"); result.termination = "failure"; break
        before = _fingerprint(c, fixture.issues); before_statuses = issue_states(fixture.issues, c); before_states = {k: v is IssueState.RESOLVED for k,v in before_statuses.items()}
        try:
            if decision.operation == "reactivate" and c.graph.nodes.get(decision.target_node_id) is not None and c.graph.nodes[decision.target_node_id].status is not GraphStatus.ARCHIVED:
                raise ValueError("REACTIVATE target must be archived")
            c.review_with_steward(decision, review_context=obs.review_context if decision.operation == "stop_unresolved" else None)
        except ValueError as exc:
            msg = str(exc); code = "INVENTED_IDENTIFIER" if "Unknown graph node" in msg else ("ILLEGAL_SHIFT" if decision.operation == "shift_focus" else "ILLEGAL_REACTIVATE" if decision.operation == "reactivate" else "BAD_GENERALIZATION" if decision.operation == "generalize" else "PREMATURE_STOP" if decision.operation == "stop_unresolved" else "ILLEGAL_ARCHIVE")
            result.failures.append(code); result.termination = "failure"; break
        after = _fingerprint(c, fixture.issues); after_statuses = issue_states(fixture.issues, c); after_states = {k: v is IssueState.RESOLVED for k,v in after_statuses.items()}; resolved = [k for k,v in after_states.items() if v and not before_states[k]]
        harmful_archive = any(c.graph.nodes[n].status is GraphStatus.ARCHIVED for n in fixture.must_remain_active_node_ids) or any(i.kind in {IssueKind.NEGLECTED_ACTIVE, IssueKind.OVER_SPECIFIC_FOCUS} and not after_states[i.issue_id] and c.graph.nodes[i.target_node_id].status is GraphStatus.ARCHIVED for i in fixture.issues)
        harmful_reactivation = any(c.graph.nodes[n].status is GraphStatus.ACTIVE for n in fixture.must_remain_archived_node_ids)
        harmful = harmful_archive or harmful_reactivation
        if harmful_archive: result.failures.append("HARMFUL_ARCHIVE")
        if harmful_reactivation: result.failures.append("HARMFUL_REACTIVATION")
        cls = "PROGRESS" if resolved and not harmful else ("NEUTRAL" if not harmful else "HARMFUL")
        result.steps.append({"step": step, "raw_output": raw, "operation": decision.operation, "classification": cls, "before": before, "after": after, "resolved": resolved, "remaining": [i.issue_id for i in fixture.issues if not after_states[i.issue_id]]})
        if c.stopped and (fixture.terminal_mode is not TerminalMode.STOP_UNRESOLVED or any(not value for value in after_states.values())):
            result.failures.append("PREMATURE_STOP")
        if c.stopped:
            result.termination = "stopped" if not [i for i in fixture.issues if not after_states[i.issue_id]] and fixture.terminal_mode is TerminalMode.STOP_UNRESOLVED else "failure"; break
        if fixture.terminal_mode is TerminalMode.QUIESCENCE and all(after_states.values()): result.termination = "quiescent"; break
        if step == fixture.step_cap and any(not value for value in after_states.values()):
            add_failure("STEP_CAP_WITH_PENDING_ISSUES"); result.termination = "step_cap"; break
        if not resolved: no_progress += 1
        else: no_progress = 0
        if decision.operation == "keep_focus" and not resolved: unchanged_keep += 1
        else: unchanged_keep = 0
        if unchanged_keep >= 2: result.failures.append("STALE_KEEP_LOOP"); result.termination = "failure"; break
        if no_progress >= 3: result.failures.append("NO_PROGRESS_LOOP"); result.termination = "failure"; break
        if after in seen and not resolved and decision.operation != "keep_focus" and unchanged_keep < 2: result.failures.append("OSCILLATION"); result.termination = "failure"; break
        seen.add(after)
    if result.termination == "step_cap" and any(not _resolved(i, c) for i in fixture.issues): add_failure("STEP_CAP_WITH_PENDING_ISSUES")
    return result
