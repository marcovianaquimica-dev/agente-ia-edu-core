"""Translate a scope's external id into a real academic entity.

The core adopted the academic hierarchy as real tables, and every existing
consumer still identifies a scope by the host's free-text code. This is the
bridge between the two, and it exists on its own - with no consumer - so that
the consumers can migrate one at a time, each with its own commit and test.

The result has THREE states on purpose. Spec section 7 ends the transition by
turning "did not resolve" into an error; a two-state result would turn
PLATFORM and SCHOOL scopes into errors too, and those legitimately point at no
hierarchy entity at all. Keeping the distinction now makes that step a one-line
change instead of an excavation.
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.academic import Class, GradeLevel, Segment, SchoolUnit


class ResolutionState(enum.Enum):
    RESOLVED = "RESOLVED"
    NOT_FOUND = "NOT_FOUND"
    NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass(frozen=True)
class ScopeResolution:
    state: ResolutionState
    entity: object | None = None

    @property
    def entity_id(self) -> uuid.UUID | None:
        return getattr(self.entity, "id", None)


# Scope types that address a row in the academic hierarchy, and the model each
# one addresses. PLATFORM and SCHOOL are deliberately absent: they are valid
# scopes that point at no hierarchy entity.
_SCOPE_MODELS = {
    "UNIT": SchoolUnit,
    "SEGMENT": Segment,
    "GRADE_LEVEL": GradeLevel,
    "CLASSROOM": Class,
}

_SCOPES_WITHOUT_ENTITY = {"PLATFORM", "SCHOOL"}


class ExternalIdResolver:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def resolve(
        self,
        school_id: uuid.UUID | str,
        scope_type: str,
        external_id: str | None,
    ) -> ScopeResolution:
        """Find the entity a scope addresses, within one school.

        Returns NOT_APPLICABLE for scopes that address no hierarchy entity,
        NOT_FOUND when one is addressed but absent, and RESOLVED otherwise.
        Raises ValueError for a scope type that does not exist at all - that is
        a programming error, and folding it into NOT_FOUND would read to a
        caller as "no such class".
        """
        normalized = (scope_type or "").upper()

        if normalized in _SCOPES_WITHOUT_ENTITY:
            return ScopeResolution(ResolutionState.NOT_APPLICABLE)

        model = _SCOPE_MODELS.get(normalized)
        if model is None:
            raise ValueError(f"unknown scope type: {scope_type!r}")

        code = (external_id or "").strip()
        if not code:
            return ScopeResolution(ResolutionState.NOT_FOUND)

        result = await self.session.execute(
            select(model).where(
                model.school_id == school_id,
                model.external_id == code,
            )
        )
        entity = result.scalars().first()
        if entity is None:
            return ScopeResolution(ResolutionState.NOT_FOUND)
        return ScopeResolution(ResolutionState.RESOLVED, entity)


__all__ = ["ExternalIdResolver", "ResolutionState", "ScopeResolution"]
