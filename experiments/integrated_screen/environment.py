from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, model_validator

from investigator.cycle import AvailableEnquiry
from investigator.graph import GraphNode


class EvidenceRelease(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")
    evidence: list[GraphNode] = Field(default_factory=list)
    newly_available: list[AvailableEnquiry] = Field(default_factory=list)
    newly_materially_usable_action_ids: list[str] = Field(default_factory=list)
    no_longer_materially_usable_action_ids: list[str] = Field(default_factory=list)
    newly_unavailable_action_ids: list[str] = Field(default_factory=list)
    global_frontier_assessed: bool | None = None

    @model_validator(mode="after")
    def unique_ids(self) -> "EvidenceRelease":
        fields = (self.newly_materially_usable_action_ids, self.no_longer_materially_usable_action_ids, self.newly_unavailable_action_ids)
        if any(len(values) != len(set(values)) for values in fields):
            raise ValueError("release action IDs must not contain duplicates")
        if set(self.newly_materially_usable_action_ids) & set(self.no_longer_materially_usable_action_ids):
            raise ValueError("an action cannot become useful and non-material in the same release")
        return self


@dataclass
class Stage1Environment:
    """Small trusted boundary: hidden releases never enter model observations."""

    initial_enquiries: list[AvailableEnquiry]
    releases: dict[str, EvidenceRelease]
    completed_action_ids: set[str]
    _available: dict[str, AvailableEnquiry]
    _materially_usable: set[str]
    _global_frontier_assessed: bool
    _assess_global_frontier_when_empty: bool
    _recent: list[GraphNode]

    @classmethod
    def for_fixture(cls, fixture: object) -> "Stage1Environment":
        if len(fixture.available_enquiries) != len({a.action_id for a in fixture.available_enquiries}):
            raise ValueError("available action IDs must not contain duplicates")
        available = {a.action_id: deepcopy(a) for a in fixture.available_enquiries}
        material = set(fixture.materially_usable_action_ids)
        if len(fixture.materially_usable_action_ids) != len(material):
            raise ValueError("materially usable action IDs must not contain duplicates")
        unknown = material - set(available)
        if unknown:
            raise ValueError(f"materially usable actions must be available: {sorted(unknown)}")
        return cls(
            initial_enquiries=deepcopy(fixture.available_enquiries),
            releases=deepcopy(fixture.releases),
            completed_action_ids=set(),
            _available=available,
            _materially_usable=material,
            _global_frontier_assessed=False,
            _assess_global_frontier_when_empty=fixture.assess_global_frontier_when_materially_usable_empty,
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
        self._materially_usable.discard(action_id)
        self._recent = deepcopy(release.evidence)
        for identifier in release.newly_unavailable_action_ids:
            if identifier not in self._available and identifier not in self.completed_action_ids:
                raise ValueError(f"unknown action in release transition: {identifier}")
            self._available.pop(identifier, None)
            self._materially_usable.discard(identifier)
        for available in release.newly_available:
            if available.action_id not in self.completed_action_ids:
                self._available[available.action_id] = deepcopy(available)
        known = set(self._available) | self.completed_action_ids
        for identifier in [*release.newly_materially_usable_action_ids, *release.no_longer_materially_usable_action_ids]:
            if identifier not in known:
                raise ValueError(f"unknown action in release transition: {identifier}")
        self._materially_usable.update(release.newly_materially_usable_action_ids)
        self._materially_usable.difference_update(release.no_longer_materially_usable_action_ids)
        self._materially_usable.intersection_update(self._available)
        if release.global_frontier_assessed is not None:
            self._global_frontier_assessed = release.global_frontier_assessed
        return release

    def recently_released_evidence(self) -> list[GraphNode]:
        return deepcopy(self._recent)

    def consume_recent_release(self) -> None:
        self._recent.clear()

    def materially_usable_action_ids(self) -> list[str]:
        return sorted(self._materially_usable)

    def global_frontier_assessed(self) -> bool:
        return self._global_frontier_assessed or (self._assess_global_frontier_when_empty and not self._materially_usable)
