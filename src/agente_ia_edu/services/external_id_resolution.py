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

The scope vocabulary here is the whole codebase's, not just the one table's.
``user_school_links.scope_type`` has a CHECK with six values, but the other two
tables carrying the ``scope_type``/``scope_external_id`` pair do not:
``study_sessions.scope_type`` is nullable and also stores ``STUDENT``
(``services/study_session.py`` writes it), and ``assessments.scope_type`` is
nullable with no CHECK at all. ``STUDENT`` and an absent scope type are real
data, not typos, and they address no entity in the academic hierarchy - which
is exactly what NOT_APPLICABLE means. A value no table produces still raises,
because folding a typo into a data state hides it.
"""

from __future__ import annotations

import enum
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.academic import Class, GradeLevel, Segment, SchoolUnit

# The four hierarchy rows a scope can address. Naming the union instead of
# ``object`` gives a caller a type with an ``id`` on it.
AcademicScopeEntity = SchoolUnit | Segment | GradeLevel | Class


class ResolutionState(enum.Enum):
    RESOLVED = "RESOLVED"
    NOT_FOUND = "NOT_FOUND"
    NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass(frozen=True)
class ScopeResolution:
    state: ResolutionState
    entity: AcademicScopeEntity | None = None

    @property
    def entity_id(self) -> uuid.UUID | None:
        # Not ``getattr(..., "id", None)``: an entity without an id would come
        # back looking like the "did not resolve" path of the spec section 7
        # transition, and a caller would take the wrong branch in silence.
        return self.entity.id if self.entity is not None else None


# Scope types that address a row in the academic hierarchy, and the model each
# one addresses. PLATFORM, SCHOOL and STUDENT are deliberately absent: they are
# valid scopes that point at no hierarchy entity.
_SCOPE_MODELS: Mapping[str, type[AcademicScopeEntity]] = MappingProxyType(
    {
        "UNIT": SchoolUnit,
        "SEGMENT": Segment,
        "GRADE_LEVEL": GradeLevel,
        "CLASSROOM": Class,
    }
)

# Real scope types that address no entity in the academic hierarchy. PLATFORM
# and SCHOOL come from the ``user_school_links`` CHECK; STUDENT is written by
# ``services/study_session.py`` into ``study_sessions.scope_type``. An absent
# scope type (None, or blank) is the same data state and is treated the same.
_SCOPES_WITHOUT_ENTITY = frozenset({"PLATFORM", "SCHOOL", "STUDENT"})

# Where the resolved entity's id belongs on ``user_school_links``. The mapping
# from scope type to entity already lives here; without this one, every
# consumer that populates the bridge re-derives the same four-branch if/elif
# and the copies drift from the canonical map the day a level is added.
#
# ``user_id`` is a bridge column and is deliberately NOT here: there is no
# USER scope type, and that column resolves from ``external_user_id`` against a
# three-part key that includes ``external_identity_provider`` - a different
# resolver, for a later sub-phase.
SCOPE_BRIDGE_COLUMNS: Mapping[str, str] = MappingProxyType(
    {
        "UNIT": "school_unit_id",
        "SEGMENT": "segment_id",
        "GRADE_LEVEL": "grade_level_id",
        "CLASSROOM": "class_id",
    }
)


def _model_for(scope_type: str | None) -> type[AcademicScopeEntity] | None:
    """The model a scope type addresses, or None when it addresses none.

    Raises ValueError for a value no table produces - that is a programming
    error, and a data state would hide it.
    """
    normalized = (scope_type or "").strip().upper()
    if not normalized or normalized in _SCOPES_WITHOUT_ENTITY:
        return None
    model = _SCOPE_MODELS.get(normalized)
    if model is None:
        raise ValueError(f"unknown scope type: {scope_type!r}")
    return model


def bridge_column(scope_type: str | None) -> str | None:
    """The ``user_school_links`` column that holds this scope's resolved id.

    None for a scope that addresses no hierarchy entity, and the same ValueError
    as :meth:`ExternalIdResolver.resolve` for a scope type that does not exist.
    """
    if _model_for(scope_type) is None:
        return None
    return SCOPE_BRIDGE_COLUMNS[(scope_type or "").strip().upper()]


class ExternalIdResolver:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def resolve(
        self,
        school_id: uuid.UUID | str | None,
        scope_type: str | None,
        external_id: str | None,
    ) -> ScopeResolution:
        """Find the entity a scope addresses, within one school.

        Returns NOT_APPLICABLE for scopes that address no hierarchy entity
        (PLATFORM, SCHOOL, STUDENT, and an absent scope type), NOT_FOUND when
        one is addressed but absent, and RESOLVED otherwise. Raises ValueError
        for a scope type that does not exist at all - that is a programming
        error, and folding it into NOT_FOUND would read to a caller as "no such
        class".

        ``school_id`` is optional because every realistic caller holds one:
        ``user_school_links.school_id`` is nullable and
        ``AuthenticatedUserContext.school_id`` is optional. A None school never
        matches a row, so a hierarchy scope with no school is NOT_FOUND.
        """
        model = _model_for(scope_type)
        if model is None:
            return ScopeResolution(ResolutionState.NOT_APPLICABLE)

        code = (external_id or "").strip()
        if not code:
            return ScopeResolution(ResolutionState.NOT_FOUND)

        result = await self.session.execute(
            select(model).where(
                model.school_id == school_id,
                model.external_id == code,
            )
        )
        # scalar_one_or_none, not first: uq_<table>_school_external_id makes
        # this at most one row, so a second one means that uniqueness is gone
        # and picking a row at random would be the worst possible answer. The
        # empty-code guard above keeps this away from external_id IS NULL,
        # where the unique constraint does not hold (distinct NULLs).
        entity = result.scalars().one_or_none()
        if entity is None:
            return ScopeResolution(ResolutionState.NOT_FOUND)
        return ScopeResolution(ResolutionState.RESOLVED, entity)

    async def resolve_many(
        self,
        school_id: uuid.UUID | str | None,
        scope_type: str | None,
        external_ids: Iterable[str | None],
    ) -> dict[str | None, ScopeResolution]:
        """Resolve many codes of one scope type, within one school, in one query.

        The consumers that migrate next are set-shaped, not row-shaped:
        ``services/coordination_portal.py`` builds a set per level out of a
        user's links, and ``services/teacher_portal.py`` already filters with
        ``scope_external_id.in_(classrooms)``. Through :meth:`resolve` those
        become N sequential round-trips where today there are none.

        Returns one entry per code given, keyed by the code exactly as passed -
        so a caller can look up ``link.scope_external_id`` without normalizing
        first, and never has to handle a missing key. Values are the same
        ScopeResolution :meth:`resolve` returns, and the two agree on every
        input. Duplicates in the input collapse onto one key, which is what a
        caller holding a set wants anyway.
        """
        codes = list(external_ids)
        model = _model_for(scope_type)
        if model is None:
            return {code: ScopeResolution(ResolutionState.NOT_APPLICABLE) for code in codes}

        resolutions: dict[str | None, ScopeResolution] = {}
        wanted: dict[str, list[str | None]] = {}
        for original in codes:
            code = (original or "").strip()
            if not code:
                resolutions[original] = ScopeResolution(ResolutionState.NOT_FOUND)
            else:
                wanted.setdefault(code, []).append(original)

        if not wanted:
            return resolutions

        result = await self.session.execute(
            select(model).where(
                model.school_id == school_id,
                model.external_id.in_(wanted),
            )
        )
        found = {entity.external_id: entity for entity in result.scalars().all()}
        for code, originals in wanted.items():
            entity = found.get(code)
            resolution = (
                ScopeResolution(ResolutionState.RESOLVED, entity)
                if entity is not None
                else ScopeResolution(ResolutionState.NOT_FOUND)
            )
            for original in originals:
                resolutions[original] = resolution
        return resolutions

    async def has_any_entities(
        self, school_id: uuid.UUID | str | None, scope_type: str | None
    ) -> bool:
        """Whether this school has at least one real row at this hierarchy level.

        R0's hierarchy tables are additive and new: nothing migrated existing
        production data into them. A consumer that validates scope codes
        against these tables must check this first - filtering a level that
        was never populated would strip access from every school that has not
        been backfilled yet, which today is every school.
        """
        model = _model_for(scope_type)
        if model is None:
            return False
        result = await self.session.execute(
            select(model.id).where(model.school_id == school_id).limit(1)
        )
        return result.scalar_one_or_none() is not None


async def real_classroom_external_ids(
    session: AsyncSession, school_id: uuid.UUID | str | None
) -> list[str]:
    """Every real ``Class`` row's ``external_id`` for one school, sorted for a
    stable, deterministic order.

    This is the R0 hierarchy's own source of truth for "what classrooms does
    this school actually have". teacher_portal.py and coordination_portal.py
    both used to fall back to a hardcoded ``["TURMA_3A", "TURMA_3B"]`` demo
    placeholder whenever neither TeachingLesson history nor a CLASSROOM-scoped
    UserSchoolLink existed yet for a school-wide teacher/coordinator - which
    fired for any real school before its first lesson or scope link was
    recorded, inventing classroom names that do not exist (reported live
    against a real school whose actual classrooms are named "1EM_A"/"1EM_B").
    This function replaces that placeholder: it is unioned into the same
    classroom-resolution queries so a school-wide caller sees the school's
    real classrooms instead. A school with zero Class rows yields ``[]`` -
    this function must never return anything but real rows.
    """
    stmt = (
        select(Class.external_id)
        .where(Class.school_id == school_id, Class.external_id.isnot(None))
        .distinct()
        .order_by(Class.external_id)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


__all__ = [
    "AcademicScopeEntity",
    "ExternalIdResolver",
    "ResolutionState",
    "SCOPE_BRIDGE_COLUMNS",
    "ScopeResolution",
    "bridge_column",
    "real_classroom_external_ids",
]
