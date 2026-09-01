from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from investigator.cycle import AvailableEnquiry
from investigator.graph import GraphNode


class EvidenceRelease(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")
    evidence: list[GraphNode] = Field(default_factory=list)
    newly_available: list[AvailableEnquiry] = Field(default_factory=list)
    materially_usable_action_ids: list[str] = Field(default_factory=list)


@dataclass
class Stage1Environment:
    """Small trusted boundary: hidden releases never enter model observations."""

    initial_enquiries: list[AvailableEnquiry]
    releases: dict[str, EvidenceRelease]
    completed_action_ids: set[str]
    _available: dict[str, AvailableEnquiry]
    _recent: list[GraphNode]

    @classmethod
    def for_fixture(cls, fixture: object) -> "Stage1Environment":
        return cls(
            initial_enquiries=deepcopy(fixture.available_enquiries),
            releases=deepcopy(fixture.releases),
            completed_action_ids=set(),
            _available={a.action_id: deepcopy(a) for a in fixture.available_enquiries},
            _recent=[],
        )

    def current_available_enquiries(self) -> list[AvailableEnquiry]:
        return [deepcopy(self._available[key]) for key in sorted(self._available)]

    def execute_enquiry(self, action_id: str) -> EvidenceRelease:
        if action_id in self.completed_action_ids:
            raise ValueError(f"Enquiry has already been completed: {action_id}")
        action = self._available.pop(action_id, None)
        if action is None:
            raise ValueError(f"Enquiry is not currently available: {action_id}")
        release = deepcopy(self.releases.get(action_id, EvidenceRelease()))
        self.completed_action_ids.add(action_id)
        self._recent = deepcopy(release.evidence)
        for available in release.newly_available:
            if available.action_id not in self.completed_action_ids:
                self._available[available.action_id] = deepcopy(available)
        return release

    def recently_released_evidence(self) -> list[GraphNode]:
        return deepcopy(self._recent)

    def consume_recent_release(self) -> None:
        self._recent.clear()

    def materially_usable_action_ids(self) -> list[str]:
        return sorted({action_id for action_id in self._available if action_id in self.releases and action_id not in self.completed_action_ids})

