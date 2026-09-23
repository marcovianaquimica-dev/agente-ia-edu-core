"""N+1 query regression tests for coordination_portal.py / teacher_portal.py.

These dashboard/roster aggregators used to iterate over a list of
classrooms/students/teachers and run one (or several) extra DB queries
PER item inside the loop, instead of a single batched query. That was
confirmed for real against a running Postgres instance (see the
session's investigation notes) before being fixed; these tests pin the
fix down with a real, in-process query counter (a SQLAlchemy
`before_cursor_execute` listener - the same technique used live) so a
future regression that reintroduces a per-item query shows up as a
failing assertion here, not just as slower dashboards in production.

The signature of a real N+1 fix is that the query count for the SAME
operation stays constant (or grows only with the number of distinct
CONTENT NODES touched, never with the number of students/classrooms/
teachers) as the collection being iterated grows. That is exactly what
each test below asserts: build a "small" collection and a "large" one
and require the query count NOT to grow with collection size.
"""

import unittest
from uuid import uuid4

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import CatalogNode, StudentContentMastery
from agente_ia_edu.services.admin import AdminRole, AdminScopeType, PlatformAdminService
from agente_ia_edu.services.coordination_portal import CoordinationPortalService
from agente_ia_edu.services.knowledge import KnowledgeService
from agente_ia_edu.services.recommendation import RecommendationEngine
from agente_ia_edu.services.teacher_portal import TeacherPortalService
from agente_ia_edu.services.teaching_context import TeachingContextService
from agente_ia_edu.services.video_engine import VideoRecommendationEngine


class QueryCounter:
    """Counts SQL statements executed against `engine` for the duration of
    a `with` block, via SQLAlchemy's `before_cursor_execute` event - the
    same instrumentation technique used to measure query counts live
    against the real dev Postgres, reused here so the (fast, disposable)
    sqlite-backed unit tests can assert exact counts."""

    def __init__(self, engine):
        self.engine = engine
        self.count = 0
        self._listener = None

    def __enter__(self):
        def _count(conn, cursor, statement, parameters, context, executemany):
            self.count += 1

        self._listener = _count
        event.listen(self.engine.sync_engine, "before_cursor_execute", self._listener)
        return self

    def __exit__(self, *exc):
        event.remove(self.engine.sync_engine, "before_cursor_execute", self._listener)


class TestPortalN1Queries(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            echo=False,
            poolclass=StaticPool,
        )
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        self.session_factory = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

    async def asyncTearDown(self):
        await self.engine.dispose()

    def _make_services(self, session):
        ks = KnowledgeService(session)
        t_svc = TeachingContextService(session)
        rec_eng = RecommendationEngine(session, ks)
        vid_eng = VideoRecommendationEngine(session, ks)
        portal_svc = TeacherPortalService(session, ks, t_svc, rec_eng, vid_eng)
        coord_svc = CoordinationPortalService(session, ks, t_svc, portal_svc, rec_eng)
        return portal_svc, coord_svc

    async def _seed_school_with_catalog(self, session):
        admin_service = PlatformAdminService(session)
        school = await admin_service.create_school(
            performed_by_external_id="admin:master",
            code=f"SCH_{uuid4().hex[:8]}",
            name="Escola N1",
        )
        root = CatalogNode(node_type="DISCIPLINE", name="Química", position=1, active=True)
        session.add(root)
        await session.flush()
        root.root_id = root.id
        suffix = uuid4().hex[:8]
        c1 = CatalogNode(parent_id=root.id, root_id=root.id, node_type="CONTENT", code=f"C1_{suffix}", name="Conteudo 1", position=1, active=True)
        c2 = CatalogNode(parent_id=root.id, root_id=root.id, node_type="CONTENT", code=f"C2_{suffix}", name="Conteudo 2", position=2, active=True)
        session.add_all([c1, c2])
        await session.flush()
        return admin_service, school, [c1.id, c2.id]

    async def _seed_classroom_students(self, session, admin_service, school_id, classroom_id, n_students, content_ids):
        for i in range(n_students):
            sid = f"student:{classroom_id}_{i}"
            await admin_service.link_user_to_school(
                performed_by_external_id="admin:master",
                external_user_id=sid,
                role=AdminRole.STUDENT,
                scope_type=AdminScopeType.CLASSROOM,
                school_id=school_id,
                scope_external_id=classroom_id,
            )
            for cid in content_ids:
                session.add(StudentContentMastery(
                    external_identity_id=sid,
                    content_node_id=cid,
                    mastery_score=55.0,
                    questions_answered=4,
                    questions_correct=2,
                ))
        await session.flush()

    # -------------------------------------------------------------------
    # TeacherPortalService.build_classroom_items
    # -------------------------------------------------------------------

    async def test_build_classroom_items_query_count_independent_of_student_count(self):
        """build_classroom_items used to run _fetch_students_in_classrooms +
        _fetch_masteries_for_students per classroom_id, plus one
        session.get(CatalogNode, ...) per distinct content a classroom's
        students had a mastery row for. Post-fix it is 4 batched queries
        (roster, masteries, content names, class_id resolution) no matter
        how many students are in the classroom."""
        async with self.session_factory() as session:
            admin_service, school, content_ids = await self._seed_school_with_catalog(session)
            await self._seed_classroom_students(session, admin_service, school.id, "TURMA_SMALL", 3, content_ids)
            await session.commit()
            portal_svc, _ = self._make_services(session)

            with QueryCounter(self.engine) as qc:
                items_small = await portal_svc.build_classroom_items(["TURMA_SMALL"], school.id, "2026")
            small_count = qc.count

        async with self.session_factory() as session:
            admin_service, school, content_ids = await self._seed_school_with_catalog(session)
            await self._seed_classroom_students(session, admin_service, school.id, "TURMA_LARGE", 15, content_ids)
            await session.commit()
            portal_svc, _ = self._make_services(session)

            with QueryCounter(self.engine) as qc:
                items_large = await portal_svc.build_classroom_items(["TURMA_LARGE"], school.id, "2026")
            large_count = qc.count

        self.assertEqual(items_small[0]["student_count"], 3)
        self.assertEqual(items_large[0]["student_count"], 15)
        self.assertEqual(small_count, 4)
        self.assertEqual(
            small_count, large_count,
            f"query count must not grow with student count (3 students: {small_count}, 15 students: {large_count})",
        )

    async def test_build_classroom_items_query_count_independent_of_classroom_count(self):
        """Same call, but scaling the number of CLASSROOMS passed in instead
        of the number of students per classroom - also must stay at 4
        queries total, not one roster/mastery pair per classroom_id."""
        async with self.session_factory() as session:
            admin_service, school, content_ids = await self._seed_school_with_catalog(session)
            await self._seed_classroom_students(session, admin_service, school.id, "TURMA_1", 2, content_ids)
            await session.commit()
            portal_svc, _ = self._make_services(session)

            with QueryCounter(self.engine) as qc:
                await portal_svc.build_classroom_items(["TURMA_1"], school.id, "2026")
            one_classroom_count = qc.count

        async with self.session_factory() as session:
            admin_service, school, content_ids = await self._seed_school_with_catalog(session)
            classroom_ids = []
            for i in range(6):
                cls_id = f"TURMA_{i}"
                classroom_ids.append(cls_id)
                await self._seed_classroom_students(session, admin_service, school.id, cls_id, 2, content_ids)
            await session.commit()
            portal_svc, _ = self._make_services(session)

            with QueryCounter(self.engine) as qc:
                items = await portal_svc.build_classroom_items(classroom_ids, school.id, "2026")
            six_classrooms_count = qc.count

        self.assertEqual(len(items), 6)
        self.assertEqual(one_classroom_count, 4)
        self.assertEqual(
            one_classroom_count, six_classrooms_count,
            f"query count must not grow with classroom count (1: {one_classroom_count}, 6: {six_classrooms_count})",
        )

    # -------------------------------------------------------------------
    # TeacherPortalService.get_classroom_detail
    # -------------------------------------------------------------------

    async def test_get_classroom_detail_query_count_independent_of_student_count(self):
        """get_classroom_detail used to fetch each student's masteries with
        its own query inside `for sid in student_ids`, and re-fetch every
        student's masteries again inside the recent_contents_taught loop.
        Post-fix, masteries are fetched once and reused; the total query
        count must not grow with the roster size."""
        async with self.session_factory() as session:
            admin_service, school, content_ids = await self._seed_school_with_catalog(session)
            await self._seed_classroom_students(session, admin_service, school.id, "TURMA_SMALL", 3, content_ids)
            await admin_service.link_user_to_school(
                performed_by_external_id="admin:master",
                external_user_id="user:prof_n1",
                role=AdminRole.TEACHER,
                scope_type=AdminScopeType.CLASSROOM,
                school_id=school.id,
                scope_external_id="TURMA_SMALL",
            )
            await session.commit()
            portal_svc, _ = self._make_services(session)

            with QueryCounter(self.engine) as qc:
                detail_small = await portal_svc.get_classroom_detail(
                    teacher_id="user:prof_n1", school_id=school.id, classroom_id="TURMA_SMALL", academic_year="2026",
                )
            small_count = qc.count

        async with self.session_factory() as session:
            admin_service, school, content_ids = await self._seed_school_with_catalog(session)
            await self._seed_classroom_students(session, admin_service, school.id, "TURMA_LARGE", 15, content_ids)
            await admin_service.link_user_to_school(
                performed_by_external_id="admin:master",
                external_user_id="user:prof_n1",
                role=AdminRole.TEACHER,
                scope_type=AdminScopeType.CLASSROOM,
                school_id=school.id,
                scope_external_id="TURMA_LARGE",
            )
            await session.commit()
            portal_svc, _ = self._make_services(session)

            with QueryCounter(self.engine) as qc:
                detail_large = await portal_svc.get_classroom_detail(
                    teacher_id="user:prof_n1", school_id=school.id, classroom_id="TURMA_LARGE", academic_year="2026",
                )
            large_count = qc.count

        self.assertEqual(len(detail_small["students"]), 3)
        self.assertEqual(len(detail_large["students"]), 15)
        self.assertEqual(
            small_count, large_count,
            f"query count must not grow with student count (3 students: {small_count}, 15 students: {large_count})",
        )

    # -------------------------------------------------------------------
    # TeacherPortalService.search_students_in_scope
    # -------------------------------------------------------------------

    async def test_search_students_in_scope_query_count_independent_of_match_count(self):
        """search_students_in_scope used to fetch masteries per matched
        student inside `for sid in matched_students`. Post-fix it's one
        batched query regardless of how many students match."""
        async with self.session_factory() as session:
            admin_service, school, content_ids = await self._seed_school_with_catalog(session)
            await self._seed_classroom_students(session, admin_service, school.id, "TURMA_SMALL", 3, content_ids)
            await admin_service.link_user_to_school(
                performed_by_external_id="admin:master",
                external_user_id="user:prof_n1",
                role=AdminRole.TEACHER,
                scope_type=AdminScopeType.CLASSROOM,
                school_id=school.id,
                scope_external_id="TURMA_SMALL",
            )
            await session.commit()
            portal_svc, _ = self._make_services(session)

            with QueryCounter(self.engine) as qc:
                results_small = await portal_svc.search_students_in_scope(
                    teacher_id="user:prof_n1", school_id=school.id, query="student",
                )
            small_count = qc.count

        async with self.session_factory() as session:
            admin_service, school, content_ids = await self._seed_school_with_catalog(session)
            await self._seed_classroom_students(session, admin_service, school.id, "TURMA_LARGE", 15, content_ids)
            await admin_service.link_user_to_school(
                performed_by_external_id="admin:master",
                external_user_id="user:prof_n1",
                role=AdminRole.TEACHER,
                scope_type=AdminScopeType.CLASSROOM,
                school_id=school.id,
                scope_external_id="TURMA_LARGE",
            )
            await session.commit()
            portal_svc, _ = self._make_services(session)

            with QueryCounter(self.engine) as qc:
                results_large = await portal_svc.search_students_in_scope(
                    teacher_id="user:prof_n1", school_id=school.id, query="student",
                )
            large_count = qc.count

        self.assertEqual(len(results_small), 3)
        self.assertEqual(len(results_large), 15)
        self.assertEqual(
            small_count, large_count,
            f"query count must not grow with matched-student count (3: {small_count}, 15: {large_count})",
        )

    # -------------------------------------------------------------------
    # CoordinationPortalService.list_coordination_teachers
    # -------------------------------------------------------------------

    async def _seed_teacher(self, session, admin_service, school_id, teacher_id, classroom_id):
        await admin_service.link_user_to_school(
            performed_by_external_id="admin:master",
            external_user_id=teacher_id,
            role=AdminRole.TEACHER,
            scope_type=AdminScopeType.CLASSROOM,
            school_id=school_id,
            scope_external_id=classroom_id,
        )

    async def _seed_coordinator(self, session, admin_service, school_id, coordinator_id):
        await admin_service.link_user_to_school(
            performed_by_external_id="admin:master",
            external_user_id=coordinator_id,
            role=AdminRole.COORDINATOR,
            scope_type=AdminScopeType.SCHOOL,
            school_id=school_id,
        )

    async def _seed_classroom_scoped_coordinator(self, session, admin_service, school_id, coordinator_id, classroom_ids):
        """A CLASSROOM-scoped (not SCHOOL-wide) coordinator, with one binding
        per classroom_id. Used instead of a SCHOOL-scope coordinator for
        compare_classrooms: a SCHOOL-scope (is_global) coordinator's
        classroom list comes from TeachingLesson rows (falling back to the
        hardcoded ["TURMA_3A", "TURMA_3B"] placeholder when none exist),
        which these N+1 tests don't seed - a CLASSROOM-scoped coordinator's
        list instead comes directly from their own UserSchoolLink rows, so
        it exactly matches the classroom_ids these tests create."""
        for cls_id in classroom_ids:
            await admin_service.link_user_to_school(
                performed_by_external_id="admin:master",
                external_user_id=coordinator_id,
                role=AdminRole.COORDINATOR,
                scope_type=AdminScopeType.CLASSROOM,
                school_id=school_id,
                scope_external_id=cls_id,
            )

    async def test_list_coordination_teachers_marginal_cost_per_teacher_is_bounded(self):
        """list_coordination_teachers used to run
        get_teacher_authorized_classrooms + _resolve_school_wide +
        _fetch_students_in_classrooms + _fetch_masteries_for_students (each
        its own round trip) PER teacher, on top of list_teacher_lessons -
        confirmed live against Postgres to cost ~8 queries per teacher
        (16 queries for 1 teacher, 96 for 11). Post-fix, links/roster/
        masteries are each fetched once for ALL teachers combined; only
        get_teacher_authorized_classrooms's own per-teacher authorization
        resolution and list_teacher_lessons (owned by TeachingContextService,
        outside this file's scope) still run once per teacher, so the
        marginal cost per extra teacher must be small and bounded - not the
        ~8 queries/teacher measured before the fix."""
        async with self.session_factory() as session:
            admin_service, school, content_ids = await self._seed_school_with_catalog(session)
            await self._seed_coordinator(session, admin_service, school.id, "user:coord_n1")
            await self._seed_teacher(session, admin_service, school.id, "user:prof_0", "TURMA_0")
            await self._seed_classroom_students(session, admin_service, school.id, "TURMA_0", 3, content_ids)
            await session.commit()
            _, coord_svc = self._make_services(session)

            with QueryCounter(self.engine) as qc:
                teachers_one = await coord_svc.list_coordination_teachers(
                    coordinator_id="user:coord_n1", school_id=school.id, academic_year="2026",
                )
            one_teacher_count = qc.count

        n_extra_teachers = 5
        async with self.session_factory() as session:
            admin_service, school, content_ids = await self._seed_school_with_catalog(session)
            await self._seed_coordinator(session, admin_service, school.id, "user:coord_n1")
            for i in range(1 + n_extra_teachers):
                cls_id = f"TURMA_{i}"
                await self._seed_teacher(session, admin_service, school.id, f"user:prof_{i}", cls_id)
                await self._seed_classroom_students(session, admin_service, school.id, cls_id, 3, content_ids)
            await session.commit()
            _, coord_svc = self._make_services(session)

            with QueryCounter(self.engine) as qc:
                teachers_many = await coord_svc.list_coordination_teachers(
                    coordinator_id="user:coord_n1", school_id=school.id, academic_year="2026",
                )
            many_teachers_count = qc.count

        self.assertEqual(len(teachers_one), 1)
        self.assertEqual(len(teachers_many), 1 + n_extra_teachers)

        marginal_per_teacher = (many_teachers_count - one_teacher_count) / n_extra_teachers
        self.assertLess(
            marginal_per_teacher, 5.0,
            f"marginal query cost per extra teacher should be well below the ~8/teacher "
            f"measured before the fix (1 teacher: {one_teacher_count} queries, "
            f"{1 + n_extra_teachers} teachers: {many_teachers_count} queries, "
            f"marginal: {marginal_per_teacher}/teacher)",
        )

    # -------------------------------------------------------------------
    # CoordinationPortalService.compare_classrooms
    # -------------------------------------------------------------------

    async def test_compare_classrooms_query_count_independent_of_classroom_count(self):
        """compare_classrooms used to run _fetch_students_in_classrooms +
        _fetch_masteries_for_students per classroom on top of
        build_classroom_items's own (now-fixed) cost. Post-fix, that extra
        pair is fetched once for every classroom combined."""
        async with self.session_factory() as session:
            admin_service, school, content_ids = await self._seed_school_with_catalog(session)
            await self._seed_classroom_scoped_coordinator(session, admin_service, school.id, "user:coord_n1", ["TURMA_0"])
            await self._seed_classroom_students(session, admin_service, school.id, "TURMA_0", 3, content_ids)
            await session.commit()
            _, coord_svc = self._make_services(session)

            with QueryCounter(self.engine) as qc:
                comparison_one = await coord_svc.compare_classrooms(
                    coordinator_id="user:coord_n1", school_id=school.id, academic_year="2026",
                )
            one_classroom_count = qc.count

        async with self.session_factory() as session:
            admin_service, school, content_ids = await self._seed_school_with_catalog(session)
            classroom_ids = [f"TURMA_{i}" for i in range(6)]
            await self._seed_classroom_scoped_coordinator(session, admin_service, school.id, "user:coord_n1", classroom_ids)
            for cls_id in classroom_ids:
                await self._seed_classroom_students(session, admin_service, school.id, cls_id, 3, content_ids)
            await session.commit()
            _, coord_svc = self._make_services(session)

            with QueryCounter(self.engine) as qc:
                comparison_many = await coord_svc.compare_classrooms(
                    coordinator_id="user:coord_n1", school_id=school.id, academic_year="2026",
                )
            six_classrooms_count = qc.count

        self.assertEqual(len(comparison_one), 1)
        self.assertEqual(len(comparison_many), 6)
        self.assertEqual(
            one_classroom_count, six_classrooms_count,
            f"query count must not grow with classroom count (1: {one_classroom_count}, 6: {six_classrooms_count})",
        )


if __name__ == "__main__":
    unittest.main()
