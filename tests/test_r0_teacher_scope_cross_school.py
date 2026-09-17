# tests/test_r0_teacher_scope_cross_school.py
"""verify_teacher_classroom_scope never checked school_id for DIRECTOR/COORDINATOR
links, so a director of one school passed for a classroom of any other. The
sibling function two methods below, verify_coordinator_scope, already gets this
right - this fix makes verify_teacher_classroom_scope match it.

Two follow-ups from the whole-branch review live here too:

- The same unchecked pattern survived in TeacherPortalService.get_teacher_
  authorized_classrooms, which enumerated every classroom of the *requested*
  school for any DIRECTOR/COORDINATOR link, whatever school that link was in.
  It is reachable from get_teacher_dashboard (no classroom_id) and from
  verify_student_access, so the leak outlived the first fix.
- verify_teacher_classroom_scope briefly also accepted a DIRECTOR/COORDINATOR
  link whose scope_type was PLATFORM. link_user_to_school never validates
  scope_type against role, so such a link is creatable through the ordinary
  API and would have walked straight through the school check. The clause is
  gone; PLATFORM_ADMIN, checked separately and unconditionally, is untouched.
"""

import unittest
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import School
from agente_ia_edu.services.admin import AdminRole, AdminScopeType, PlatformAdminService
from agente_ia_edu.services.knowledge import KnowledgeService
from agente_ia_edu.services.recommendation import RecommendationEngine
from agente_ia_edu.services.teacher_portal import TeacherPortalService
from agente_ia_edu.services.teaching_context import ScopeAuthorizationError, TeachingContextService
from agente_ia_edu.services.video_engine import VideoRecommendationEngine


CLASSROOM_ONLY_IN_B = "TURMA-SECRETA-DE-B"


class TeacherScopeCrossSchoolTests(unittest.IsolatedAsyncioTestCase):
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

    async def _two_schools(self, session):
        school_a = School(id=uuid.uuid4(), code="SCH-A", name="school-a")
        school_b = School(id=uuid.uuid4(), code="SCH-B", name="school-b")
        session.add_all([school_a, school_b])
        await session.commit()
        return school_a, school_b

    async def test_a_director_of_one_school_is_denied_another_schools_classroom(self):
        """The reported shape exactly, run against the real service."""
        async with self.session_factory() as session:
            school_a, school_b = await self._two_schools(session)
            admin = PlatformAdminService(session)
            await admin.link_user_to_school(
                performed_by_external_id="setup",
                external_user_id="director-a",
                role=AdminRole.DIRECTOR,
                scope_type=AdminScopeType.SCHOOL,
                school_id=school_a.id,
            )

            svc = TeachingContextService(session)
            with self.assertRaises(ScopeAuthorizationError):
                await svc.verify_teacher_classroom_scope(
                    teacher_id="director-a",
                    school_id=school_b.id,
                    classroom_id="QUALQUER-TURMA-DE-B",
                )

    async def test_a_coordinator_of_one_school_is_denied_another_schools_classroom(self):
        """Same bug, same fix, the other of the two affected roles."""
        async with self.session_factory() as session:
            school_a, school_b = await self._two_schools(session)
            admin = PlatformAdminService(session)
            await admin.link_user_to_school(
                performed_by_external_id="setup",
                external_user_id="coord-a",
                role=AdminRole.COORDINATOR,
                scope_type=AdminScopeType.SCHOOL,
                school_id=school_a.id,
            )

            svc = TeachingContextService(session)
            with self.assertRaises(ScopeAuthorizationError):
                await svc.verify_teacher_classroom_scope(
                    teacher_id="coord-a",
                    school_id=school_b.id,
                    classroom_id="QUALQUER-TURMA-DE-B",
                )

    async def test_a_director_still_accesses_their_own_school(self):
        """The fix must not deny what was always legitimate - only what was
        never declared."""
        async with self.session_factory() as session:
            school_a, _ = await self._two_schools(session)
            admin = PlatformAdminService(session)
            await admin.link_user_to_school(
                performed_by_external_id="setup",
                external_user_id="director-own",
                role=AdminRole.DIRECTOR,
                scope_type=AdminScopeType.SCHOOL,
                school_id=school_a.id,
            )

            svc = TeachingContextService(session)
            allowed = await svc.verify_teacher_classroom_scope(
                teacher_id="director-own",
                school_id=school_a.id,
                classroom_id="QUALQUER-TURMA-DA-PROPRIA-ESCOLA",
            )
        self.assertTrue(allowed)

    async def test_a_platform_admin_is_unaffected(self):
        """PLATFORM_ADMIN is the one role that genuinely has no school - the
        only role link_user_to_school lets through without one."""
        async with self.session_factory() as session:
            _, school_b = await self._two_schools(session)
            admin = PlatformAdminService(session)
            await admin.link_user_to_school(
                performed_by_external_id="setup",
                external_user_id="platform-admin-1",
                role=AdminRole.PLATFORM_ADMIN,
                scope_type=AdminScopeType.PLATFORM,
            )

            svc = TeachingContextService(session)
            allowed = await svc.verify_teacher_classroom_scope(
                teacher_id="platform-admin-1",
                school_id=school_b.id,
                classroom_id="QUALQUER-TURMA",
            )
        self.assertTrue(allowed)

    async def test_a_teacher_in_their_own_classroom_is_unaffected(self):
        """The TEACHER branch below this fix already checked school_id and
        must keep behaving exactly as it did."""
        async with self.session_factory() as session:
            school_a, _ = await self._two_schools(session)
            admin = PlatformAdminService(session)
            await admin.link_user_to_school(
                performed_by_external_id="setup",
                external_user_id="teacher-a",
                role=AdminRole.TEACHER,
                scope_type=AdminScopeType.CLASSROOM,
                school_id=school_a.id,
                scope_external_id="TURMA-A1",
            )

            svc = TeachingContextService(session)
            allowed = await svc.verify_teacher_classroom_scope(
                teacher_id="teacher-a", school_id=school_a.id, classroom_id="TURMA-A1"
            )
        self.assertTrue(allowed)

        async with self.session_factory() as session:
            svc = TeachingContextService(session)
            with self.assertRaises(ScopeAuthorizationError):
                await svc.verify_teacher_classroom_scope(
                    teacher_id="teacher-not-linked-at-all",
                    school_id=uuid.uuid4(),
                    classroom_id="QUALQUER",
                )

    async def test_a_director_with_platform_scope_type_is_still_denied_another_school(self):
        """link_user_to_school never validates scope_type against role, so a
        DIRECTOR link carrying scope_type=PLATFORM is creatable through the
        ordinary API. It must not be a passport between schools: only
        PLATFORM_ADMIN is global."""
        async with self.session_factory() as session:
            school_a, school_b = await self._two_schools(session)
            admin = PlatformAdminService(session)
            link = await admin.link_user_to_school(
                performed_by_external_id="setup",
                external_user_id="director-platform-scope",
                role=AdminRole.DIRECTOR,
                scope_type=AdminScopeType.PLATFORM,
                school_id=school_a.id,
            )
            # Guard the premise: the escalation vector is genuinely creatable.
            self.assertEqual(link.role, AdminRole.DIRECTOR)
            self.assertEqual(link.scope_type, AdminScopeType.PLATFORM)

            svc = TeachingContextService(session)
            with self.assertRaises(ScopeAuthorizationError):
                await svc.verify_teacher_classroom_scope(
                    teacher_id="director-platform-scope",
                    school_id=school_b.id,
                    classroom_id="QUALQUER-TURMA-DE-B",
                )

    async def test_a_student_link_never_authorizes_teacher_scoped_actions(self):
        """A STUDENT link must be an explicit deny, not an accidental
        fallthrough - even one that matches school_id and classroom_id
        exactly must not authorize teacher-scoped actions."""
        async with self.session_factory() as session:
            school_a, _ = await self._two_schools(session)
            admin = PlatformAdminService(session)
            await admin.link_user_to_school(
                performed_by_external_id="setup",
                external_user_id="student-a",
                role=AdminRole.STUDENT,
                scope_type=AdminScopeType.CLASSROOM,
                school_id=school_a.id,
                scope_external_id="TURMA-A1",
            )

            svc = TeachingContextService(session)
            with self.assertRaises(ScopeAuthorizationError):
                await svc.verify_teacher_classroom_scope(
                    teacher_id="student-a",
                    school_id=school_a.id,
                    classroom_id="TURMA-A1",
                )


class AuthorizedClassroomsCrossSchoolTests(unittest.IsolatedAsyncioTestCase):
    """TeacherPortalService.get_teacher_authorized_classrooms carried the same
    unchecked role test as verify_teacher_classroom_scope, one layer up: the
    school_id it queried was the *requested* one, never the link's own. It
    feeds get_teacher_dashboard (the branch without classroom_id) and
    verify_student_access, so a director of school A could enumerate school B.
    """

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

    def _portal(self, session):
        knowledge = KnowledgeService(session)
        teaching_context = TeachingContextService(session)
        return TeacherPortalService(
            session,
            knowledge,
            teaching_context,
            RecommendationEngine(session, knowledge),
            VideoRecommendationEngine(session, knowledge),
        )

    async def _two_schools_with_a_classroom_only_in_b(self, session):
        """School B holds one classroom nobody outside B ever declared."""
        school_a = School(id=uuid.uuid4(), code="SCH-A", name="school-a")
        school_b = School(id=uuid.uuid4(), code="SCH-B", name="school-b")
        session.add_all([school_a, school_b])
        await session.commit()

        admin = PlatformAdminService(session)
        await admin.link_user_to_school(
            performed_by_external_id="setup",
            external_user_id="teacher-b",
            role=AdminRole.TEACHER,
            scope_type=AdminScopeType.CLASSROOM,
            school_id=school_b.id,
            scope_external_id=CLASSROOM_ONLY_IN_B,
        )
        return school_a, school_b

    async def test_a_director_of_one_school_enumerates_nothing_of_another(self):
        """The live probe from the review, turned into a regression test."""
        async with self.session_factory() as session:
            school_a, school_b = await self._two_schools_with_a_classroom_only_in_b(session)
            admin = PlatformAdminService(session)
            await admin.link_user_to_school(
                performed_by_external_id="setup",
                external_user_id="director-a",
                role=AdminRole.DIRECTOR,
                scope_type=AdminScopeType.SCHOOL,
                school_id=school_a.id,
            )

            classrooms = await self._portal(session).get_teacher_authorized_classrooms(
                "director-a", school_b.id
            )
        self.assertEqual(classrooms, [])

    async def test_a_coordinator_of_one_school_enumerates_nothing_of_another(self):
        """The other of the two roles that shared the branch."""
        async with self.session_factory() as session:
            school_a, school_b = await self._two_schools_with_a_classroom_only_in_b(session)
            admin = PlatformAdminService(session)
            await admin.link_user_to_school(
                performed_by_external_id="setup",
                external_user_id="coord-a",
                role=AdminRole.COORDINATOR,
                scope_type=AdminScopeType.SCHOOL,
                school_id=school_a.id,
            )

            classrooms = await self._portal(session).get_teacher_authorized_classrooms(
                "coord-a", school_b.id
            )
        self.assertEqual(classrooms, [])

    async def test_a_director_asking_for_their_own_school_still_gets_a_result(self):
        """A legitimately scoped director must keep the behaviour they had -
        the school-wide query and its TURMA_3A scaffolding fallback - and must
        still see nothing belonging to the other school."""
        async with self.session_factory() as session:
            school_a, _ = await self._two_schools_with_a_classroom_only_in_b(session)
            admin = PlatformAdminService(session)
            await admin.link_user_to_school(
                performed_by_external_id="setup",
                external_user_id="director-own",
                role=AdminRole.DIRECTOR,
                scope_type=AdminScopeType.SCHOOL,
                school_id=school_a.id,
            )

            classrooms = await self._portal(session).get_teacher_authorized_classrooms(
                "director-own", school_a.id
            )
        self.assertTrue(classrooms, "an authorized director must not be emptied by the fix")
        self.assertNotIn(CLASSROOM_ONLY_IN_B, classrooms)

    async def test_a_platform_admin_still_enumerates_any_school(self):
        """PLATFORM_ADMIN keeps the unconditional branch - the fix narrows the
        two school-bound roles only."""
        async with self.session_factory() as session:
            _, school_b = await self._two_schools_with_a_classroom_only_in_b(session)
            admin = PlatformAdminService(session)
            await admin.link_user_to_school(
                performed_by_external_id="setup",
                external_user_id="platform-admin-1",
                role=AdminRole.PLATFORM_ADMIN,
                scope_type=AdminScopeType.PLATFORM,
            )

            classrooms = await self._portal(session).get_teacher_authorized_classrooms(
                "platform-admin-1", school_b.id
            )
        self.assertIn(CLASSROOM_ONLY_IN_B, classrooms)


if __name__ == "__main__":
    unittest.main()
