# tests/test_coordination_portal_no_fabricated_data.py
"""Three fabricated-data bugs in CoordinationPortalService, all found by manual
review of coordination_portal.py (2026-09-25):

1. list_coordination_teachers invents an entire teacher ("Prof. Mendes", 25
   students, 64.0% mastery) when a school has ZERO real teacher links, instead
   of returning an empty list.

2. get_coordinator_authorized_scopes's is_global branch always returns
   hardcoded allowed_grades={"1ª Série","2ª Série","3ª Série"},
   allowed_units={"Unidade Principal"}, allowed_segments={"Ensino Médio"} -
   regardless of what the school's real R0 hierarchy (GradeLevel/SchoolUnit/
   Segment) actually contains.

3. get_coordination_hierarchy always wraps every classroom under one
   fabricated unit (unit_id="MAIN_UNIT", "Unidade Principal") and one
   fabricated segment (segment_id="MEDIO", "Ensino Médio"), never the school's
   real SchoolUnit/Segment/GradeLevel rows - even when they exist.

Each class below reproduces the bug against a school seeded with REAL R0
academic-hierarchy rows (SchoolUnit/Segment/GradeLevel/Class), following the
same sqlite in-memory + _seed_hierarchy pattern already used in
tests/test_r0_scope_validation_coordination.py.
"""

import unittest
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import School
from agente_ia_edu.db.models.academic import AcademicYear, Class, GradeLevel, Segment, SchoolUnit
from agente_ia_edu.services.admin import AdminRole, AdminScopeType, PlatformAdminService
from agente_ia_edu.services.coordination_portal import CoordinationPortalService
from agente_ia_edu.services.knowledge import KnowledgeService
from agente_ia_edu.services.recommendation import RecommendationEngine
from agente_ia_edu.services.teacher_portal import TeacherPortalService
from agente_ia_edu.services.teaching_context import TeachingContextService
from agente_ia_edu.services.video_engine import VideoRecommendationEngine


class CoordinationPortalNoFabricatedDataTests(unittest.IsolatedAsyncioTestCase):
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

    async def _school(self, session, code):
        school = School(id=uuid.uuid4(), code=f"SCH-{code}", name=f"Escola {code}")
        session.add(school)
        await session.commit()
        return school

    def _full_portal(self, session):
        ks = KnowledgeService(session)
        t_svc = TeachingContextService(session)
        rec_eng = RecommendationEngine(session, ks)
        vid_eng = VideoRecommendationEngine(session, ks)
        t_portal = TeacherPortalService(session, ks, t_svc, rec_eng, vid_eng)
        return CoordinationPortalService(session, ks, t_svc, t_portal, rec_eng)

    async def _seed_hierarchy(self, session, school, *, with_unit: bool, code: str):
        """Mirrors scripts/seed_escola_abc.py's real R0 shape: a Segment, a
        GradeLevel inside it, and Classes inside that - optionally with a real
        SchoolUnit too, to cover both "unit exists" and "no unit registered
        yet" (Escola ABC's real, current state)."""
        unit = None
        if with_unit:
            unit = SchoolUnit(id=uuid.uuid4(), school_id=school.id, name="Campus Principal", external_id=f"UNIT-{code}")
            session.add(unit)
            await session.flush()

        segment = Segment(id=uuid.uuid4(), school_id=school.id, name="Ensino Médio", external_id=f"MEDIO-{code}")
        session.add(segment)
        await session.flush()

        grade = GradeLevel(
            id=uuid.uuid4(), school_id=school.id, segment_id=segment.id,
            name="1ª Série", external_id=f"1_SERIE-{code}",
        )
        year = AcademicYear(id=uuid.uuid4(), school_id=school.id, year=2026, external_id=f"YEAR-{code}")
        session.add_all([grade, year])
        await session.flush()

        klass = Class(
            id=uuid.uuid4(), school_id=school.id, academic_year_id=year.id,
            grade_level_id=grade.id, school_unit_id=unit.id if unit else None,
            name="Turma A", external_id=f"TURMA-{code}",
        )
        session.add(klass)
        await session.commit()
        return unit, segment, grade, klass

    # ---------------------------------------------------------------------
    # Problem 1: list_coordination_teachers fabricated "Prof. Mendes"
    # ---------------------------------------------------------------------

    async def test_no_teachers_linked_returns_empty_list_not_a_fabricated_teacher(self):
        async with self.session_factory() as session:
            school = await self._school(session, "no-teachers")
            admin = PlatformAdminService(session)
            # A real, school-wide coordinator - but NO teacher link at all.
            await admin.link_user_to_school(
                performed_by_external_id="setup",
                external_user_id="coord-lonely",
                role=AdminRole.COORDINATOR,
                scope_type=AdminScopeType.SCHOOL,
                school_id=school.id,
            )

            portal = self._full_portal(session)
            teachers = await portal.list_coordination_teachers(
                coordinator_id="coord-lonely",
                school_id=school.id,
            )

        self.assertEqual(teachers, [])

    # ---------------------------------------------------------------------
    # Problem 2: get_coordinator_authorized_scopes hardcoded is_global scopes
    # ---------------------------------------------------------------------

    async def test_global_coordinator_gets_real_grades_segments_units_not_hardcoded(self):
        async with self.session_factory() as session:
            school = await self._school(session, "real-hierarchy")
            await self._seed_hierarchy(session, school, with_unit=True, code="A")
            admin = PlatformAdminService(session)
            await admin.link_user_to_school(
                performed_by_external_id="setup",
                external_user_id="coord-global-real",
                role=AdminRole.COORDINATOR,
                scope_type=AdminScopeType.SCHOOL,
                school_id=school.id,
            )

            portal = self._full_portal(session)
            scopes = await portal.get_coordinator_authorized_scopes("coord-global-real", school.id)

        self.assertTrue(scopes["is_global"])
        # The school's REAL grade/segment/unit external_ids - not the
        # hardcoded "1ª Série"/"Ensino Médio"/"Unidade Principal" strings.
        self.assertEqual(scopes["allowed_grades"], {"1_SERIE-A"})
        self.assertEqual(scopes["allowed_segments"], {"MEDIO-A"})
        self.assertEqual(scopes["allowed_units"], {"UNIT-A"})

    async def test_global_coordinator_at_school_with_no_hierarchy_yet_gets_empty_sets(self):
        """No GradeLevel/Segment/SchoolUnit rows exist for this school at all
        (a legacy, pre-R0 school, or a fresh one like Escola ABC's units) -
        the hardcoded fallback must not paper over that with invented
        values; an empty set is what get_coordinator_authorized_scopes'
        allowed_grades/allowed_units are actually checked against downstream
        (lines 251/256), and an empty set there correctly denies a filter
        for a level the school has no real data for."""
        async with self.session_factory() as session:
            school = await self._school(session, "no-hierarchy")
            admin = PlatformAdminService(session)
            await admin.link_user_to_school(
                performed_by_external_id="setup",
                external_user_id="coord-global-empty",
                role=AdminRole.COORDINATOR,
                scope_type=AdminScopeType.SCHOOL,
                school_id=school.id,
            )

            portal = self._full_portal(session)
            scopes = await portal.get_coordinator_authorized_scopes("coord-global-empty", school.id)

        self.assertTrue(scopes["is_global"])
        self.assertEqual(scopes["allowed_grades"], set())
        self.assertEqual(scopes["allowed_units"], set())
        self.assertEqual(scopes["allowed_segments"], set())

    # ---------------------------------------------------------------------
    # Problem 3: get_coordination_hierarchy fabricated "MAIN_UNIT"/"MEDIO"
    # ---------------------------------------------------------------------

    async def test_hierarchy_reflects_real_school_unit_and_segment_when_they_exist(self):
        async with self.session_factory() as session:
            school = await self._school(session, "hier-real")
            unit, segment, grade, klass = await self._seed_hierarchy(
                session, school, with_unit=True, code="B"
            )
            admin = PlatformAdminService(session)
            await admin.link_user_to_school(
                performed_by_external_id="setup",
                external_user_id="coord-hier-real",
                role=AdminRole.COORDINATOR,
                scope_type=AdminScopeType.CLASSROOM,
                school_id=school.id,
                scope_external_id="TURMA-B",
            )

            portal = self._full_portal(session)
            hierarchy = await portal.get_coordination_hierarchy(
                coordinator_id="coord-hier-real",
                school_id=school.id,
            )

        units = hierarchy["units"]
        self.assertEqual(len(units), 1)
        self.assertEqual(units[0]["unit_id"], str(unit.id))
        self.assertEqual(units[0]["unit_name"], "Campus Principal")
        segments = units[0]["segments"]
        self.assertEqual(segments[0]["segment_id"], str(segment.id))
        self.assertEqual(segments[0]["segment_name"], "Ensino Médio")
        self.assertEqual(segments[0]["grades"][0]["grade_level"], "1ª Série")

    async def test_hierarchy_groups_classrooms_with_no_real_unit_under_an_explicit_bucket(self):
        """Escola ABC's real, current shape: Segment/GradeLevel/Class exist,
        but no SchoolUnit was ever created. The tree must say so honestly -
        never fabricate "Unidade Principal" as if it were a real row."""
        async with self.session_factory() as session:
            school = await self._school(session, "hier-no-unit")
            unit, segment, grade, klass = await self._seed_hierarchy(
                session, school, with_unit=False, code="C"
            )
            admin = PlatformAdminService(session)
            await admin.link_user_to_school(
                performed_by_external_id="setup",
                external_user_id="coord-hier-no-unit",
                role=AdminRole.COORDINATOR,
                scope_type=AdminScopeType.CLASSROOM,
                school_id=school.id,
                scope_external_id="TURMA-C",
            )

            portal = self._full_portal(session)
            hierarchy = await portal.get_coordination_hierarchy(
                coordinator_id="coord-hier-no-unit",
                school_id=school.id,
            )

        units = hierarchy["units"]
        self.assertEqual(len(units), 1)
        self.assertIsNone(units[0]["unit_id"])
        self.assertNotEqual(units[0]["unit_name"], "Unidade Principal")
        # The real segment/grade must still come through even without a unit.
        segments = units[0]["segments"]
        self.assertEqual(segments[0]["segment_name"], "Ensino Médio")
        self.assertEqual(segments[0]["grades"][0]["grade_level"], "1ª Série")

    async def test_hierarchy_with_no_hierarchy_data_at_all_does_not_fabricate_main_unit(self):
        """No Class row resolves at all (classic pre-R0 school, classroom_id
        is a bare string with nothing behind it) - must not claim
        "MAIN_UNIT"/"Unidade Principal"/"MEDIO"/"Ensino Médio" as if real."""
        async with self.session_factory() as session:
            school = await self._school(session, "hier-legacy")
            admin = PlatformAdminService(session)
            await admin.link_user_to_school(
                performed_by_external_id="setup",
                external_user_id="coord-hier-legacy",
                role=AdminRole.COORDINATOR,
                scope_type=AdminScopeType.CLASSROOM,
                school_id=school.id,
                scope_external_id="TURMA_LEGACY",
            )

            portal = self._full_portal(session)
            hierarchy = await portal.get_coordination_hierarchy(
                coordinator_id="coord-hier-legacy",
                school_id=school.id,
            )

        units = hierarchy["units"]
        self.assertEqual(len(units), 1)
        self.assertIsNone(units[0]["unit_id"])
        self.assertNotEqual(units[0]["unit_name"], "Unidade Principal")
        segments = units[0]["segments"]
        self.assertIsNone(segments[0]["segment_id"])
        self.assertNotEqual(segments[0]["segment_name"], "Ensino Médio")


if __name__ == "__main__":
    unittest.main()
