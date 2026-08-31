"""
Teaching context and recency policies (Phase 12B.1).

Defines policies for:
- Recency Policy: Determines if a lesson/context entry is recent (default 14 days) and matches academic year.
- Context Priority Policy: Enforces priority order TEACHER > COORDINATION > SCHOOL_PLAN > AUTONOMOUS.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass
class RecencyPolicy:
    """Configurable policy for calculating context recency."""

    recent_context_days: int = 14

    def is_recent(
        self,
        recorded_at: datetime,
        reference_date: datetime | None = None,
    ) -> bool:
        """Return True if recorded_at falls within recent_context_days from reference_date."""
        ref = reference_date or datetime.now(timezone.utc)
        if ref.tzinfo is None:
            ref = ref.replace(tzinfo=timezone.utc)
        if recorded_at.tzinfo is None:
            recorded_at = recorded_at.replace(tzinfo=timezone.utc)

        cutoff = ref - timedelta(days=self.recent_context_days)
        return recorded_at >= cutoff


@dataclass
class ContextPriorityPolicy:
    """Configurable policy for prioritizing multiple pedagogical contexts."""

    HIERARCHY: dict[str, int] = None

    def __post_init__(self):
        if self.HIERARCHY is None:
            self.HIERARCHY = {
                "TEACHER": 1,
                "COORDINATION": 2,
                "SCHOOL_PLAN": 3,
                "AUTONOMOUS": 4,
            }

    def get_rank(self, source: str) -> int:
        return self.HIERARCHY.get(source.upper(), 99)

    def select_primary_source(self, sources: list[str]) -> str:
        if not sources:
            return "AUTONOMOUS"
        sorted_sources = sorted(sources, key=lambda s: self.get_rank(s))
        return sorted_sources[0].upper()
