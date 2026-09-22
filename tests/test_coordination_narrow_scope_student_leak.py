# tests/test_coordination_narrow_scope_student_leak.py
"""GET /api/v1/coordination/students/{student_id} and GET /api/v1/coordination/search
only checked that the caller holds a COORDINATOR/DIRECTOR/PLATFORM_ADMIN link at the
requested school_id (verify_coordinator_access with no classroom_id) before delegating
straight into TeacherPortalService.get_student_detail_for_teacher / search_students_in_scope.
Those reuse get_teacher_authorized_classrooms, whose DIRECTOR/COORDINATOR branch treats
ANY such link at the right school as fully school-wide, regardless of the link's own
scope_type - so a coordinator whose UserSchoolLink is narrowly scoped to one CLASSROOM
(exactly what get_coordination_dashboard's classroom_id filter DOES respect - see
test_coordination_portal.py::test_15_16_17_18_..._and_unauthorized_blocking) could still
read the full detail of, and find via search, any student of ANY OTHER classroom in the
same school. Live-confirmed against a running dev server (real Postgres, two real
classrooms in one school): a coordinator scoped to TURMA_B1_PROBE got a 200 with full
mastery/recommendation detail for a student in TURMA_B2_PROBE_NOT_COORDS, and that
student turned up in an unfiltered /search too.

Fixed by giving CoordinationPortalService its own student-scope check
(verify_coordinator_student_scope) and its own scope-aware search
(search_students_in_scope), both built on get_coordinator_authorized_scopes /
_resolve_scope_classrooms - the same primitives get_coordination_dashboard,
get_coordination_hierarchy and compare_classrooms already use - instead of reusing the
teacher-shaped, always-school-wide-for-DIRECTOR/COORDINATOR helper.
"""

import unittest
import uuid

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.routes.coordination_portal import (
    get_student_detail_for_coordination,
    search_students_for_coordination,
)
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import School
from agente_ia_edu.identity import ExternalIdentityContext
from agente_ia_edu.services.admin import AdminRole, AdminScopeType, PlatformAdminService


class CoordinatorNarrowScopeStudentLeakTests(unittest.IsolatedAsyncioTestCase):
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

    async def _seed(self, session):
        school = School(id=uuid.uuid4(), code="SCH-X", name="school-x")
        session.add(school)
        await session.commit()

        admin = PlatformAdminService(session)
        await admin.link_user_to_school(
            performed_by_external_id="setup",
            external_user_id="coord-narrow",
            role=AdminRole.COORDINATOR,
            scope_type=AdminScopeType.CLASSROOM,
            school_id=school.id,
            scope_external_id="TURMA-X1",
        )
        await admin.link_user_to_school(
            performed_by_external_id="setup",
            external_user_id="student-in-scope",
            role=AdminRole.STUDENT,
            scope_type=AdminScopeType.CLASSROOM,
            school_id=school.id,
            scope_external_id="TURMA-X1",
        )
        await admin.link_user_to_school(
            performed_by_external_id="setup",
            external_user_id="student-out-of-scope",
            role=AdminRole.STUDENT,
            scope_type=AdminScopeType.CLASSROOM,
            school_id=school.id,
            scope_external_id="TURMA-X2-NAO-E-DO-COORD",
        )
        return school

    async def test_student_detail_denies_a_student_outside_the_coordinators_own_classroom(self):
        """The live-confirmed shape: same school, wrong classroom, narrowly-scoped coordinator."""
        async with self.session_factory() as session:
            school = await self._seed(session)
            school_id = school.id

        identity = ExternalIdentityContext(provider="test", external_user_id="coord-narrow")
        with self.assertRaises(HTTPException) as ctx:
            await get_student_detail_for_coordination(
                student_id="student-out-of-scope",
                school_id=school_id,
                identity=identity,
                session_factory=self.session_factory,
            )
        self.assertEqual(ctx.exception.status_code, 403)

    async def test_student_detail_allows_a_student_of_the_coordinators_own_classroom(self):
        """The fix must not deny what was always legitimate."""
        async with self.session_factory() as session:
            school = await self._seed(session)
            school_id = school.id

        identity = ExternalIdentityContext(provider="test", external_user_id="coord-narrow")
        result = await get_student_detail_for_coordination(
            student_id="student-in-scope",
            school_id=school_id,
            identity=identity,
            session_factory=self.session_factory,
        )
        self.assertEqual(result.student_id, "student-in-scope")

    async def test_search_never_returns_a_student_outside_the_coordinators_own_classroom(self):
        async with self.session_factory() as session:
            school = await self._seed(session)
            school_id = school.id

        identity = ExternalIdentityContext(provider="test", external_user_id="coord-narrow")
        results = await search_students_for_coordination(
            q="student",
            school_id=school_id,
            identity=identity,
            session_factory=self.session_factory,
        )
        ids = [r.student_id for r in results]
        self.assertIn("student-in-scope", ids)
        self.assertNotIn("student-out-of-scope", ids)

    async def test_a_school_wide_coordinator_is_unaffected(self):
        """The fix must narrow only the classroom-restricted case - a genuinely
        school-wide coordinator keeps seeing every student in their school."""
        async with self.session_factory() as session:
            school = School(id=uuid.uuid4(), code="SCH-Y", name="school-y")
            session.add(school)
            await session.commit()
            school_id = school.id

            admin = PlatformAdminService(session)
            await admin.link_user_to_school(
                performed_by_external_id="setup",
                external_user_id="coord-wide",
                role=AdminRole.COORDINATOR,
                scope_type=AdminScopeType.SCHOOL,
                school_id=school_id,
            )
            # No TeachingLesson exists yet for this school, so the is_global
            # branch of _resolve_scope_classrooms/get_teacher_authorized_classrooms
            # falls back to the same hardcoded placeholder classroom id both
            # already use elsewhere for a school with no lessons yet
            # (TeacherPortalService.get_teacher_authorized_classrooms,
            # CoordinationPortalService._resolve_scope_classrooms) - unrelated
            # to this fix, so the student is placed there to observe the fix
            # in isolation rather than that pre-existing fallback gap.
            await admin.link_user_to_school(
                performed_by_external_id="setup",
                external_user_id="student-anywhere",
                role=AdminRole.STUDENT,
                scope_type=AdminScopeType.CLASSROOM,
                school_id=school_id,
                scope_external_id="TURMA_3A",
            )

        identity = ExternalIdentityContext(provider="test", external_user_id="coord-wide")
        result = await get_student_detail_for_coordination(
            student_id="student-anywhere",
            school_id=school_id,
            identity=identity,
            session_factory=self.session_factory,
        )
        self.assertEqual(result.student_id, "student-anywhere")

        results = await search_students_for_coordination(
            q="student",
            school_id=school_id,
            identity=identity,
            session_factory=self.session_factory,
        )
        self.assertIn("student-anywhere", [r.student_id for r in results])


if __name__ == "__main__":
    unittest.main()
