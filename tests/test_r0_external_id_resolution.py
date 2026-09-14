# tests/test_r0_external_id_resolution.py
"""Translating a scope's external id into a real entity.

Three states, not two. "This scope never points at a hierarchy entity" is not
the same as "it should and I could not find it" - and spec section 7 turns the
second into an error while the first must stay legal, or every platform
administrator stops working on the day that lands.
"""

import unittest
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models.academic import AcademicYear, Class, GradeLevel, Segment, SchoolUnit
from agente_ia_edu.services.external_id_resolution import (
    ExternalIdResolver,
    ResolutionState,
)

SCHOOL_A = uuid.uuid4()
SCHOOL_B = uuid.uuid4()


class ExternalIdResolutionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", echo=False, poolclass=StaticPool
        )
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _seed(self, session, school_id, suffix=""):
        """One row of each hierarchy level, external ids suffixed per school."""
        unit = SchoolUnit(
            id=uuid.uuid4(), school_id=school_id, name=f"unit{suffix}",
            external_id=f"UNIT-1{suffix}",
        )
        segment = Segment(
            id=uuid.uuid4(), school_id=school_id, name=f"segment{suffix}",
            external_id=f"SEG-1{suffix}",
        )
        session.add_all([unit, segment])
        await session.flush()
        grade = GradeLevel(
            id=uuid.uuid4(), school_id=school_id, segment_id=segment.id,
            name=f"grade{suffix}", external_id=f"GRADE-1{suffix}",
        )
        year = AcademicYear(
            id=uuid.uuid4(), school_id=school_id, year=2026, external_id=f"YEAR{suffix}",
        )
        session.add_all([grade, year])
        await session.flush()
        klass = Class(
            id=uuid.uuid4(), school_id=school_id, academic_year_id=year.id,
            grade_level_id=grade.id, name=f"class{suffix}",
            external_id=f"TURMA-1{suffix}",
        )
        session.add(klass)
        await session.commit()
        return {"unit": unit, "segment": segment, "grade": grade, "class": klass}

    async def test_each_hierarchy_scope_resolves_to_its_own_entity(self):
        async with self.session_factory() as session:
            seeded = await self._seed(session, SCHOOL_A)
            resolver = ExternalIdResolver(session)

            cases = [
                ("UNIT", "UNIT-1", seeded["unit"]),
                ("SEGMENT", "SEG-1", seeded["segment"]),
                ("GRADE_LEVEL", "GRADE-1", seeded["grade"]),
                ("CLASSROOM", "TURMA-1", seeded["class"]),
            ]
            for scope_type, external_id, expected in cases:
                with self.subTest(scope_type=scope_type):
                    found = await resolver.resolve(SCHOOL_A, scope_type, external_id)
                    self.assertEqual(found.state, ResolutionState.RESOLVED)
                    self.assertEqual(found.entity_id, expected.id)

    async def test_platform_and_school_are_not_applicable_not_missing(self):
        """The distinction spec section 7 depends on: these scopes legitimately
        point at no hierarchy entity, so they must never look like absent data."""
        async with self.session_factory() as session:
            resolver = ExternalIdResolver(session)
            for scope_type in ("PLATFORM", "SCHOOL"):
                with self.subTest(scope_type=scope_type):
                    found = await resolver.resolve(SCHOOL_A, scope_type, "anything")
                    self.assertEqual(found.state, ResolutionState.NOT_APPLICABLE)
                    self.assertIsNone(found.entity_id)

    async def test_an_unknown_code_is_not_found(self):
        async with self.session_factory() as session:
            await self._seed(session, SCHOOL_A)
            resolver = ExternalIdResolver(session)
            found = await resolver.resolve(SCHOOL_A, "CLASSROOM", "TURMA-QUE-NAO-EXISTE")
        self.assertEqual(found.state, ResolutionState.NOT_FOUND)
        self.assertIsNone(found.entity_id)

    async def test_resolution_never_crosses_a_school_boundary(self):
        """Two schools, the same external id. Resolving for one must never
        return the other's row - this is the whole reason external_id is unique
        per school rather than per year or per segment."""
        async with self.session_factory() as session:
            a = await self._seed(session, SCHOOL_A)
            b = await self._seed(session, SCHOOL_B)
            resolver = ExternalIdResolver(session)

            for_a = await resolver.resolve(SCHOOL_A, "CLASSROOM", "TURMA-1")
            for_b = await resolver.resolve(SCHOOL_B, "CLASSROOM", "TURMA-1")

        self.assertEqual(for_a.entity_id, a["class"].id)
        self.assertEqual(for_b.entity_id, b["class"].id)
        self.assertNotEqual(for_a.entity_id, for_b.entity_id)

    async def test_an_empty_or_missing_code_is_not_found_not_a_crash(self):
        async with self.session_factory() as session:
            resolver = ExternalIdResolver(session)
            for external_id in (None, "", "   "):
                with self.subTest(external_id=repr(external_id)):
                    found = await resolver.resolve(SCHOOL_A, "CLASSROOM", external_id)
                    self.assertEqual(found.state, ResolutionState.NOT_FOUND)

    async def test_an_unknown_scope_type_is_rejected_loudly(self):
        """A scope type outside the CHECK constraint is a programming error, not
        a data condition - it must not be silently folded into NOT_FOUND, which
        a caller would read as 'no such class'."""
        async with self.session_factory() as session:
            resolver = ExternalIdResolver(session)
            with self.assertRaises(ValueError):
                await resolver.resolve(SCHOOL_A, "TURMA", "TURMA-1")


if __name__ == "__main__":
    unittest.main()
