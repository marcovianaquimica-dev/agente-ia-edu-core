"""PHASE 24 study session perf audit - N+1 regression test for
StudySessionService.create_coordination_sessions().

create_coordination_sessions() fans a coordinator's single request out over
every student in a classroom. Confirmed LIVE against Postgres (in-process
query counter, same technique used here) that the previous implementation ran
several queries PER STUDENT that did not depend on anything student-specific:

  - a fresh AdaptiveLearningPathService() instance per student, which defeats
    that service's own per-instance prerequisite-graph memoization (the exact
    cache its manager_view() already relies on - see
    AdaptiveLearningPathService._prereq_graph docstring);
  - one _catalog_names() lookup per student for the SAME target_content_codes;
  - one MaterialAvailabilityService.resolve_for_content() call per student for
    the SAME codes + the SAME school_id when the coordinator pinned explicit
    target_content_codes;
  - one "does this student already have an active SCHOOL session today" query
    per student instead of one batched .in_() query for the whole target.

Live measurement (real Postgres, port 5433, temporary school/classroom/
students, cleaned up afterwards): 4 -> 27 students grew the query count by
~15 queries/student before the fix, ~8/student after (the remainder is
legitimate per-student data - each student's own domain map/mastery/activity
history - inside AdaptiveLearningPathService / CurriculumDomainMapService,
which are out of this file's scope).

This test pins the FIXED-input-shape down with a fast, in-process,
disposable sqlite counter: when the coordinator supplies explicit
target_content_codes, the query count for the four items above must stay
flat as the student count grows.
"""

from __future__ import annotations

import unittest
import uuid as _uuid
from datetime import datetime, timezone

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models.admin import School, UserSchoolLink
from agente_ia_edu.db.models.assessments import DomainContentMastery
from agente_ia_edu.db.models.catalog import CatalogNode
from agente_ia_edu.services.study_session import StudySessionService

SCHOOL_ID = _uuid.uuid5(_uuid.NAMESPACE_DNS, "phase24-n1-school")
COORD_ID = "n1_coord"
CONTENT_CODE = "N1_CONTENT"


class QueryCounter:
    """Same technique as tests/test_portal_n1_queries.py: count SQL
    statements executed against `engine` for the duration of a `with` block
    via SQLAlchemy's `before_cursor_execute`."""

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


class StudySessionCoordinationN1Tests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", echo=False, poolclass=StaticPool)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.factory = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)

        async with self.factory() as session:
            session.add(School(id=SCHOOL_ID, code="N1SCH", name="Escola N1", status="ACTIVE"))
            session.add(CatalogNode(code=CONTENT_CODE, name="Conteúdo N1",
                                    node_type="CONTENT", position=1, active=True))
            session.add(UserSchoolLink(external_user_id=COORD_ID, school_id=SCHOOL_ID,
                                       role="COORDINATOR", scope_type="SCHOOL",
                                       scope_external_id=str(SCHOOL_ID), active=True))
            await session.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _seed_classroom(self, classroom: str, n: int) -> None:
        # Each student gets a pre-existing DomainContentMastery row so
        # AdaptiveLearningPathService.build_path()'s domain-map read
        # (CurriculumDomainMapService.get_map) takes its plain read path
        # instead of the first-time rebuild_student() write path - that write
        # path commits per student (a genuine, student-specific,
        # out-of-this-file's-scope cost unrelated to the N+1 this test
        # targets) and would otherwise dominate the query count.
        async with self.factory() as session:
            for i in range(n):
                sid = f"stu_{classroom}_{i}"
                session.add(UserSchoolLink(
                    external_user_id=sid, school_id=SCHOOL_ID,
                    role="STUDENT", scope_type="CLASSROOM", scope_external_id=classroom,
                    active=True))
                session.add(DomainContentMastery(
                    student_external_id=sid, taxonomy_version="curriculum-v2",
                    content_code=CONTENT_CODE, subcontent_code=None,
                    questions_seen=4, questions_answered=4, questions_correct=2,
                    questions_incorrect=2, accuracy=0.5, evidence_count=4,
                    evidence_state="OBSERVED", definitive_evidence_count=4,
                    provisional_evidence_count=0, forced_closure_evidence_count=0,
                    visual_dependency_evidence_count=0,
                    origin_breakdown={"OFFICIAL_ACTIVITY": 4},
                    last_evaluated_at=datetime.now(timezone.utc)))
            await session.commit()

    async def _create_sessions(self, classroom: str, n_students: int) -> int:
        async with self.factory() as session:
            svc = StudySessionService(session)
            with QueryCounter(self.engine) as counter:
                result = await svc.create_coordination_sessions(
                    COORD_ID, school_id=str(SCHOOL_ID), target_type="CLASSROOM",
                    target_id=classroom, session_date="2026-09-21",
                    start_at="2026-09-21T08:00:00Z", end_at="2026-09-21T09:00:00Z",
                    target_content_codes=[CONTENT_CODE],
                )
            self.assertEqual(result["created_or_updated"], n_students, result)
            return counter.count

    async def test_create_coordination_sessions_query_count_does_not_scale_with_class_size(self):
        await self._seed_classroom("small", 4)
        q_small = await self._create_sessions("small", 4)

        await self._seed_classroom("big", 27)
        q_big = await self._create_sessions("big", 27)

        # N+1 signature: query count grows ~linearly with student count.
        #
        # Growth is NOT expected to be perfectly flat: AdaptiveLearningPathService
        # / CurriculumDomainMapService (a different service, out of this file's
        # scope) still run a few genuinely per-student queries inside
        # build_path() - each student's own domain-map row, school context and
        # (unrelated to this fix) an unbatched prerequisite lookup of their own.
        # What this test pins down is the four lookups create_coordination_sessions
        # itself used to run once PER STUDENT for no reason (fresh
        # AdaptiveLearningPathService instance defeating its own prereq-graph/
        # catalog cache, _catalog_names, the explicit material-availability call,
        # and the "already active today" check) - measured with this exact
        # scenario (sqlite, identical evidence per student, 4 vs 27 students):
        #   pre-fix:  growth = 368 queries (16.0 / extra student)
        #   post-fix: growth = 184 queries ( 8.0 / extra student)
        # 250 sits comfortably above the fixed number and well below a
        # reintroduced N+1 (which pushes growth back above ~300).
        growth = q_big - q_small
        self.assertLess(
            growth, 250,
            f"query count grew by {growth} going from 4 to 27 students "
            f"({q_small} -> {q_big}) - looks like an N+1 in "
            f"create_coordination_sessions()",
        )


if __name__ == "__main__":
    unittest.main()
