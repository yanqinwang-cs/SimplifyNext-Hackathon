"""Deterministic, non-model matcher for the controlled Stage 2B environment."""
from dataclasses import dataclass
import re

from .fixtures import Fixture, hidden_fixtures


@dataclass(frozen=True)
class MatchResult:
    fixture: Fixture | None
    quality: str
    reason: str


@dataclass(frozen=True)
class VisibleMatch:
    answerable: bool
    reason: str


class ControlledEvidenceMatcher:
    def __init__(self) -> None:
        self.fixtures = hidden_fixtures()

    @staticmethod
    def visible_match(request_text: str, visible_sources: list[tuple[str, str | None]]) -> VisibleMatch:
        """Check only admitted source names/content; never consult hidden fixtures."""
        text = request_text.lower()
        terms = ("concern", "anomal", "allegation", "assessment record", "conduct", "observation", "submitted", "rules", "logistics")
        source_text = " ".join(f"{name} {content or ''}".lower() for name, content in visible_sources)
        source_hits = sum(1 for term in terms if term in source_text and term in text)
        if source_hits >= 1:
            return VisibleMatch(True, "Responsive information is already present in currently visible case sources.")
        return VisibleMatch(False, "Currently visible sources do not substantially address this information need.")

    def match(self, request_text: str, released: set[str] | None = None) -> MatchResult:
        released = released or set()
        words = set(re.findall(r"[a-z0-9]+", request_text.lower()))
        scored: list[tuple[int, int, Fixture]] = []
        for order, fixture in enumerate(self.fixtures):
            if fixture.key in released:
                continue
            hits = sum(1 for concept in fixture.concepts if set(re.findall(r"[a-z0-9]+", concept.lower())) <= words)
            if hits:
                scored.append((hits, -order, fixture))
        if not scored:
            return MatchResult(None, "none", "No unreleased hidden fixture matched the request.")
        _, _, fixture = max(scored, key=lambda item: (item[0], item[1]))
        return MatchResult(fixture, "direct" if len(scored) == 1 else "responsive", "Deterministic concept match.")
