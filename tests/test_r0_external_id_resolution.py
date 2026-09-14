# tests/test_r0_external_id_resolution.py
"""Translating a scope's external id into a real entity.

Three states, not two. "This scope never points at a hierarchy entity" is not
the same as "it should and I could not find it" - and spec section 7 turns the
second into an error while the first must stay legal, or every platform
administrator stops working on the day that lands.
"""

import unittest
import uuid

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models.academic import AcademicYear, Class, GradeLevel, Segment, SchoolUnit
from agente_ia_edu.db.models.admin import UserSchoolLink
from agente_ia_edu.services.external_id_resolution import (
    SCOPE_BRIDGE_COLUMNS,
    ExternalIdResolver,
    ResolutionState,
    bridge_column,
)

SCHOOL_A = uuid.uuid4()
SCHOOL_B = uuid.uuid4()


class _ResolverTestCase(unittest.IsolatedAsyncioTestCase):
    """One in-memory database per test, seeded one row deep per level."""

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


class ExternalIdResolutionTests(_ResolverTestCase):
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

    async def test_scopes_the_other_tables_produce_are_data_not_crashes(self):
        """user_school_links has a CHECK with six values; the other two tables
        carrying scope_type do not. study_sessions stores STUDENT and is
        nullable, assessments is nullable with no CHECK at all. Those are real
        scopes addressing no hierarchy entity - which is NOT_APPLICABLE, not an
        unhandled exception on a read path."""
        async with self.session_factory() as session:
            resolver = ExternalIdResolver(session)
            for scope_type in ("STUDENT", "student", None, "", "   "):
                with self.subTest(scope_type=repr(scope_type)):
                    found = await resolver.resolve(SCHOOL_A, scope_type, "ALUNO-1")
                    self.assertEqual(found.state, ResolutionState.NOT_APPLICABLE)
                    self.assertIsNone(found.entity_id)


class ScopeBridgeColumnTests(unittest.TestCase):
    """The map from scope type to the user_school_links column that holds the
    resolved id. Asserted against the model's real columns, because a map that
    says class_id when the column is named otherwise is worse than no map."""

    def test_every_bridge_column_exists_on_the_model(self):
        columns = set(UserSchoolLink.__table__.columns.keys())
        for scope_type, column in SCOPE_BRIDGE_COLUMNS.items():
            with self.subTest(scope_type=scope_type):
                self.assertIn(column, columns)

    def test_the_map_covers_exactly_the_scopes_that_address_an_entity(self):
        """Every scope with an entity has a column, and no scope without one
        does. Drift either way puts an id in the wrong column."""
        self.assertEqual(
            set(SCOPE_BRIDGE_COLUMNS), {"UNIT", "SEGMENT", "GRADE_LEVEL", "CLASSROOM"}
        )
        for scope_type in ("PLATFORM", "SCHOOL", "STUDENT", None, ""):
            with self.subTest(scope_type=repr(scope_type)):
                self.assertIsNone(bridge_column(scope_type))

    def test_the_accessor_normalizes_and_rejects_like_resolve_does(self):
        self.assertEqual(bridge_column("classroom"), "class_id")
        self.assertEqual(bridge_column(" GRADE_LEVEL "), "grade_level_id")
        with self.assertRaises(ValueError):
            bridge_column("TURMA")

    def test_user_id_is_not_in_the_map_though_it_is_a_bridge_column(self):
        """It resolves from external_user_id against a three-part key that
        includes external_identity_provider - a different resolver, later."""
        self.assertIn("user_id", UserSchoolLink.__table__.columns.keys())
        self.assertNotIn("user_id", set(SCOPE_BRIDGE_COLUMNS.values()))


class BatchResolutionTests(_ResolverTestCase):
    """Batch resolution, and the property that matters most about it: the two
    routes must never disagree, or migrating a set-shaped consumer changes its
    answers as well as its shape."""

    async def test_many_codes_resolve_in_a_single_query(self):
        selects = []

        @event.listens_for(self.engine.sync_engine, "before_cursor_execute")
        def _count(conn, cursor, statement, parameters, context, executemany):
            if statement.lstrip().upper().startswith("SELECT"):
                selects.append(statement)

        async with self.session_factory() as session:
            seeded = await self._seed(session, SCHOOL_A)
            resolver = ExternalIdResolver(session)
            selects.clear()
            found = await resolver.resolve_many(
                SCHOOL_A, "CLASSROOM", {"TURMA-1", "TURMA-QUE-NAO-EXISTE"}
            )

        self.assertEqual(len(selects), 1, f"esperava uma consulta só, houve {len(selects)}")
        self.assertEqual(found["TURMA-1"].entity_id, seeded["class"].id)
        self.assertEqual(found["TURMA-QUE-NAO-EXISTE"].state, ResolutionState.NOT_FOUND)

    async def test_the_result_has_one_entry_per_code_given_keyed_as_given(self):
        """A caller looks up link.scope_external_id directly - no normalizing
        first, and no missing key to handle."""
        async with self.session_factory() as session:
            await self._seed(session, SCHOOL_A)
            resolver = ExternalIdResolver(session)
            codes = ["UNIT-1", " UNIT-1 ", "NAO-EXISTE", None, ""]
            found = await resolver.resolve_many(SCHOOL_A, "UNIT", codes)

        self.assertEqual(set(found), set(codes))
        self.assertEqual(found["UNIT-1"].state, ResolutionState.RESOLVED)
        self.assertEqual(found[" UNIT-1 "].entity_id, found["UNIT-1"].entity_id)
        self.assertEqual(found["NAO-EXISTE"].state, ResolutionState.NOT_FOUND)
        self.assertEqual(found[None].state, ResolutionState.NOT_FOUND)

    async def test_both_routes_give_the_same_answer_for_the_same_input(self):
        """The single-code path must keep behaving exactly as it does, and the
        batch path must agree with it on every case there is."""
        cases = [
            ("UNIT", "UNIT-1"),
            ("SEGMENT", "SEG-1"),
            ("GRADE_LEVEL", "GRADE-1"),
            ("CLASSROOM", "TURMA-1"),
            ("CLASSROOM", "TURMA-QUE-NAO-EXISTE"),
            ("classroom", "TURMA-1"),
            ("CLASSROOM", None),
            ("CLASSROOM", ""),
            ("CLASSROOM", "   "),
            ("PLATFORM", "qualquer"),
            ("SCHOOL", "qualquer"),
            ("STUDENT", "ALUNO-1"),
            (None, "ALUNO-1"),
        ]
        async with self.session_factory() as session:
            await self._seed(session, SCHOOL_A)
            resolver = ExternalIdResolver(session)
            for scope_type, code in cases:
                with self.subTest(scope_type=repr(scope_type), code=repr(code)):
                    one = await resolver.resolve(SCHOOL_A, scope_type, code)
                    many = await resolver.resolve_many(SCHOOL_A, scope_type, [code])
                    self.assertEqual(many[code].state, one.state)
                    self.assertEqual(many[code].entity_id, one.entity_id)

    async def test_an_unknown_scope_type_is_rejected_loudly_in_batch_too(self):
        async with self.session_factory() as session:
            resolver = ExternalIdResolver(session)
            with self.assertRaises(ValueError):
                await resolver.resolve_many(SCHOOL_A, "TURMA", ["TURMA-1"])

    async def test_no_codes_asks_the_database_nothing(self):
        selects = []

        @event.listens_for(self.engine.sync_engine, "before_cursor_execute")
        def _count(conn, cursor, statement, parameters, context, executemany):
            selects.append(statement)

        async with self.session_factory() as session:
            resolver = ExternalIdResolver(session)
            self.assertEqual(await resolver.resolve_many(SCHOOL_A, "CLASSROOM", []), {})
        self.assertEqual(selects, [])

    async def test_batch_never_crosses_a_school_boundary_either(self):
        async with self.session_factory() as session:
            a = await self._seed(session, SCHOOL_A)
            b = await self._seed(session, SCHOOL_B)
            resolver = ExternalIdResolver(session)
            for_a = await resolver.resolve_many(SCHOOL_A, "CLASSROOM", ["TURMA-1"])
            for_b = await resolver.resolve_many(SCHOOL_B, "CLASSROOM", ["TURMA-1"])

        self.assertEqual(for_a["TURMA-1"].entity_id, a["class"].id)
        self.assertEqual(for_b["TURMA-1"].entity_id, b["class"].id)


if __name__ == "__main__":
    unittest.main()
